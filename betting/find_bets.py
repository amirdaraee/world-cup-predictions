"""Build a bet plan from model edges vs live Polymarket prices.

  python3 betting/find_bets.py        ->  betting/state/plan.json

Read-only: needs no keys. Pulls fresh prices + CLOB token ids from the
free Gamma API, compares against the RAW model (never the blend — the
blend already contains the market), sizes stakes with fractional Kelly
under the caps in betting/config.json.

Only positive-edge buys are planned (either side of a binary). Golden
Ball / Glove are never bet (no calibrated model). Exact-score, totals,
BTTS and spread scanning are gated off by default in config include —
opt in deliberately. Started matches are never scanned: our probabilities
are pre-match and in-play prices know the current score.
Review the plan before running place_bets.py.
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
sys.path.insert(0, os.path.join(ROOT, "pipeline"))
from wc26_polymarket import (ALIASES, names_for, parse_score_question,
                             classify_more_market, FUTURES_SLUGS)  # noqa: E402

def merge_local(cfg, local):
    """Overlay config.local.json on the committed config. `include` is
    deep-merged: a local file that flips two categories on must not
    silently wipe the gates it doesn't mention."""
    include = {**cfg.get("include", {}), **(local.pop("include", None) or {})}
    cfg = {**cfg, **local}
    cfg["include"] = include
    return cfg


CFG = json.load(open(f"{HERE}/config.json"))
if os.path.exists(f"{HERE}/config.local.json"):   # gitignored personal caps
    CFG = merge_local(CFG, json.load(open(f"{HERE}/config.local.json")))
AWARD_SLUGS = {
    "golden_boot": "world-cup-golden-boot-winner",
    "top_scorer_nation": "world-cup-top-scorer-nation",
}


def gamma(path, **q):
    url = f"https://gamma-api.polymarket.com{path}?" + urllib.parse.urlencode(q)
    req = urllib.request.Request(url, headers={"User-Agent": "wc26-research"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def norm(s):
    import unicodedata
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def kelly_stake(q, p, bankroll, fraction=None):
    """Quarter(-ish) Kelly stake for buying YES at price p with belief q."""
    f_star = (q - p) / (1 - p)
    if fraction is None:
        fraction = CFG["kelly_fraction"]
    return bankroll * fraction * f_star


def tradeable(mk):
    """Reject placeholder/illiquid books: an untraded line sits at ~50/50
    with no depth, and its 'price' is fiction — a fat fake edge."""
    liq = float(mk.get("liquidityNum") or 0)
    spread = float(mk.get("spread") or 1)
    return (liq >= CFG.get("min_liquidity_usdc", 1000) and
            spread <= CFG.get("max_book_spread", 0.06))


def kickoff_times():
    fx = json.load(
        open(f"{DATA}/fifa_world_cup_2026_group_matches.json"))["matches"]
    try:
        fx += json.load(open(f"{DATA}/wc26_knockout_matches.json"))["matches"]
    except FileNotFoundError:
        pass
    return {str(m["match_id"]): m["date_utc"] for m in fx}


def started(mid, times):
    ko = times.get(mid)
    return ko and datetime.now(timezone.utc) >= datetime.fromisoformat(ko)


def match_candidates():
    sims = json.load(open(f"{DATA}/wc26_simulations.json"))["simulations"]
    snap = json.load(open(f"{DATA}/wc26_market_prices.json"))["prices"]
    times = kickoff_times()
    out = []
    for mid, rec in snap.items():
        sim = sims.get(mid)
        if not sim or started(mid, times):
            continue
        try:
            evs = gamma("/events", slug=rec["slug"])
        except Exception as e:
            print(f"  fetch failed {rec['slug']}: {e}")
            continue
        if not evs:
            continue
        for mk in evs[0]["markets"]:
            ql = mk["question"].lower()
            if not tradeable(mk):
                continue
            side = ("draw" if "draw" in ql else
                    "home" if any(f"will {n} win" in ql for n in names_for(sim["home"])) else
                    "away" if any(f"will {n} win" in ql for n in names_for(sim["away"])) else None)
            if not side:
                continue
            try:
                price = float(json.loads(mk["outcomePrices"])[0])
                token = json.loads(mk["clobTokenIds"])[0]   # YES token
            except (KeyError, ValueError, IndexError):
                continue
            q = sim["moneyline"][side]   # RAW model, not blend
            if q - price >= CFG["min_edge_match"]:
                label = {"home": sim["home"], "away": sim["away"], "draw": "Draw"}[side]
                out.append({
                    "category": "moneyline", "match_id": mid,
                    "bet": f"{sim['home']} v {sim['away']}: {label} (YES)",
                    "question": mk["question"], "token_id": token,
                    "model_p": round(q, 4), "market_p": price,
                    "neg_risk": bool(mk.get("negRisk")),
                    "edge": round(q - price, 4),
                    # for the knockout draw caution in build_plan: a
                    # 90-minute moneyline never gets paid for extra time
                    "ko": bool(sim.get("round")),
                    "p_draw": round(sim["moneyline"]["draw"], 4),
                })
        time.sleep(0.12)
    return out


def exact_score_candidates():
    sims = json.load(open(f"{DATA}/wc26_simulations.json"))["simulations"]
    snap = json.load(open(f"{DATA}/wc26_market_prices.json"))["prices"]
    times = kickoff_times()
    out = []
    for mid, rec in snap.items():
        sim = sims.get(mid)
        slug = rec.get("exact_score_slug")
        if not slug or not sim or "exact_scores" not in sim or \
                started(mid, times):
            continue
        try:
            evs = gamma("/events", slug=slug)
        except Exception as e:
            print(f"  fetch failed {slug}: {e}")
            continue
        if not evs:
            continue
        for mk in evs[0]["markets"]:
            cell = parse_score_question(mk.get("question", ""),
                                        sim["home"], sim["away"])
            if not cell or cell == "other" or not tradeable(mk):
                continue
            try:
                price = float(json.loads(mk["outcomePrices"])[0])
                token = json.loads(mk["clobTokenIds"])[0]   # YES token
            except (KeyError, ValueError, IndexError):
                continue
            q = sim["exact_scores"].get(cell, 0.0)
            if q - price >= CFG["min_edge_score"]:
                out.append({
                    "category": "exact_score", "match_id": mid,
                    "bet": f"{sim['home']} v {sim['away']}: {cell} (YES)",
                    "question": mk["question"], "token_id": token,
                    "model_p": round(q, 4), "market_p": price,
                    "neg_risk": bool(mk.get("negRisk")),
                    "edge": round(q - price, 4),
                })
        time.sleep(0.12)
    return out


def more_markets_candidates():
    """Totals / BTTS / spread edges from the -more-markets books. Both
    sides of each binary are checked (an underpriced Under is a buy of
    outcome token 1)."""
    sims = json.load(open(f"{DATA}/wc26_simulations.json"))["simulations"]
    snap = json.load(open(f"{DATA}/wc26_market_prices.json"))["prices"]
    times = kickoff_times()
    include = CFG["include"]
    out = []
    for mid, rec in snap.items():
        sim = sims.get(mid)
        slug = rec.get("more_markets_slug")
        if not slug or not sim or started(mid, times):
            continue
        try:
            evs = gamma("/events", slug=slug)
        except Exception as e:
            print(f"  fetch failed {slug}: {e}")
            continue
        if not evs:
            continue
        for mk in evs[0]["markets"]:
            cls = classify_more_market(mk.get("question", ""),
                                       sim["home"], sim["away"])
            if not cls or not include.get(cls[0]) or not tradeable(mk):
                continue
            cat, key = cls
            if cat == "team_totals":
                side, line = key.split("_over_")
                model_yes = sim.get("team_totals", {}).get(side, {}) \
                    .get(f"over_{line}")
            else:
                model_yes = (sim["totals"].get(key) if cat == "totals" else
                             sim["btts"] if cat == "btts" else
                             sim["spread"].get(key))
            if model_yes is None:   # market line the model doesn't price
                continue
            try:
                prices = [float(p) for p in json.loads(mk["outcomePrices"])]
                tokens = json.loads(mk["clobTokenIds"])
                outcomes = json.loads(mk.get("outcomes") or '["Yes","No"]')
            except (KeyError, ValueError, IndexError):
                continue
            for idx, q in ((0, model_yes), (1, 1 - model_yes)):
                if idx >= min(len(prices), len(tokens), len(outcomes)):
                    continue
                if q - prices[idx] >= CFG["min_edge_match"]:
                    out.append({
                        "category": cat, "match_id": mid,
                        "bet": f"{sim['home']} v {sim['away']}: "
                               f"{mk['question'].split(': ')[-1]} "
                               f"— {outcomes[idx]}",
                        "question": mk["question"], "token_id": tokens[idx],
                        "model_p": round(q, 4), "market_p": prices[idx],
                        "neg_risk": bool(mk.get("negRisk")),
                        "edge": round(q - prices[idx], 4),
                    })
        time.sleep(0.12)
    return out


SIBLING_RESULT_CATS = ("halftime", "second_half", "first_to_score")


def sibling_result_candidates():
    """3-way books fetched per fixture: halftime result, second-half
    result, first to score. Both sides of each binary are checked."""
    sims = json.load(open(f"{DATA}/wc26_simulations.json"))["simulations"]
    snap = json.load(open(f"{DATA}/wc26_market_prices.json"))["prices"]
    times = kickoff_times()
    include = CFG["include"]
    cats = [c for c in SIBLING_RESULT_CATS if include.get(c)]
    out = []
    for mid, rec in snap.items():
        sim = sims.get(mid)
        if not sim or started(mid, times):
            continue
        for cat in cats:
            slug = rec.get(f"{cat}_slug")
            if not slug or cat not in sim:
                continue
            try:
                evs = gamma("/events", slug=slug)
            except Exception as e:
                print(f"  fetch failed {slug}: {e}")
                continue
            if not evs:
                continue
            for mk in evs[0]["markets"]:
                if not tradeable(mk):
                    continue
                ql = mk.get("question", "").lower()
                if cat == "first_to_score":
                    side = ("neither" if "neither" in ql else
                            "home" if any(n in ql.split("to score first")[0]
                                          for n in names_for(sim["home"])) else
                            "away" if any(n in ql.split("to score first")[0]
                                          for n in names_for(sim["away"])) else None)
                else:
                    side = ("draw" if "draw" in ql else
                            "home" if any(n in ql for n in names_for(sim["home"])) else
                            "away" if any(n in ql for n in names_for(sim["away"])) else None)
                if not side:
                    continue
                model_yes = sim[cat].get(side)
                if model_yes is None:
                    continue
                try:
                    prices = [float(p) for p in json.loads(mk["outcomePrices"])]
                    tokens = json.loads(mk["clobTokenIds"])
                except (KeyError, ValueError, IndexError):
                    continue
                for idx, q in ((0, model_yes), (1, 1 - model_yes)):
                    if idx >= min(len(prices), len(tokens)):
                        continue
                    if q - prices[idx] >= CFG["min_edge_match"]:
                        out.append({
                            "category": cat, "match_id": mid,
                            "bet": f"{sim['home']} v {sim['away']}: "
                                   f"{mk['question'].rstrip('?')} "
                                   f"— {'YES' if idx == 0 else 'NO'}",
                            "question": mk["question"],
                            "token_id": tokens[idx],
                            "model_p": round(q, 4), "market_p": prices[idx],
                        "neg_risk": bool(mk.get("negRisk")),
                            "edge": round(q - prices[idx], 4),
                        })
            time.sleep(0.12)
    return out


def corners_candidates():
    """Total-corner O/U vs the intercept-only NegBin base rate (245-match
    fit; the xG slope failed validation and is not used). Value exists
    only where a thin book strays far from the base rate — the liquidity
    filter does the real work here."""
    try:
        corners = json.load(open(f"{DATA}/wc26_corners.json"))["matches"]
    except FileNotFoundError:
        return []
    snap = json.load(open(f"{DATA}/wc26_market_prices.json"))["prices"]
    times = kickoff_times()
    out = []
    for mid, rec in snap.items():
        slug = rec.get("corners_slug")
        model = corners.get(mid)
        if not slug or not model or started(mid, times):
            continue
        try:
            evs = gamma("/events", slug=slug)
        except Exception as e:
            print(f"  fetch failed {slug}: {e}")
            continue
        if not evs:
            continue
        for mk in evs[0]["markets"]:
            m = re.search(r":\s*O/U (\d+\.5) Total Corners\??$",
                          mk.get("question", ""))
            if not m or not tradeable(mk):
                continue
            q_yes = model.get(f"over_{m.group(1)}")
            if q_yes is None:
                continue
            try:
                prices = [float(p) for p in json.loads(mk["outcomePrices"])]
                tokens = json.loads(mk["clobTokenIds"])
            except (KeyError, ValueError, IndexError):
                continue
            for idx, q in ((0, q_yes), (1, 1 - q_yes)):
                if idx >= min(len(prices), len(tokens)):
                    continue
                if q - prices[idx] >= CFG["min_edge_match"]:
                    out.append({
                        "category": "corners", "match_id": mid,
                        "bet": f"Corners O/U {m.group(1)} "
                               f"({'Over' if idx == 0 else 'Under'}) — "
                               f"{mk['question'].split(':')[0]}",
                        "question": mk["question"],
                        "token_id": tokens[idx],
                        "model_p": round(q, 4), "market_p": prices[idx],
                        "neg_risk": bool(mk.get("negRisk")),
                        "edge": round(q - prices[idx], 4),
                    })
        time.sleep(0.12)
    return out


def futures_candidates():
    """Stage-reach + group-winner futures vs the tournament ensemble."""
    tourn = json.load(open(f"{DATA}/wc26_tournament.json"))["teams"]

    def team_for(title):
        if title in tourn:
            return title
        tl = (title or "").lower()
        for team in tourn:
            if tl in names_for(team) or norm(title) == norm(team):
                return team
        return None

    slugs = [(stage, slug) for stage, slug in FUTURES_SLUGS.items()]
    slugs += [("win_group", f"world-cup-group-{g}-winner")
              for g in "abcdefghijkl"]
    out = []
    for stage, slug in slugs:
        try:
            evs = gamma("/events", slug=slug)
        except Exception as e:
            print(f"  fetch failed {slug}: {e}")
            continue
        for mk in (evs[0]["markets"] if evs else []):
            team = team_for(mk.get("groupItemTitle") or "")
            if not team or not tradeable(mk):
                continue
            q_yes = tourn[team].get(stage)
            if q_yes is None:
                continue
            try:
                prices = [float(p) for p in json.loads(mk["outcomePrices"])]
                tokens = json.loads(mk["clobTokenIds"])
            except (KeyError, ValueError, IndexError):
                continue
            for idx, q in ((0, q_yes), (1, 1 - q_yes)):
                if idx >= min(len(prices), len(tokens)):
                    continue
                if q - prices[idx] >= CFG["min_edge_match"]:
                    out.append({
                        "category": "futures", "match_id": f"future:{team}",
                        "bet": f"{team} {stage} — "
                               f"{'YES' if idx == 0 else 'NO'}",
                        "question": mk.get("question", ""),
                        "token_id": tokens[idx],
                        "model_p": round(q, 4), "market_p": prices[idx],
                        "neg_risk": bool(mk.get("negRisk")),
                        "edge": round(q - prices[idx], 4),
                    })
        time.sleep(0.15)
    return out


def award_candidates():
    awards = json.load(open(f"{DATA}/wc26_awards.json"))
    out = []
    model_by_cat = {
        "golden_boot": {norm(b["player"]): b["p_model"] for b in awards["golden_boot"]},
        "top_scorer_nation": {norm(n["team"]): n["p_model"] for n in awards["top_scorer_nation"]},
    }
    for cat, slug in AWARD_SLUGS.items():
        if not CFG["include"].get(cat):
            continue
        evs = gamma("/events", slug=slug)
        for mk in (evs[0]["markets"] if evs else []):
            title = mk.get("groupItemTitle") or ""
            if not title or title == "Other":
                continue
            try:
                price = float(json.loads(mk["outcomePrices"])[0])
                token = json.loads(mk["clobTokenIds"])[0]
            except (KeyError, ValueError, IndexError):
                continue
            # match market name to model name by surname
            last = norm(title).split()[-1]
            qs = [v for k, v in model_by_cat[cat].items()
                  if k.split()[-1] == last or k == norm(title)]
            if len(qs) != 1:
                continue
            q = qs[0]
            if q - price >= CFG["min_edge_award"]:
                out.append({
                    "category": cat, "bet": f"{title} (YES)",
                    "question": mk["question"], "token_id": token,
                    "model_p": round(q, 4), "market_p": price,
                    "neg_risk": bool(mk.get("negRisk")),
                    "edge": round(q - price, 4),
                })
        time.sleep(0.2)
    return out


def build_plan(cands, cfg):
    """Select and size candidates under the caps (pure: no I/O).

    Award bets that qualify always make the plan; per-match bets
    (moneyline/exact score) fill the remaining max_bets slots by edge.
    Returns (sized candidates, total stake)."""
    award_cats = ("golden_boot", "top_scorer_nation")
    # longshot floor: skip any outcome the model itself rates below
    # min_model_prob. Against a near-zero market price, a tiny model error
    # fabricates a fat "edge" — the thin-book futures (Senegal win-group at
    # ~2%) that bled. Awards are legitimately low-probability, so exempt them.
    floor = cfg.get("min_model_prob", 0.0)
    if floor:
        cands = [c for c in cands if c["category"] in award_cats
                 or c["model_p"] >= floor]
    cands = sorted(cands, key=lambda c: -c["edge"])
    # one moneyline bet per match: home/draw/away are mutually exclusive, so
    # betting two of them (the draw AND the underdog) is a correlated double-
    # stake. Keep only the best-edge side per fixture (cands is edge-sorted).
    if cfg.get("one_moneyline_per_match", True):
        seen, deduped = set(), []
        for c in cands:
            if c["category"] == "moneyline":
                if c.get("match_id") in seen:
                    continue
                seen.add(c.get("match_id"))
            deduped.append(c)
        cands = deduped
    # one goal-count bet per match: a match-total AND a team-total on the
    # same fixture are the same opinion staked twice — one scoreline settles
    # both. Keep only the best-edge expression per fixture (edge-sorted).
    if cfg.get("one_goal_market_per_match", True):
        seen, deduped = set(), []
        for c in cands:
            if c["category"] in ("totals", "team_totals"):
                if c.get("match_id") in seen:
                    continue
                seen.add(c.get("match_id"))
            deduped.append(c)
        cands = deduped
    max_bets = cfg.get("max_bets", 12)
    awards = [c for c in cands if c["category"] in award_cats]
    mlines = [c for c in cands if c["category"] not in award_cats]
    cands = awards + mlines[:max(max_bets - len(awards), 0)]
    cands.sort(key=lambda c: -c["edge"])

    bankroll = cfg["max_total_stake_usdc"]
    for c in cands:
        stake = min(kelly_stake(c["model_p"], c["market_p"], bankroll,
                                cfg.get("kelly_fraction")),
                    cfg["max_per_bet_usdc"])
        # floor qualifying bets at min_stake for breadth across categories
        c["stake_usdc"] = round(max(stake, cfg.get("min_stake_usdc", 1)), 2)
    # knockout draw caution (reduce-only, like apply_elo_caution): ~30% of
    # ties end level after 90, and a 90-minute moneyline is never paid for
    # "advances on penalties" — when the model itself rates the draw high,
    # much of the apparent edge is a coin-flip we can't collect on.
    if cfg.get("ko_draw_caution", True):
        thr = cfg.get("ko_draw_pp", 28) / 100.0
        factor = cfg.get("ko_draw_factor", 0.5)
        kept = []
        for c in cands:
            if c["category"] == "moneyline" and c.get("ko") and \
                    c.get("p_draw", 0) >= thr:
                c["stake_usdc"] = round(c["stake_usdc"] * factor, 2)
                c["ko_draw_flagged"] = True   # reduce-only: full-budget
                if c["stake_usdc"] < cfg.get("min_stake_usdc", 1):   # mode
                    continue                  # must not re-inflate this
            kept.append(c)
        cands = kept
    # per-fixture exposure cap: many markets, one opinion — five
    # correlated bets on the same match are one big bet in disguise
    cap = cfg.get("max_per_match_usdc")
    if cap:
        spent, kept = {}, []
        for c in cands:   # edge-descending: best expression of the
            mid = c.get("match_id") or c["bet"]   # opinion keeps its size
            room = cap - spent.get(mid, 0.0)
            if room < cfg.get("min_stake_usdc", 1):
                continue
            c["stake_usdc"] = round(min(c["stake_usdc"], room), 2)
            spent[mid] = spent.get(mid, 0.0) + c["stake_usdc"]
            kept.append(c)
        cands = kept
    # scale down if the plan exceeds the total cap
    planned = sum(c["stake_usdc"] for c in cands)
    if planned > bankroll:
        scale = bankroll / planned
        for c in cands:
            c["stake_usdc"] = round(c["stake_usdc"] * scale, 2)
        planned = sum(c["stake_usdc"] for c in cands)
    # full-budget mode (--full-budget): the remaining bankroll is a TARGET,
    # not just a ceiling. Kelly sizing above is per-edge and will happily
    # leave money on the table; when the user says "deploy $X", water-fill
    # the shortfall across the qualifying bets, proportional to edge, until
    # every cap binds. Reduce-only cautions stay reduced (ko_draw_flagged),
    # awards are exempt from the per-match cap (not one fixture — same rule
    # as select_todo in place_bets), and if the caps bind before the target
    # is reached we say so out loud instead of pretending.
    if cfg.get("deploy_full_budget") and cands:
        per_bet = cfg["max_per_bet_usdc"]
        spent_m = {}
        for c in cands:
            mid = c.get("match_id") or c["bet"]
            spent_m[mid] = spent_m.get(mid, 0.0) + c["stake_usdc"]
        for _ in range(50):
            shortfall = bankroll - sum(c["stake_usdc"] for c in cands)
            if shortfall < 0.01:
                break
            fillable = []
            for c in cands:
                if c.get("ko_draw_flagged"):
                    continue
                room = per_bet - c["stake_usdc"]
                if cap and c["category"] not in award_cats \
                        and c.get("match_id"):
                    room = min(room, cap - spent_m.get(c["match_id"], 0.0))
                if room > 0.01:
                    fillable.append((c, room))
            if not fillable:
                break
            wsum = sum(c["edge"] for c, _ in fillable) or 1.0
            moved = 0.0
            for c, _ in fillable:
                # recompute room LIVE: a same-match sibling may have taken
                # part of the match cap earlier in this very pass
                room = per_bet - c["stake_usdc"]
                if cap and c["category"] not in award_cats \
                        and c.get("match_id"):
                    room = min(room, cap - spent_m.get(c["match_id"], 0.0))
                add = round(min(room, shortfall * c["edge"] / wsum), 2)
                if add < 0.01:
                    continue
                c["stake_usdc"] = round(c["stake_usdc"] + add, 2)
                mid = c.get("match_id") or c["bet"]
                spent_m[mid] = spent_m.get(mid, 0.0) + add
                moved += add
            if moved < 0.01:
                break
        planned = sum(c["stake_usdc"] for c in cands)
        if planned < bankroll - 0.01:
            print(f"full-budget: caps bind at ${planned:.2f} of the "
                  f"${bankroll:.2f} target (per-bet ${per_bet}, per-match "
                  f"${cap}) — raise them in config.local.json to deploy more")
    return cands, round(planned, 2)


def apply_elo_caution(bets, elo_matches, cfg):
    """Second-opinion tripwire (reduce-only): where the Elo companion and
    the DC model disagree on a fixture's result probabilities by >=
    elo_caution_pp, structural model uncertainty is doing work — every
    sized bet on that fixture is scaled by elo_caution_factor. Like the
    news gate it can only shrink, never add or raise; bets scaled below
    $1 drop. No elo file or gate off => untouched."""
    if not elo_matches or not cfg.get("use_elo_caution", True):
        return bets, []
    thr = cfg.get("elo_caution_pp", 15) / 100.0
    factor = cfg.get("elo_caution_factor", 0.5)
    kept, flagged = [], []
    for b in bets:
        m = elo_matches.get(str(b.get("match_id") or ""))
        if m and m.get("disagreement", 0) >= thr:
            b = {**b, "stake_usdc": round(b["stake_usdc"] * factor, 2),
                 "elo_disagreement": m["disagreement"]}
            flagged.append(b["bet"])
            if b["stake_usdc"] < 1:
                continue
        kept.append(b)
    return kept, flagged


def record_paper(cands):
    """Paper-trading log: every candidate from every scan, regardless of
    what gets staked. betting/paper.py grades these later for CLV (did the
    price move toward our view before close?) and result PnL — the honest
    out-of-sample test of the edge-finding, with no money at risk."""
    path = f"{HERE}/state/paper.json"
    try:
        paper = json.load(open(path))
    except FileNotFoundError:
        paper = {"runs": []}
    paper["runs"].append({
        "at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        "candidates": [{k: c.get(k) for k in
                        ("category", "bet", "question", "token_id",
                         "match_id", "model_p", "market_p", "edge")}
                       for c in cands]})
    os.makedirs(f"{HERE}/state", exist_ok=True)
    json.dump(paper, open(path, "w"), indent=1, ensure_ascii=False)
    print(f"paper log: +{len(cands)} candidates "
          f"({sum(len(r['candidates']) for r in paper['runs'])} total)")


def main():
    if "--full-budget" in sys.argv:
        CFG["deploy_full_budget"] = True   # budget is a target, not a ceiling
    cands = []
    if CFG["include"].get("moneyline"):
        print("scanning match moneylines...", flush=True)
        cands += match_candidates()
    if CFG["include"].get("exact_score"):
        print("scanning exact-score books...", flush=True)
        cands += exact_score_candidates()
    if any(CFG["include"].get(c)
           for c in ("totals", "team_totals", "btts", "spread")):
        print("scanning totals/BTTS/spread books...", flush=True)
        cands += more_markets_candidates()
    if any(CFG["include"].get(c) for c in SIBLING_RESULT_CATS):
        print("scanning halftime/second-half/first-to-score books...",
              flush=True)
        cands += sibling_result_candidates()
    if CFG["include"].get("futures"):
        print("scanning futures books...", flush=True)
        cands += futures_candidates()
    if CFG["include"].get("corners"):
        print("scanning corner books...", flush=True)
        cands += corners_candidates()
    print("scanning award markets...", flush=True)
    cands += award_candidates()
    record_paper(cands)
    # size against what is LEFT of the bankroll, not the headline cap —
    # the ledger already holds real positions and place_bets will refuse
    # anything past the cap anyway; planning to the full cap just makes
    # a plan that mostly can't execute
    try:
        ledger = json.load(open(f"{HERE}/state/ledger.json"))
        spent = sum(b["stake_usdc"] for b in ledger["placed"])
    except FileNotFoundError:
        spent = 0.0
    remaining = max(CFG["max_total_stake_usdc"] - spent, 0.0)
    if spent:
        print(f"\nledger holds ${spent:.2f} of the "
              f"${CFG['max_total_stake_usdc']} cap — "
              f"sizing plan to the ${remaining:.2f} that remains")
    if remaining < CFG.get("min_stake_usdc", 1):
        print("CAP EXHAUSTED — no room for even a minimum stake; raise "
              "max_total_stake_usdc in config.local.json to bet more")
        cands = []
    cands, total = build_plan(cands, {**CFG,
                                      "max_total_stake_usdc": remaining})
    try:
        elo = json.load(open(f"{DATA}/wc26_elo.json"))["matches"]
    except FileNotFoundError:
        elo = None
    cands, elo_flagged = apply_elo_caution(cands, elo, CFG)
    if elo_flagged:
        total = round(sum(c["stake_usdc"] for c in cands), 2)
        print(f"elo second opinion disagrees on {len(elo_flagged)} bet(s) "
              f"— stakes scaled by {CFG.get('elo_caution_factor', 0.5)}:")
        for b in elo_flagged:
            print(f"  ~ {b}")

    plan = {
        "created": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        "config": CFG,
        "total_planned_usdc": total,
        "bets": cands,
    }
    os.makedirs(f"{HERE}/state", exist_ok=True)
    json.dump(plan, open(f"{HERE}/state/plan.json", "w"), indent=2, ensure_ascii=False)
    print(f"\n{len(cands)} candidate bets, ${total:.2f} planned "
          f"(caps: ${CFG['max_total_stake_usdc']} total / "
          f"${CFG['max_per_bet_usdc']} per bet)")
    for c in cands:
        print(f"  ${c['stake_usdc']:>6.2f}  {c['bet']:<46} "
              f"model {c['model_p']:.2f} vs mkt {c['market_p']:.2f} "
              f"(+{c['edge'] * 100:.1f}¢) [{c['category']}]")
    print(f"\nplan written to betting/state/plan.json — review it, then:\n"
          f"  .venv/bin/python3 betting/place_bets.py            (dry run)\n"
          f"  .venv/bin/python3 betting/place_bets.py --live     (real money)")


if __name__ == "__main__":
    main()
