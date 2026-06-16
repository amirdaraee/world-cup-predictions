"""Accuracy convergence tracker.

Snapshots the public scorecard once per matchday so we can watch the live
numbers converge toward the model's out-of-sample backtest. It records ONLY
already-computed, look-ahead-free grades:

  - the locked-bracket result/exact/Brier/log-loss from
    wc26_predictions.json["accuracy"] (graded against pre-tournament picks;
    market is graded at lock-time prices, so model vs market vs blend is fair)
  - the forward-locked O/U 2.5 grade from wc26_totals_locked.json
    (write-once, pre-kickoff)
  - the model's backtest log-loss as the convergence target

It appends to data/wc26_accuracy_log.json, but only when the number of graded
matches has changed since the last snapshot (so re-running nightly is a no-op
until the next match finishes). `--force` appends regardless.

    python3 pipeline/wc26_accuracy_track.py          # snapshot if graded count grew
    python3 pipeline/wc26_accuracy_track.py --force   # always append
    python3 pipeline/wc26_accuracy_track.py --show     # print the log, no write

Nothing here is fitted or look-ahead; it only reads grades produced upstream by
wc26_update_results.py (locked picks) and wc26_totals_lock.py. Pure stdlib.
"""
import json
import math
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
LOG = os.path.join(DATA, "wc26_accuracy_log.json")


def _load(name):
    with open(os.path.join(DATA, name)) as f:
        return json.load(f)


def totals_lock_grade(locked, finished_total_by_mid):
    """Forward-locked O/U grade per source — same formula as the site's
    totals_grade, recomputed here so the tracker has no heavy imports."""
    srcs = ("over_model", "over_market", "over_blend")
    agg = {s: {"n": 0, "ll": 0.0, "hit": 0} for s in srcs}
    n = 0
    for mid, rec in locked.items():
        at = finished_total_by_mid.get(mid)
        if at is None:
            continue
        over = 1 if at > rec.get("line", 2.5) else 0
        graded = False
        for s in srcs:
            p = rec.get(s)
            if p is None:
                continue
            st = agg[s]
            st["n"] += 1
            st["ll"] -= math.log(max(p if over else 1 - p, 1e-9))
            st["hit"] += int((p > 0.5) == bool(over))
            graded = True
        n += graded
    out = {"graded": n}
    for s in srcs:
        st = agg[s]
        if st["n"]:
            out[s.replace("over_", "")] = {
                "hit": st["hit"], "of": st["n"],
                "logloss": round(st["ll"] / st["n"], 4)}
    return out


def build_snapshot():
    pred = _load("wc26_predictions.json")
    acc = pred.get("accuracy") or {}
    sims = _load("wc26_simulations.json")

    # finished totals, for the forward-locked O/U grade
    gm = _load("fifa_world_cup_2026_group_matches.json")["matches"]
    finished_total = {}
    for m in gm:
        if str(m.get("status", "")).startswith("Match Finished") and m.get("score"):
            h, a = m["score"].split("-")
            finished_total[str(m["match_id"])] = int(h) + int(a)
    try:
        locked = _load("wc26_totals_locked.json").get("matches", {})
        totals = totals_lock_grade(locked, finished_total)
    except FileNotFoundError:
        totals = {"graded": 0}

    cmp = acc.get("compare", {})
    return {
        "date": time.strftime("%Y-%m-%d", time.gmtime()),
        "generated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        "graded_group_matches": acc.get("graded_group_matches", 0),
        "result_hits": acc.get("result_hits"),
        "result_pct": acc.get("result_pct"),
        "exact_score_hits": acc.get("exact_score_hits"),
        "moneyline": {
            "blend": cmp.get("blend"),
            "model": cmp.get("model"),
            "market": cmp.get("market"),
            "market_priced_matches": acc.get("market_priced_matches"),
        },
        "totals_ou25_locked": totals,
        "backtest_logloss_target": sims.get("backtest_logloss"),
    }


def load_log():
    if os.path.exists(LOG):
        return json.load(open(LOG))
    return {"note": ("Per-matchday accuracy snapshots. Tracks live log-loss "
                     "convergence toward backtest_logloss_target. Append-only; "
                     "written by pipeline/wc26_accuracy_track.py."),
            "snapshots": []}


def report(snap, prev, target):
    n = snap["graded_group_matches"]
    ml = snap["moneyline"]["blend"] or {}
    ll = ml.get("logloss")
    line = (f"MD snapshot: {n} graded · result {snap['result_hits']}/{n} "
            f"({(snap['result_pct'] or 0) * 100:.0f}%) · blend log-loss {ll}")
    if target and ll is not None:
        gap = ll - target
        line += f" · {gap:+.3f} vs {target} backtest"
    if prev:
        pll = (prev.get("moneyline", {}).get("blend") or {}).get("logloss")
        if pll is not None and ll is not None:
            line += f" · {ll - pll:+.3f} since last ({prev['graded_group_matches']} graded)"
    print(line)
    t = snap["totals_ou25_locked"]
    if t.get("graded"):
        b = t.get("blend", {})
        print(f"  O/U 2.5 (locked): {b.get('hit')}/{b.get('of')} blend, "
              f"log-loss {b.get('logloss')} on {t['graded']} matches")


def main():
    args = sys.argv[1:]
    log = load_log()
    snaps = log["snapshots"]
    prev = snaps[-1] if snaps else None

    if "--show" in args:
        for s in snaps:
            report(s, None, s.get("backtest_logloss_target"))
        print(f"\n{len(snaps)} snapshot(s) in {LOG}")
        return

    snap = build_snapshot()
    if snap["graded_group_matches"] == 0:
        print("no graded matches yet; nothing to snapshot")
        return

    grew = (prev is None
            or snap["graded_group_matches"] != prev["graded_group_matches"])
    if not grew and "--force" not in args:
        print(f"graded count unchanged ({snap['graded_group_matches']}); "
              f"no new snapshot (use --force to append anyway)")
        report(snap, prev, snap["backtest_logloss_target"])
        return

    snaps.append(snap)
    json.dump(log, open(LOG, "w"), indent=2, ensure_ascii=False)
    report(snap, prev, snap["backtest_logloss_target"])
    print(f"appended snapshot #{len(snaps)} -> {LOG}")


if __name__ == "__main__":
    main()
