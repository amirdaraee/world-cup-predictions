"""One-off OVERRIDE: flat $5 on every UNPLAYED group match's model pick,
regardless of edge. Builds betting/state/plan.json for place_bets.py.

This deliberately bypasses the edge gate and Kelly sizing in find_bets —
betting no-edge favourites is negative-EV (you pay the spread on the
obvious ones). User-requested, eyes open. It still REUSES every find_bets
safety helper: live Gamma token lookup, the tradeable() liquidity guard,
and the kickoff skip; and place_bets.py then re-applies plan-age, kickoff
recheck, live-ask/slippage, ledger dedup, per-bet/total caps and the
wallet-balance check. So broken books, started matches and already-held
markets are still filtered.

  .venv/bin/python3 experiments/bet_all_groups.py        # build plan.json
  .venv/bin/python3 betting/place_bets.py                # dry-run review
  .venv/bin/python3 betting/place_bets.py --live         # place real orders
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BET = os.path.join(HERE, "..", "betting")
sys.path.insert(0, BET)
import find_bets as F   # noqa: E402

STAKE = 5.0


def build():
    sims = json.load(open(f"{F.DATA}/wc26_simulations.json"))["simulations"]
    snap = json.load(open(f"{F.DATA}/wc26_market_prices.json"))["prices"]
    times = F.kickoff_times()
    bets, skipped = [], []
    for mid, rec in snap.items():
        sim = sims.get(mid)
        if not sim or F.started(mid, times):
            continue
        # group matches only (skip knockouts — they have no fixed teams yet)
        if sim.get("round"):
            continue
        pick = max(("home", "draw", "away"), key=lambda s: sim["moneyline"][s])
        label = {"home": sim["home"], "away": sim["away"], "draw": "Draw"}[pick]
        try:
            evs = F.gamma("/events", slug=rec["slug"])
        except Exception as e:
            skipped.append(f"{sim['home']} v {sim['away']}: fetch failed ({e})")
            continue
        if not evs:
            skipped.append(f"{sim['home']} v {sim['away']}: no event")
            continue
        chosen = None
        for mk in evs[0]["markets"]:
            ql = mk["question"].lower()
            if not F.tradeable(mk):
                continue
            side = ("draw" if "draw" in ql else
                    "home" if any(f"will {n} win" in ql for n in F.names_for(sim["home"])) else
                    "away" if any(f"will {n} win" in ql for n in F.names_for(sim["away"])) else None)
            if side != pick:
                continue
            try:
                price = float(json.loads(mk["outcomePrices"])[0])
                token = json.loads(mk["clobTokenIds"])[0]
            except (KeyError, ValueError, IndexError):
                continue
            chosen = {
                "category": "moneyline", "match_id": mid,
                "bet": f"{sim['home']} v {sim['away']}: {label} (YES)",
                "question": mk["question"], "token_id": token,
                "model_p": round(sim["moneyline"][pick], 4), "market_p": price,
                "neg_risk": bool(mk.get("negRisk")),
                "stake_usdc": STAKE, "edge": round(sim["moneyline"][pick] - price, 4),
            }
            break
        if chosen:
            bets.append(chosen)
        else:
            skipped.append(f"{sim['home']} v {sim['away']}: no tradeable {pick} market")
        time.sleep(0.12)

    plan = {"created": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
            "note": "OVERRIDE flat $5/match all unplayed groups (bet_all_groups.py)",
            "total_planned_usdc": round(sum(b["stake_usdc"] for b in bets), 2),
            "bets": bets}
    json.dump(plan, open(f"{BET}/state/plan.json", "w"), indent=2)
    print(f"built {len(bets)} flat-${STAKE:.0f} bets "
          f"(${plan['total_planned_usdc']}) -> betting/state/plan.json")
    if skipped:
        print(f"\n{len(skipped)} matches not added:")
        for s in skipped:
            print("  -", s)
    neg = [b for b in bets if b["edge"] < 0]
    print(f"\n{len(neg)}/{len(bets)} are negative-edge (model below market — "
          f"paying the spread); avg edge {sum(b['edge'] for b in bets)/max(len(bets),1):+.3f}")


if __name__ == "__main__":
    build()
