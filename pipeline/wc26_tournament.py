"""WC2026 tournament engine — ensemble edition (needs .venv numpy).

  .venv/bin/python3 wc26_tournament.py            # futures + locked bracket
  .venv/bin/python3 wc26_tournament.py --force    # re-lock predictions file

Upgrades over the v1 approximation:
  - 200-model Bayesian-bootstrap ensemble (exponential match re-weighting)
    so simulations carry parameter uncertainty, not one point estimate.
  - Official FIFA bracket: real R32 template (matches 73-88), third-place
    slot group constraints, real R16/QF/SF/final flow (89-104).
  - 100,000 tournament simulations (numpy-precomputed score CDFs).
  - Deterministic "most likely" bracket locked to wc26_predictions.json
    for later accuracy grading (never overwritten without --force).

Host advantage in knockouts: USA all rounds, Mexico/Canada through R16.
Knockout draw probability split proportionally (extra-time/pens approx).
"""
import json
import math
import os
import random
import sys

import numpy as np

from wc26_simulate import (ROOT, DATA, TODAY, CITY_COUNTRY, params, load_matches,
                           blend, now_utc, save_versioned)

N_BOOT = 200
N_SIMS = 100_000
MAX_G = 12
rng = np.random.default_rng(2026)
random.seed(2026)

# --- anomaly model (assumed magnitudes, zero-mean; see model notes) ---
FORM_SD = 0.06        # per-tournament team form shock on log attack/defence
ATTRITION_P = 0.10    # chance a KO match leaves lasting damage (cards/knocks)
ATTRITION_HIT = 0.04  # log-attack penalty per accumulated knock
ET_FATIGUE = 0.03     # next-match log-attack penalty after a 120-minute tie

P = params()
HOSTS = {"United States", "Mexico", "Canada"}

# ---- official bracket (Wikipedia: 2026 FIFA World Cup knockout stage) ----
# R32 ties: (match_no, side1, side2); W=group winner, R=runner-up,
# T=third place drawn from the allowed groups.
R32 = [
    (73, ("R", "A"), ("R", "B")),
    (74, ("W", "E"), ("T", "ABCDF")),
    (75, ("W", "F"), ("R", "C")),
    (76, ("W", "C"), ("R", "F")),
    (77, ("W", "I"), ("T", "CDFGH")),
    (78, ("R", "E"), ("R", "I")),
    (79, ("W", "A"), ("T", "CEFHI")),
    (80, ("W", "L"), ("T", "EHIJK")),
    (81, ("W", "D"), ("T", "BEFIJ")),
    (82, ("W", "G"), ("T", "AEHIJ")),
    (83, ("R", "K"), ("R", "L")),
    (84, ("W", "H"), ("R", "J")),
    (85, ("W", "B"), ("T", "EFGIJ")),
    (86, ("W", "J"), ("R", "H")),
    (87, ("W", "K"), ("T", "DEIJL")),
    (88, ("R", "D"), ("R", "G")),
]
R16 = [(89, 74, 77), (90, 73, 75), (91, 76, 78), (92, 79, 80),
       (93, 83, 84), (94, 81, 82), (95, 86, 88), (96, 85, 87)]
QF = [(97, 89, 90), (98, 93, 94), (99, 91, 92), (100, 95, 96)]
SF = [(101, 97, 98), (102, 99, 100)]
FINAL = (104, 101, 102)
THIRD_SLOTS = [(no, set(allowed)) for no, (k1, _), (k2, allowed) in
               [(no, s1, s2) for no, s1, s2 in R32] if k2 == "T"]


# ---------------- numpy model fitting ----------------
def build_arrays(matches):
    teams = sorted({m["home"] for m in matches} | {m["away"] for m in matches})
    idx = {t: i for i, t in enumerate(teams)}
    hi = np.array([idx[m["home"]] for m in matches])
    ai = np.array([idx[m["away"]] for m in matches])
    hg = np.array([m["hg"] for m in matches], float)
    ag = np.array([m["ag"] for m in matches], float)
    w = np.array([m["w"] for m in matches])
    nf = np.array([0.0 if m["neutral"] else 1.0 for m in matches])
    return teams, idx, hi, ai, hg, ag, w, nf


def np_fit(arrs, shrink, w_mult=None, iters=80):
    teams, idx, hi, ai, hg, ag, w, nf = arrs
    w = w * w_mult if w_mult is not None else w
    n = len(teams)
    att = np.zeros(n)
    dfn = np.zeros(n)
    mu, hadv = np.log(1.25), 0.25
    for _ in range(iters):
        lh = np.exp(mu + hadv * nf + att[hi] + dfn[ai])
        la = np.exp(mu + att[ai] + dfn[hi])
        mu += np.log((w * (hg + ag)).sum() / (w * (lh + la)).sum())
        lh = np.exp(mu + hadv * nf + att[hi] + dfn[ai])
        hadv += np.log((w * nf * hg).sum() / (w * nf * lh).sum())
        pr = shrink * np.exp(mu)
        eh = np.exp(mu + hadv * nf + dfn[ai])   # home exp goals sans att
        ea = np.exp(mu + dfn[hi])
        num_a = pr + np.bincount(hi, w * hg, n) + np.bincount(ai, w * ag, n)
        den_a = pr + np.bincount(hi, w * eh, n) + np.bincount(ai, w * ea, n)
        att = np.log(num_a / den_a)
        gh = np.exp(mu + hadv * nf + att[hi])   # vs home attack, sans def
        ga_ = np.exp(mu + att[ai])
        num_d = pr + np.bincount(ai, w * hg, n) + np.bincount(hi, w * ag, n)
        den_d = pr + np.bincount(ai, w * gh, n) + np.bincount(hi, w * ga_, n)
        dfn = np.log(num_d / den_d)
        mu += att.mean() + dfn.mean()
        att -= att.mean()
        dfn -= dfn.mean()
    # squad-value prior (same as wc26_simulate.apply_value_prior, vectorised)
    from wc26_simulate import VALUE_BETA, _value_z
    loaded = _value_z()
    if loaded and VALUE_BETA:
        vals, default = loaded
        lv = np.array([np.log(vals.get(t, default)) for t in teams])
        z = (lv - lv.mean()) / lv.std()
        att = att + VALUE_BETA * z / 2
        dfn = dfn - VALUE_BETA * z / 2
    return att, dfn, mu, hadv


def grid_np(l1, l2, rho, boost=1.0):
    k = np.arange(MAX_G + 1)
    from math import lgamma
    lg = np.array([lgamma(i + 1) for i in range(MAX_G + 1)])
    ph = np.exp(-l1 + k * np.log(l1) - lg)
    pa = np.exp(-l2 + k * np.log(l2) - lg)
    g = np.outer(ph, pa)
    g[0, 0] *= 1 - l1 * l2 * rho
    g[1, 0] *= 1 + l2 * rho
    g[0, 1] *= 1 + l1 * rho
    g[1, 1] *= 1 - rho
    if boost != 1.0:
        g[2:, 2:] *= boost
    return g / g.sum()


# ---------------- setup ----------------
print("fitting ensemble...", flush=True)
raw = load_matches(TODAY.isoformat(), P["half_life"], P["friendly_w"],
                   P.get("margin_cap", 99))
ARRS = build_arrays(raw)
TEAMS_ALL, IDX = ARRS[0], ARRS[1]
MEAN = np_fit(ARRS, P["shrink"])
BOOT = [np_fit(ARRS, P["shrink"], rng.exponential(1.0, len(raw)), iters=60)
        for _ in range(N_BOOT)]
print(f"ensemble of {N_BOOT} models fitted", flush=True)

FIXTURES = json.load(
    open(f"{DATA}/fifa_world_cup_2026_group_matches.json"))["matches"]
GROUPS = sorted({m["group"] for m in FIXTURES})
GROUP_TEAMS = {g: set() for g in GROUPS}
for m in FIXTURES:
    GROUP_TEAMS[m["group"]] |= {m["home"], m["away"]}


def fixture_lams(model, home, away, city):
    att, dfn, mu, hadv = model
    venue = CITY_COUNTRY.get(city, "United States")
    h1 = hadv if home == venue else 0.0
    h2 = hadv if away == venue else 0.0
    l1 = np.exp(mu + h1 + att[IDX[home]] + dfn[IDX[away]])
    l2 = np.exp(mu + h2 + att[IDX[away]] + dfn[IDX[home]])
    return float(l1), float(l2)


def ko_lams(model, a, b, round_no):
    att, dfn, mu, hadv = model
    ha = hadv if (a in HOSTS and (a == "United States" or round_no <= 2)) else 0.0
    hb = hadv if (b in HOSTS and (b == "United States" or round_no <= 2)) else 0.0
    l1 = np.exp(mu + ha + att[IDX[a]] + dfn[IDX[b]])
    l2 = np.exp(mu + hb + att[IDX[b]] + dfn[IDX[a]])
    return float(l1), float(l2)


def hda(g):
    return (float(np.tril(g, -1).sum()), float(np.trace(g)),
            float(np.triu(g, 1).sum()))


_ko_cache = {}


def ko_win(model, a, b, round_no, _b=None):
    """P(a beats b) — cached per bootstrap model index when given."""
    key = (a, b, round_no <= 2, _b)
    if key not in _ko_cache:
        pH, pD, pA = hda(grid_np(*ko_lams(model, a, b, round_no), P["rho"],
                                 P["min2_boost"]))
        _ko_cache[key] = pH + pD * pH / (pH + pA)
    return _ko_cache[key]




# precompute group score CDFs for every bootstrap model: (B, 72, 169)
print("precomputing fixture rates...", flush=True)
ALL_TEAMS = sorted({t for g in GROUPS for t in GROUP_TEAMS[g]})
T_IDX = {t: i for i, t in enumerate(ALL_TEAMS)}
# per-bootstrap-model expected goals for the 72 group fixtures
L1G = np.empty((N_BOOT, len(FIXTURES)))
L2G = np.empty((N_BOOT, len(FIXTURES)))
for b, model in enumerate(BOOT):
    for f, m in enumerate(FIXTURES):
        L1G[b, f], L2G[b, f] = fixture_lams(model, m["home"], m["away"], m["city"])
FIX_INFO = [(m["home"], m["away"], m["group"]) for m in FIXTURES]
FIX_HI = np.array([T_IDX[m["home"]] for m in FIXTURES])
FIX_AI = np.array([T_IDX[m["away"]] for m in FIXTURES])
# Tournament sims sample plain Poisson scores (the Dixon-Coles low-score
# tweak shifts draw rates ~1% and is kept for match-card pricing only).


# ---------------- condition on reality ----------------
def load_actual_state():
    """Pin the simulation to what has actually happened: once the group stage
    is complete and the real R32 draw is known, group outcomes stop being
    re-simulated and decided knockout ties advance their real winner —
    without this, eliminated teams keep carrying title odds. Ratings stay
    frozen at the pre-tournament cutoff: this fixes WHO IS STILL IN and
    where, not form. Returns None whenever the state can't be pinned
    (pre-knockouts, missing data), which keeps the original full simulation."""
    if any(not (str(m.get("status", "")).startswith("Match Finished")
                and m.get("score")) for m in FIXTURES):
        return None
    try:
        ko = json.load(open(f"{DATA}/wc26_knockout_matches.json"))["matches"]
    except FileNotFoundError:
        return None
    r32_fx = [m for m in ko if m.get("round") == "Round of 32"]
    if len(r32_fx) != 16:
        return None
    # actual standings -> group winner / runner-up (pts, gd, gf), and the
    # goals every team has already scored (they seed the Boot tallies)
    pts, gd, gf, goals_played = {}, {}, {}, {}
    for m in FIXTURES:
        hs, as_ = (int(x) for x in m["score"].split("-"))
        for t, sf_, sa in ((m["home"], hs, as_), (m["away"], as_, hs)):
            pts[t] = pts.get(t, 0) + (3 if sf_ > sa else 1 if sf_ == sa else 0)
            gd[t] = gd.get(t, 0) + sf_ - sa
            gf[t] = gf.get(t, 0) + sf_
            goals_played[t] = goals_played.get(t, 0) + sf_
    win, run = {}, {}
    for g in GROUPS:
        order = sorted(GROUP_TEAMS[g],
                       key=lambda t: (pts[t], gd[t], gf[t]), reverse=True)
        win[g], run[g] = order[0], order[1]
    # pin each real R32 fixture to its FIFA match number: side1 of every
    # template tie is a specific group's winner/runner-up, which identifies
    # the fixture uniquely (side2 resolves the third-place draw for free)
    team_fx = {}
    for m in r32_fx:
        team_fx[m["home"]] = m
        team_fx[m["away"]] = m
    teams_in = {}
    for no, (k1, v1), _s2 in R32:
        t1 = win[v1] if k1 == "W" else run[v1]
        fx = team_fx.get(t1)
        if not fx:
            return None
        teams_in[no] = (fx["home"], fx["away"])
    if len({frozenset(p) for p in teams_in.values()}) != 16:
        return None
    # decided knockout ties (any round): real winner by team pair; a level
    # 90' score is settled by the shoot-out and flags the 120-minute leg
    decided = {}
    for m in ko:
        if not (str(m.get("status", "")).startswith("Match Finished")
                and m.get("score")):
            continue
        hs, as_ = (int(x) for x in m["score"].split("-"))
        w = m["home"] if hs > as_ else m["away"] if as_ > hs else None
        pens = m.get("penalties")
        if w is None and isinstance(pens, str) and "-" in pens:
            ph, pa = (int(x) for x in pens.split("-"))
            w = m["home"] if ph > pa else m["away"]
        if not w:
            continue
        decided[frozenset((m["home"], m["away"]))] = (w, hs == as_)
        goals_played[m["home"]] = goals_played.get(m["home"], 0) + hs
        goals_played[m["away"]] = goals_played.get(m["away"], 0) + as_
    return {"win": win, "teams_in": teams_in, "decided": decided,
            "goals_played": goals_played}


ACTUAL = load_actual_state()
if ACTUAL:
    print(f"conditioning on reality: group stage fixed, "
          f"{len(ACTUAL['decided'])} knockout tie(s) decided", flush=True)


def allocate_thirds(thirds, slots=THIRD_SLOTS):
    """Backtracking: assign 8 (team, group) thirds to slots w/ group constraints."""
    assign = {}

    def rec(i):
        if i == len(slots):
            return True
        no, allowed = slots[i]
        for t, g in thirds:
            if g in allowed and t not in assign.values():
                assign[no] = t
                if rec(i + 1):
                    return True
                del assign[no]
        return False

    return assign if rec(0) else None


def sim_tournament(b):
    """One full tournament with bootstrap model b.
    Returns (group winners, reached stages, tournament goals per team)."""
    model = BOOT[b]
    goals = {}
    # per-tournament form shock: injuries, chemistry, camp chaos (zero-mean)
    shock = rng.normal(0.0, FORM_SD, len(ALL_TEAMS))
    if ACTUAL:
        # group stage happened: fixed outcomes, real R32 draw, actual goals
        win = ACTUAL["win"]
        teams_in = ACTUAL["teams_in"]
        goals.update(ACTUAL["goals_played"])
    else:
        pts, gd, gf = {}, {}, {}
        tilt = np.exp(shock[FIX_HI] - shock[FIX_AI])
        hg_s = rng.poisson(L1G[b] * tilt)
        ag_s = rng.poisson(L2G[b] / tilt)
        for f, (home, away, grp) in enumerate(FIX_INFO):
            i, j = int(hg_s[f]), int(ag_s[f])
            for t, sf_, sa in ((home, i, j), (away, j, i)):
                pts[t] = pts.get(t, 0) + (3 if sf_ > sa else 1 if sf_ == sa else 0)
                gd[t] = gd.get(t, 0) + sf_ - sa
                gf[t] = gf.get(t, 0) + sf_
                goals[t] = goals.get(t, 0) + sf_
        win, run, thirds = {}, {}, []
        for g in GROUPS:
            order = sorted(GROUP_TEAMS[g],
                           key=lambda t: (pts[t], gd[t], gf[t], random.random()),
                           reverse=True)
            win[g], run[g] = order[0], order[1]
            thirds.append((order[2], g))
        thirds.sort(key=lambda tg: (pts[tg[0]], gd[tg[0]], gf[tg[0]],
                                    random.random()), reverse=True)
        best8 = thirds[:8]
        alloc = allocate_thirds(best8)
        if alloc is None:                  # rare: constraints unsatisfiable
            alloc = {no: best8[i][0] for i, (no, _) in enumerate(THIRD_SLOTS)}

        teams_in = {}                      # match_no -> (a, b)
        for no, s1, s2 in R32:
            def side(s, no=no):
                k, v = s
                return win[v] if k == "W" else run[v] if k == "R" else alloc[no]
            teams_in[no] = (side(s1), side(s2))

    handicap = {}   # accumulated attrition + fatigue per team (log-attack)

    def play_ko(a, bb, rnd):
        """Sample a knockout score with form shock + carryover anomalies;
        draws settled by relative strength (ET/pens approximation)."""
        l1, l2 = ko_lams(model, a, bb, rnd)
        ia, ib = T_IDX[a], T_IDX[bb]
        l1 *= math.exp(shock[ia] - shock[ib] - handicap.get(a, 0.0))
        l2 *= math.exp(shock[ib] - shock[ia] - handicap.get(bb, 0.0))
        i = rng.poisson(l1)
        j = rng.poisson(l2)
        goals[a] = goals.get(a, 0) + int(i)
        goals[bb] = goals.get(bb, 0) + int(j)
        went_distance = i == j
        for t in (a, bb):
            if random.random() < ATTRITION_P:
                handicap[t] = handicap.get(t, 0.0) + ATTRITION_HIT
            if went_distance:
                handicap[t] = handicap.get(t, 0.0) + ET_FATIGUE
        if i > j:
            return a
        if j > i:
            return bb
        return a if random.random() < l1 / (l1 + l2) else bb

    def advance(a, bb, rnd):
        """Real winner when the tie has been played; simulate otherwise.
        Played ties still feed the anomaly carryover — knocks and a
        120-minute leg burden the FUTURE matches we do simulate."""
        if ACTUAL:
            hit = ACTUAL["decided"].get(frozenset((a, bb)))
            if hit:
                w, went_distance = hit
                for t in (a, bb):
                    if random.random() < ATTRITION_P:
                        handicap[t] = handicap.get(t, 0.0) + ATTRITION_HIT
                    if went_distance:
                        handicap[t] = handicap.get(t, 0.0) + ET_FATIGUE
                return w
        return play_ko(a, bb, rnd)

    winners = {}
    reached = {"r32": set(), "r16": set(), "qf": set(), "sf": set(),
               "final": set(), "champion": set()}
    for no, (a, bb) in teams_in.items():
        reached["r32"] |= {a, bb}
        winners[no] = advance(a, bb, 1)
    for rnd, stage, pairs in ((2, "r16", R16), (3, "qf", QF), (4, "sf", SF)):
        for no, m1, m2 in pairs:
            a, bb = winners[m1], winners[m2]
            reached[stage] |= {a, bb}
            winners[no] = advance(a, bb, rnd)
    no, m1, m2 = FINAL
    a, bb = winners[m1], winners[m2]
    reached["final"] |= {a, bb}
    champ = advance(a, bb, 5)
    reached["champion"] = {champ}
    return win, reached, goals


def run_futures():
    stages = ["win_group", "r32", "r16", "qf", "sf", "final", "champion"]
    all_teams = sorted({t for g in GROUPS for t in GROUP_TEAMS[g]})
    t_idx = {t: i for i, t in enumerate(all_teams)}
    goals_mat = np.zeros((N_SIMS, len(all_teams)), dtype=np.int16)
    count = {t: dict.fromkeys(stages, 0) for t in all_teams}
    for s in range(N_SIMS):
        b = s % N_BOOT
        win, reached, goals = sim_tournament(b)
        for g in GROUPS:
            count[win[g]]["win_group"] += 1
        for stage, ts in reached.items():
            for t in ts:
                count[t][stage] += 1
        for t, g in goals.items():
            goals_mat[s, t_idx[t]] = g
        if s % 20000 == 19999:
            print(f"  {s + 1}/{N_SIMS} sims", flush=True)
    np.savez_compressed(f"{DATA}/wc26_team_goals.npz",
                        goals=goals_mat, teams=np.array(all_teams))
    print("saved per-sim team goal tallies -> wc26_team_goals.npz", flush=True)
    teams = sorted(count, key=lambda t: -count[t]["champion"])
    out = {
        "method": f"{N_SIMS} Monte Carlo tournaments over a {N_BOOT}-model "
                  f"bootstrap ensemble (Dixon-Coles, params {P}); official "
                  "FIFA bracket incl. third-place slot constraints; anomaly "
                  f"model: form shock sd={FORM_SD}, KO attrition "
                  f"p={ATTRITION_P} hit={ATTRITION_HIT}, ET fatigue {ET_FATIGUE}"
                  + (f"; conditioned on actual results (group stage fixed, "
                     f"{len(ACTUAL['decided'])} knockout ties decided)"
                     if ACTUAL else ""),
        "conditioned_on_results": bool(ACTUAL),
        "ko_ties_decided": len(ACTUAL["decided"]) if ACTUAL else 0,
        "generated": now_utc(),
        "teams": {t: {s: round(count[t][s] / N_SIMS, 4) for s in stages}
                  for t in teams},
    }
    json.dump(out, open(f"{DATA}/wc26_tournament.json", "w"), indent=2,
              ensure_ascii=False)
    save_versioned(f"{DATA}/wc26_tournament.json")
    print("favourites:", ", ".join(
        f"{t} {out['teams'][t]['champion']:.1%}" for t in teams[:8]), flush=True)


# ---------------- deterministic locked bracket ----------------
def predict_bracket():
    # group matches: model probs blended with Polymarket where priced;
    # picks and expected points come from the blend (market sees lineups).
    try:
        MKT = json.load(open(f"{DATA}/wc26_market_prices.json"))["prices"]
    except FileNotFoundError:
        MKT = {}
    group_preds = []
    xpts = {}
    xgd = {}
    for m in FIXTURES:
        l1, l2 = fixture_lams(MEAN, m["home"], m["away"], m["city"])
        g = grid_np(l1, l2, P["rho"], P["min2_boost"])
        pH, pD, pA = hda(g)
        p_model = {"H": pH, "D": pD, "A": pA}
        mkt = MKT.get(str(m["match_id"]), {}).get("moneyline")
        if mkt:
            p_use = blend(p_model, {"H": mkt["home"], "D": mkt["draw"],
                                    "A": mkt["away"]})
        else:
            p_use = p_model
        i, j = np.unravel_index(int(g.argmax()), g.shape)
        res = max(p_use, key=p_use.get)
        # store the over/under book at lock time too, so a future clean
        # pre-tournament lock can grade calibrated totals the way 1X2 is
        # graded (the grid is already here — it's nearly free)
        tot = float(g.sum())
        ou_model, ou, ou_market = {}, {}, {}
        mtot = MKT.get(str(m["match_id"]), {}).get("totals") or {}
        for L in (1.5, 2.5, 3.5):
            om = sum(float(g[a][b]) for a in range(g.shape[0])
                     for b in range(g.shape[1]) if a + b > L) / tot
            ou_model[f"over_{L}"] = round(om, 4)
            mk = mtot.get(f"over_{L}")
            live = mk is not None and 0.02 < mk < 0.98
            if live:
                ou_market[f"over_{L}"] = round(mk, 4)
            ou[f"over_{L}"] = round(
                blend({"O": om, "U": 1 - om},
                      {"O": mk, "U": 1 - mk})["O"] if live else om, 4)
        group_preds.append({
            "match_id": m["match_id"], "group": m["group"],
            "date_utc": m["date_utc"], "home": m["home"], "away": m["away"],
            "pred_score": f"{i}-{j}", "pred_result": res,
            "p": {k: round(v, 4) for k, v in p_use.items()},
            "p_model": {k: round(v, 4) for k, v in p_model.items()},
            "p_market": ({"H": mkt["home"], "D": mkt["draw"], "A": mkt["away"]}
                         if mkt else None),
            "totals_model": ou_model, "totals": ou,
            "totals_market": ou_market or None,
        })
        pH, pD, pA = p_use["H"], p_use["D"], p_use["A"]
        for t, ex, c in ((m["home"], 3 * pH + pD, l1 - l2),
                         (m["away"], 3 * pA + pD, l2 - l1)):
            xpts[t] = xpts.get(t, 0) + ex
            xgd[t] = xgd.get(t, 0) + c
    tables = {}
    win, run, thirds = {}, {}, []
    for g in GROUPS:
        order = sorted(GROUP_TEAMS[g], key=lambda t: (xpts[t], xgd[t]),
                       reverse=True)
        tables[g] = [{"team": t, "xpts": round(xpts[t], 2),
                      "xgd": round(xgd[t], 2)} for t in order]
        win[g], run[g] = order[0], order[1]
        thirds.append((order[2], g))
    thirds.sort(key=lambda tg: (xpts[tg[0]], xgd[tg[0]]), reverse=True)
    alloc = allocate_thirds(thirds[:8])

    ko = []
    winners = {}

    def play(no, a, b, rnd, label):
        p = ko_win(MEAN, a, b, rnd)
        w = a if p >= 0.5 else b
        winners[no] = w
        ko.append({"match": no, "round": label, "team1": a, "team2": b,
                   "pick": w, "p_pick": round(p if w == a else 1 - p, 4)})

    for no, s1, s2 in R32:
        def side(s, no=no):
            k, v = s
            return win[v] if k == "W" else run[v] if k == "R" else alloc[no]
        play(no, side(s1), side(s2), 1, "Round of 32")
    for no, m1, m2 in R16:
        play(no, winners[m1], winners[m2], 2, "Round of 16")
    for no, m1, m2 in QF:
        play(no, winners[m1], winners[m2], 3, "Quarter-final")
    for no, m1, m2 in SF:
        play(no, winners[m1], winners[m2], 4, "Semi-final")
    sf_losers = [t for no, m1, m2 in SF
                 for t in (winners[m1], winners[m2]) if t != winners[no]]
    play(103, sf_losers[0], sf_losers[1], 5, "Third place")
    no, m1, m2 = FINAL
    play(104, winners[m1], winners[m2], 5, "Final")

    return {
        "locked_at": now_utc(),
        "method": "Mean Dixon-Coles model (tuned params, margin-capped), "
                  "Polymarket-blended group picks (w=0.35), official FIFA bracket",
        "champion": winners[104],
        "group_matches": group_preds,
        "group_tables": tables,
        "knockout": ko,
        "predicted_stage_teams": {
            "r16": sorted({winners[no] for no, _, _ in R32}),
            "qf": sorted({winners[no] for no, _, _ in R16}),
            "sf": sorted({winners[no] for no, _, _ in QF}),
            "final": sorted({winners[no] for no, _, _ in SF}),
        },
        "actuals": {},
        "accuracy": None,
    }


if __name__ == "__main__":
    pred_path = f"{DATA}/wc26_predictions.json"
    if os.path.exists(pred_path) and "--force" not in sys.argv:
        print("predictions already locked (use --force to overwrite)")
    else:
        json.dump(predict_bracket(), open(pred_path, "w"), indent=2,
                  ensure_ascii=False)
        save_versioned(pred_path)
        print(f"locked predicted bracket -> wc26_predictions.json")
    run_futures()
