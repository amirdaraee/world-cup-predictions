"""Top up the just-placed R32 value bets with the
remaining cap headroom — more money on the SAME +edge positions, proportional
to their current size. Writes betting/state/plan.json for
`place_bets.py --live --allow-topup`.
"""
import json, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)); BET = os.path.join(HERE, "..", "betting")
sys.path.insert(0, BET)
from find_bets import CFG

NEG_RISK = {"moneyline", "golden_boot", "top_scorer_nation", "futures"}


def main():
    led = json.load(open(f"{BET}/state/ledger.json"))["placed"]
    spent = sum(b["stake_usdc"] for b in led)
    headroom = CFG["max_total_stake_usdc"] - spent
    last_date = max(b["at"][:10] for b in led if b.get("at"))
    rnd = [b for b in led if b.get("at", "").startswith(last_date)]
    total = sum(b["stake_usdc"] for b in rnd)
    print(f"topping up {len(rnd)} bets (${total:.2f}) by ${headroom:.2f} "
          f"(round date {last_date})")
    bets = []
    budget = headroom - 0.5
    for b in rnd:
        add = round(b["stake_usdc"] / total * budget, 2)
        add = min(add, CFG["max_per_bet_usdc"])
        if add < 1:
            continue
        bets.append({
            "category": b["category"], "match_id": b.get("match_id", ""),
            "bet": b["bet"], "question": b.get("question", ""),
            "token_id": b["token_id"],
            "model_p": b.get("model_p"),
            "market_p": b.get("price_at_exec") or b.get("price_seen"),
            "neg_risk": b["category"] in NEG_RISK,
            "stake_usdc": add,
        })
    plan = {"created": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
            "note": "R32 top-up: add to the value positions (allow-topup)",
            "total_planned_usdc": round(sum(b["stake_usdc"] for b in bets), 2),
            "bets": bets}
    json.dump(plan, open(f"{BET}/state/plan.json", "w"), indent=2)
    print(f"built {len(bets)} top-ups = ${plan['total_planned_usdc']} -> plan.json")
    for b in sorted(bets, key=lambda x: -x["stake_usdc"])[:25]:
        print(f"   +${b['stake_usdc']:>5.2f}  {b['bet'][:55]}")


if __name__ == "__main__":
    main()
