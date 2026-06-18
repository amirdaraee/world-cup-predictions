"""Score the 24 played WC2026 matches with TOTALLY DIFFERENT method families
— not tweaks of Dixon-Coles. Every model is fit WC-blind (cutoff 2026-06-10;
the CSV ends before the tournament) and then scored on the played round.

Methods:
  freq    constant H/D/A = training base rates (skill floor, no team info)
  ologit  ordered logit on the Elo rating gap — models the 3-way outcome
          DIRECTLY, with NO Poisson goal grid (a different family entirely)
  elo     project Elo -> Poisson goals map (structurally independent rating
          model; wc26_elo.py)
  market  Polymarket at lock-time prices (clean, from wc26_predictions.json)
  dc      our shipped Dixon-Coles model (reference)

We compare on a common subset (matches every method can price) so the log-loss
numbers are apples-to-apples; the market only covers the priced matches.
24 matches is noise — read ranks, not decimals.
"""
import json
import math
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pipeline"))
import wc26_simulate as S          # noqa: E402
import wc26_elo as E               # noqa: E402

DATA = S.DATA
CUTOFF = "2026-06-10"              # < first WC kickoff: every fit is WC-blind
RES_IDX = {"H": 0, "D": 1, "A": 2}


def holdout():
    fixtures = {str(m["match_id"]): m for m in
                json.load(open(f"{DATA}/fifa_world_cup_2026_group_matches.json"))["matches"]}
    pred = json.load(open(f"{DATA}/wc26_predictions.json"))

    def walk(o):
        out = []
        if isinstance(o, dict):
            if "actual_result" in o and "p_model" in o:
                out.append(o)
            for v in o.values():
                out += walk(v)
        elif isinstance(o, list):
            for v in o:
                out += walk(v)
        return out

    rows = []
    for r in walk(pred):
        fx = fixtures.get(str(r["match_id"]))
        if not fx:
            continue
        vc = S.CITY_COUNTRY.get(fx["city"], "United States")
        rows.append({
            "home": r["home"], "away": r["away"],
            "home_field": r["home"] == vc, "away_field": r["away"] == vc,
            "result": r["actual_result"],
            "market": r.get("p_market"),   # lock-time, clean
        })
    return rows


# ---- method: frequency baseline -------------------------------------------
def freq_probs():
    c = Counter()
    for m in E.load_chrono(CUTOFF):
        c["H" if m["hg"] > m["ag"] else "A" if m["ag"] > m["hg"] else "D"] += 1
    tot = sum(c.values())
    return (c["H"] / tot, c["D"] / tot, c["A"] / tot)


# ---- method: ordered logit on Elo rating gap (no goal grid) ----------------
def fit_ologit(samples, iters=4000):
    """P(A)=σ(c1-η), P(D)=σ(c2-η)-σ(c1-η), P(H)=1-σ(c2-η);
       η = b·(diff/400) + h·home_field.  Coordinate ascent, stdlib."""
    b, h, c1, c2 = 1.0, 0.3, -0.4, 0.4
    sg = lambda z: 1.0 / (1.0 + math.exp(-z))

    def ll(b, h, c1, c2):
        if c1 >= c2:
            return -1e18
        t = 0.0
        for s in samples:
            eta = b * (s["diff"] / 400.0) + (0.0 if s["neutral"] else h)
            pA = sg(c1 - eta)
            pH = 1 - sg(c2 - eta)
            pD = max(sg(c2 - eta) - pA, 1e-12)
            res = "H" if s["hg"] > s["ag"] else "A" if s["ag"] > s["hg"] else "D"
            t += math.log(max({"H": pH, "D": pD, "A": pA}[res], 1e-12))
        return t

    params = [b, h, c1, c2]
    step = [0.1, 0.1, 0.1, 0.1]
    best = ll(*params)
    for _ in range(iters):
        improved = False
        for i in range(4):
            for sgn in (1, -1):
                cand = list(params)
                cand[i] += sgn * step[i]
                v = ll(*cand)
                if v > best + 1e-9:
                    params, best, improved = cand, v, True
        if not improved:
            step = [s / 2 for s in step]
            if max(step) < 1e-5:
                break
    return params


def ologit_probs(params, diff, home_field):
    b, h, c1, c2 = params
    sg = lambda z: 1.0 / (1.0 + math.exp(-z))
    eta = b * (diff / 400.0) + (h if home_field else 0.0)
    pA = sg(c1 - eta)
    pH = 1 - sg(c2 - eta)
    return (pH, max(sg(c2 - eta) - pA, 1e-12), pA)


# ---- scoring ---------------------------------------------------------------
def score(name, prob_fn, rows, common):
    ll = brier = 0.0
    hits = n = 0
    for i, m in enumerate(rows):
        if i not in common:
            continue
        p = prob_fn(m)
        if p is None:
            continue
        res = RES_IDX[m["result"]]
        ll -= math.log(max(p[res], 1e-9))
        for k in range(3):
            brier += (p[k] - (1.0 if k == res else 0.0)) ** 2
        hits += int(max(range(3), key=lambda k: p[k]) == res)
        n += 1
    return {"name": name, "n": n, "logloss": ll / n, "brier": brier / n,
            "hits": hits, "hitpct": hits / n}


def main():
    rows = holdout()

    # --- build each WC-blind model once ---
    base = S.params()
    dc = S.fit(S.load_matches(CUTOFF, base["half_life"], base["friendly_w"],
                              base["margin_cap"]), base["shrink"])

    R, samples = E.replay(E.load_chrono(CUTOFF))
    goals = E.fit_goals(samples)
    olg = fit_ologit(samples)
    fb = freq_probs()
    print(f"ordered-logit fit: b={olg[0]:.3f} h={olg[1]:.3f} "
          f"c1={olg[2]:.3f} c2={olg[3]:.3f}")

    def dc_fn(m):
        l1, l2 = S.lambdas(dc, m["home"], m["away"], m["home_field"])
        if m["away_field"]:
            l2a, l1a = S.lambdas(dc, m["away"], m["home"], True)
            l1, l2 = l1a, l2a
        return S.one_x_two(S.score_grid(l1, l2, base["rho"]))

    def elo_fn(m):
        if m["home"] not in R or m["away"] not in R:
            return None
        l1, l2 = E.lambdas_elo(R, goals, m["home"], m["away"], m["home_field"])
        return S.one_x_two(S.score_grid(l1, l2, base["rho"]))

    def ologit_fn(m):
        if m["home"] not in R or m["away"] not in R:
            return None
        diff = R[m["home"]] - R[m["away"]]
        return ologit_probs(olg, diff, m["home_field"])

    def freq_fn(m):
        return fb

    def market_fn(m):
        mk = m["market"]
        if not mk:
            return None
        return (mk["H"], mk["D"], mk["A"])

    methods = [("freq (baseline)", freq_fn), ("ologit (Elo gap)", ologit_fn),
               ("elo->poisson", elo_fn), ("market (lock)", market_fn),
               ("dc (shipped)", dc_fn)]

    # common subset: matches EVERY method can price (so it's apples-to-apples)
    common = set(range(len(rows)))
    for _, fn in methods:
        common &= {i for i, m in enumerate(rows) if fn(m) is not None}

    draws = sum(rows[i]["result"] == "D" for i in common)
    print(f"\ncommon subset: {len(common)} matches "
          f"(every method prices), draws={draws}\n")
    print(f"{'method':<20}{'log-loss':>9}{'brier':>8}{'hit':>11}")
    print("-" * 48)
    res = [score(n, fn, rows, common) for n, fn in methods]
    for s in sorted(res, key=lambda x: x["logloss"]):
        print(f"{s['name']:<20}{s['logloss']:>9.4f}{s['brier']:>8.4f}"
              f"{s['hits']:>5}/{s['n']} {s['hitpct']:>3.0%}")


if __name__ == "__main__":
    main()
