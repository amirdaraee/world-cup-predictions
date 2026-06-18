"""Out-of-sample tuning probe — fit ONLY on pre-tournament history, score on
the 24 played WC2026 matches as a clean holdout.

No WC result ever enters a fit: every model is trained with cutoff 2026-06-10
(the CSV ends before the tournament, verified). We then read the already-played
matches from wc26_predictions.json and score each candidate's 1X2 probabilities
against the actual outcomes. This shows which hyperparameter tweaks WOULD have
scored better on this round — without letting the round contaminate the fit.

The point is diagnosis, not adoption: 24 matches is far too thin to retune on,
and the per-variant spread below is mostly noise. Read it that way.
"""
import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pipeline"))
import wc26_simulate as S  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
CUTOFF = "2026-06-10"   # < first WC kickoff (2026-06-11): training is WC-blind


def played_matches():
    """The graded WC matches: home/away + city (for home-field) + actual 1X2."""
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
        rows.append({"home": r["home"], "away": r["away"],
                     "city": fx["city"], "result": r["actual_result"]})
    return rows


def model_probs(model, m):
    """Reproduce main()'s home-field venue logic exactly."""
    vc = S.CITY_COUNTRY.get(m["city"], "United States")
    home_field = m["home"] == vc
    away_field = m["away"] == vc
    l1, l2 = S.lambdas(model, m["home"], m["away"], home_field)
    if away_field:
        l2a, l1a = S.lambdas(model, m["away"], m["home"], True)
        l1, l2 = l1a, l2a
    return l1, l2


def score(params, matches):
    model = S.fit(S.load_matches(CUTOFF, params["half_life"], params["friendly_w"],
                                 params["margin_cap"]), params["shrink"],
                  value_beta=params.get("value_beta", S.VALUE_BETA))
    ll = brier = 0.0
    hits = n = 0
    for m in matches:
        if m["home"] not in model["att"] or m["away"] not in model["att"]:
            continue
        l1, l2 = model_probs(model, m)
        pH, pD, pA = S.one_x_two(S.score_grid(l1, l2, params["rho"]))
        p = {"H": pH, "D": pD, "A": pA}
        res = m["result"]
        ll -= math.log(max(p[res], 1e-9))
        for k in p:
            brier += (p[k] - (1.0 if k == res else 0.0)) ** 2
        hits += int(max(p, key=p.get) == res)
        n += 1
    return {"n": n, "logloss": ll / n, "brier": brier / n, "hits": hits,
            "hitpct": hits / n}


def main():
    matches = played_matches()
    base = S.params()
    base = {**base, "value_beta": S.VALUE_BETA}
    print(f"holdout: {len(matches)} played WC matches; "
          f"draws={sum(m['result']=='D' for m in matches)}\n")

    variants = [("SHIPPED (baseline)", base)]
    # principled single-knob perturbations (motivated by priors, NOT by the
    # WC results): the round was draw-heavy, so test more draw mass (rho), plus
    # the usual recency / shrinkage / friendly / value-prior knobs.
    variants += [
        ("rho -0.10 (DC default, +draws)", {**base, "rho": -0.10}),
        ("rho -0.15 (max draws)", {**base, "rho": -0.15}),
        ("rho  0.00 (no DC corr)", {**base, "rho": 0.0}),
        ("half_life 548 (faster decay)", {**base, "half_life": 548}),
        ("half_life 365 (fastest)", {**base, "half_life": 365}),
        ("shrink 8.0 (more regression)", {**base, "shrink": 8.0}),
        ("shrink 12.0 (max regression)", {**base, "shrink": 12.0}),
        ("friendly_w 0.6", {**base, "friendly_w": 0.6}),
        ("friendly_w 0.4", {**base, "friendly_w": 0.4}),
        ("value_beta 0.0 (no squad $)", {**base, "value_beta": 0.0}),
        ("value_beta 0.6 (strong squad $)", {**base, "value_beta": 0.6}),
        ("margin_cap 4 (tame blowouts)", {**base, "margin_cap": 4}),
    ]

    rows = []
    for name, p in variants:
        s = score(p, matches)
        rows.append((name, s))
    base_ll = rows[0][1]["logloss"]
    print(f"{'variant':<34}{'log-loss':>9}{'Δ':>8}{'brier':>8}{'hit':>9}")
    print("-" * 68)
    for name, s in rows:
        d = s["logloss"] - base_ll
        mark = "" if abs(d) < 1e-9 else f"{d:+.4f}"
        print(f"{name:<34}{s['logloss']:>9.4f}{mark:>8}{s['brier']:>8.4f}"
              f"{s['hits']:>4}/{s['n']} {s['hitpct']:>3.0%}")


if __name__ == "__main__":
    main()
