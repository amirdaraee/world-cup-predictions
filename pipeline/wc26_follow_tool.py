"""Hypothetical 'what if you'd followed the model' return — PUBLIC DATA ONLY.

Reconstructs the moneyline bets the tool WOULD have flagged (raw-model edge over
the lock-time market >= min_edge_match) from the locked pre-tournament picks in
wc26_predictions.json, flat-stakes each one, and grades them against the actual
results. It answers "if you had flat-staked every moneyline signal, what would
you have made?" — with no look-ahead (model + market prices are both frozen
pre-match) and, deliberately, NO personal betting data: it never reads the
ledger, the paper log, stakes, or the wallet. Everything here is reproducible
from committed files.

Writes data/wc26_follow_tool.json for the site; prints a summary.

    python3 pipeline/wc26_follow_tool.py
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
STAKE = 1.0   # flat $1 per signal — scale linearly for any unit


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
    payload = {
        "note": ("Hypothetical flat-stake backtest of the model's moneyline "
                 "signals (raw-model edge >= min_edge_match over the lock-time "
                 "market), graded on actual results. Public data only — no "
                 "personal betting records. Not betting advice."),
        "summary": s,
        "bets": sorted(bets, key=lambda b: -b["edge"]),
    }
    out = os.path.join(DATA, "wc26_follow_tool.json")
    json.dump(payload, open(out, "w"), indent=2, ensure_ascii=False)

    print(f"Follow-the-tool (flat ${STAKE:.0f}/signal, edge >= {thr*100:.0f}c, "
          f"graded on actuals):")
    print(f"  signals: {s['n_bets']} | wins: {s['wins']} "
          f"({s['win_rate']*100:.0f}%) | avg edge {s['avg_edge']*100:.1f}c")
    print(f"  staked ${s['staked']:.0f} -> returned ${s['returned']:.2f} "
          f"| profit ${s['profit']:+.2f} | ROI {s['roi']*100:+.1f}%")
    print(f"  -> {out}")
    print("\n  top signals:")
    for b in payload["bets"][:8]:
        print(f"    {b['side']:<16} {b['model']:.0%} vs mkt {b['market']:.0%} "
              f"(+{b['edge']*100:.0f}c)  {'WON ' if b['won'] else 'lost'} "
              f"${b['profit']:+.2f}")


if __name__ == "__main__":
    main()
