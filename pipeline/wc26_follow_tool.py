"""Hypothetical 'what if you'd followed the model' return — PUBLIC DATA ONLY.

Reconstructs the moneyline bets the tool WOULD have flagged (raw-model edge over
the lock-time market >= min_edge_match) from the locked pre-tournament picks in
wc26_predictions.json, flat-stakes each one, and grades them against the actual
results. It answers "if you had flat-staked every moneyline signal, what would
you have made?" — with no look-ahead (model + market prices are both frozen
pre-match) and, deliberately, NO personal betting data: it never reads the
ledger, the paper log, stakes, or the wallet. Everything here is reproducible
from committed files.

Knockout ties are graded the same way but priced differently: the canonical
wc26_market_prices.json is re-fetched after matches (prices collapse to 0/1 —
contaminated), so each finished tie is priced from the LATEST time-coded
snapshot in runs/ fetched strictly before kickoff whose moneyline is sane.
The model side is safe by construction: wc26_simulate.py trains at a hard
pre-tournament cutoff, so it never sees tournament results. Ties without a
clean pre-kickoff snapshot are skipped (and counted), and signals grade on
the 90-minute result — a level score is a Draw regardless of penalties.

Writes data/wc26_follow_tool.json for the site; prints a summary.

    python3 pipeline/wc26_follow_tool.py
"""
import datetime
import glob
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
STAKE = 1.0   # flat $1 per signal — scale linearly for any unit
MIN_MODEL_P = 0.10  # mirror the live tool's longshot floor (min_model_prob)


def edge_threshold():
    cfg = json.load(open(os.path.join(ROOT, "betting", "config.json")))
    return cfg.get("min_edge_match", 0.08)


def graded_priced():
    pred = json.load(open(os.path.join(DATA, "wc26_predictions.json")))

    def walk(o):
        out = []
        if isinstance(o, dict):
            if "actual_result" in o and "p_model" in o and o.get("p_market"):
                out.append(o)
            for v in o.values():
                out += walk(v)
        elif isinstance(o, list):
            for v in o:
                out += walk(v)
        return out
    return walk(pred)


def backtest():
    thr = edge_threshold()
    bets = []
    for r in graded_priced():
        pm, mk = r["p_model"], r["p_market"]
        actual = r["actual_result"]
        for out in ("H", "D", "A"):
            edge = pm[out] - mk[out]
            if edge < thr:
                continue
            price = mk[out]
            if not (0 < price < 1):
                continue
            won = (actual == out)
            payout = STAKE / price if won else 0.0   # 1/price shares * $1
            bets.append({
                "match": f"{r['home']} v {r['away']}",
                "side": {"H": r["home"], "D": "Draw", "A": r["away"]}[out],
                "model": round(pm[out], 3), "market": round(price, 3),
                "edge": round(edge, 3), "won": won,
                "stake": STAKE, "payout": round(payout, 3),
                "profit": round(payout - STAKE, 3),
            })
    return bets, thr


def _price_snapshots():
    """All runs/ market-price archives as (fetched_at, prices), newest first.
    These are append-only time-coded copies, so the fetch time is trustworthy —
    unlike the canonical file, which gets overwritten with post-match prices."""
    snaps = []
    for path in glob.glob(os.path.join(ROOT, "runs", "*_market_prices.json")):
        try:
            d = json.load(open(path))
            at = datetime.datetime.strptime(
                d["fetched_at"], "%Y-%m-%d %H:%M UTC"
            ).replace(tzinfo=datetime.timezone.utc)
        except (KeyError, ValueError, json.JSONDecodeError):
            continue
        snaps.append((at, d.get("prices", {})))
    return sorted(snaps, key=lambda s: s[0], reverse=True)


def _pre_kickoff_moneyline(match_id, kickoff, snaps):
    """Latest sane moneyline snapped strictly before kickoff, else None.
    Sanity gates keep out placeholder books and part-settled markets: every
    price in (0.01, 0.99) and the book summing to roughly one."""
    for at, prices in snaps:
        if at >= kickoff:
            continue
        rec = prices.get(str(match_id)) or prices.get(match_id) or {}
        ml = rec.get("moneyline") or {}
        vals = [ml.get(k) for k in ("home", "draw", "away")]
        if any(v is None or not (0.01 < v < 0.99) for v in vals):
            continue
        if not (0.90 <= sum(vals) <= 1.15):
            continue
        return ml, at
    return None


def knockout_backtest(thr):
    """Flat-$1 signals over FINISHED knockout ties, look-ahead-free: frozen
    pre-tournament model vs the last committed pre-kickoff price snapshot,
    graded on the 90-minute result (level = Draw, penalties ignored)."""
    try:
        kos = json.load(open(
            os.path.join(DATA, "wc26_knockout_matches.json")))["matches"]
        sims = json.load(open(
            os.path.join(DATA, "wc26_simulations.json")))["simulations"]
    except (FileNotFoundError, KeyError):
        return [], 0
    snaps = _price_snapshots()
    bets, skipped = [], 0
    for m in kos:
        if not str(m.get("status", "")).startswith("Match Finished") \
                or not m.get("score") or not m.get("date_utc"):
            continue
        sim = sims.get(str(m["match_id"]))
        if not sim:
            continue
        kickoff = datetime.datetime.fromisoformat(m["date_utc"])
        found = _pre_kickoff_moneyline(m["match_id"], kickoff, snaps)
        if found is None:
            skipped += 1
            continue
        ml, at = found
        hs, as_ = (int(x) for x in m["score"].split("-"))
        actual = "H" if hs > as_ else "A" if as_ > hs else "D"
        pm = sim["moneyline"]
        for out, key in (("H", "home"), ("D", "draw"), ("A", "away")):
            p, price = pm[key], ml[key]
            edge = p - price
            if edge < thr or p < MIN_MODEL_P:
                continue
            won = (actual == out)
            payout = STAKE / price if won else 0.0
            bets.append({
                "match": f"{m['home']} v {m['away']}",
                "round": m.get("round", ""),
                "side": {"H": m["home"], "D": "Draw", "A": m["away"]}[out],
                "model": round(p, 3), "market": round(price, 3),
                "edge": round(edge, 3), "won": won,
                "stake": STAKE, "payout": round(payout, 3),
                "profit": round(payout - STAKE, 3),
                "priced_at": at.strftime("%Y-%m-%d %H:%M UTC"),
            })
    return bets, skipped


def summarize(bets, thr):
    n = len(bets)
    wins = sum(b["won"] for b in bets)
    staked = n * STAKE
    returned = sum(b["payout"] for b in bets)
    profit = returned - staked
    roi = profit / staked if staked else 0.0
    avg_edge = sum(b["edge"] for b in bets) / n if n else 0.0
    return {
        "threshold": thr, "n_bets": n, "wins": wins,
        "win_rate": round(wins / n, 4) if n else 0.0,
        "staked": round(staked, 2), "returned": round(returned, 2),
        "profit": round(profit, 2), "roi": round(roi, 4),
        "avg_edge": round(avg_edge, 3), "stake_unit": STAKE,
    }


def main():
    bets, thr = backtest()
    s = summarize(bets, thr)
    ko_bets, ko_skipped = knockout_backtest(thr)
    ko_s = summarize(ko_bets, thr)
    payload = {
        "note": ("Hypothetical flat-stake backtest of the model's moneyline "
                 "signals (raw-model edge >= min_edge_match over the pre-match "
                 "market), graded on actual results. Group stage prices come "
                 "from the pre-tournament lock; knockout prices from the last "
                 "committed pre-kickoff snapshot in runs/ (ties without a "
                 "clean one are skipped), graded on the 90-minute result. "
                 "Public data only — no personal betting records. Not betting "
                 "advice."),
        "summary": s,
        "bets": sorted(bets, key=lambda b: -b["edge"]),
        "knockout": {
            "summary": ko_s,
            "bets": sorted(ko_bets, key=lambda b: -b["edge"]),
            "skipped_no_snapshot": ko_skipped,
        },
    }
    out = os.path.join(DATA, "wc26_follow_tool.json")
    json.dump(payload, open(out, "w"), indent=2, ensure_ascii=False)

    print(f"Follow-the-tool (flat ${STAKE:.0f}/signal, edge >= {thr*100:.0f}c, "
          f"graded on actuals):")
    print(f"  signals: {s['n_bets']} | wins: {s['wins']} "
          f"({s['win_rate']*100:.0f}%) | avg edge {s['avg_edge']*100:.1f}c")
    print(f"  staked ${s['staked']:.0f} -> returned ${s['returned']:.2f} "
          f"| profit ${s['profit']:+.2f} | ROI {s['roi']*100:+.1f}%")
    print(f"  knockout: {ko_s['n_bets']} signals | wins: {ko_s['wins']} "
          f"| profit ${ko_s['profit']:+.2f} | ROI {ko_s['roi']*100:+.1f}% "
          f"| ties skipped (no clean pre-kickoff snapshot): {ko_skipped}")
    print(f"  -> {out}")
    print("\n  top signals:")
    for b in (payload["bets"] + payload["knockout"]["bets"])[:12]:
        print(f"    {b['side']:<16} {b['model']:.0%} vs mkt {b['market']:.0%} "
              f"(+{b['edge']*100:.0f}c)  {'WON ' if b['won'] else 'lost'} "
              f"${b['profit']:+.2f}")


if __name__ == "__main__":
    main()
