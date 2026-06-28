"""Top-up: flat result (moneyline) bets on the R32 matches that don't
yet have one in the ledger (excluding the anti-model Brazil v Japan). Splits
the remaining cap headroom evenly. Writes betting/state/plan.json for place_bets.
"""
import json, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)); BET = os.path.join(HERE, "..", "betting")
sys.path.insert(0, BET)
import find_bets as F  # noqa
from find_bets import CFG

DATA = F.DATA
SKIP = {"Brazil", "Japan"}


def main():
    led = json.load(open(f"{BET}/state/ledger.json"))["placed"]
    spent = sum(b["stake_usdc"] for b in led)
    headroom = CFG["max_total_stake_usdc"] - spent
    sims = json.load(open(f"{DATA}/wc26_simulations.json"))["simulations"]
    snap = json.load(open(f"{DATA}/wc26_market_prices.json"))["prices"]
    ko = [m for m in json.load(open(f"{DATA}/wc26_knockout_matches.json"))["matches"]
          if m.get("round") == "Round of 32"]
    # which R32 matches already have a moneyline bet in the ledger?
    ml_done = set()
    for b in led:
        if b.get("category") == "moneyline" and " v " in b.get("bet", ""):
            ml_done.add(b["bet"].split(":")[0].strip())
    targets = []
    for m in ko:
        if {m["home"], m["away"]} & SKIP:
            continue
        if f"{m['home']} v {m['away']}" in ml_done:
            continue
        sim = sims.get(str(m["match_id"]))
        if not sim:
            continue
        ml = sim["moneyline"]; pick = max(ml, key=ml.get)
        targets.append((m, pick, ml[pick], snap.get(str(m["match_id"]), {}).get("slug")))
    stake = round((headroom - 1.0) / len(targets), 2)
    print(f"headroom ${headroom:.2f}; {len(targets)} result bets at ${stake:.2f} each")
    bets = []
    for m, pick, mp, slug in targets:
        try:
            evs = F.gamma("/events", slug=slug)
        except Exception as e:
            print(f"  fetch failed {m['home']} v {m['away']}: {e}"); continue
        if not evs:
            continue
        for mk in evs[0]["markets"]:
            ql = mk["question"].lower()
            side = ("draw" if "draw" in ql else
                    "home" if any(f"will {n} win" in ql for n in F.names_for(m["home"])) else
                    "away" if any(f"will {n} win" in ql for n in F.names_for(m["away"])) else None)
            if side != pick or not F.tradeable(mk):
                continue
            try:
                price = float(json.loads(mk["outcomePrices"])[0]); token = json.loads(mk["clobTokenIds"])[0]
            except (KeyError, ValueError, IndexError):
                continue
            label = {"home": m["home"], "away": m["away"], "draw": "Draw"}[pick]
            anti = " ANTI-MODEL" if mp < price else ""
            print(f"  {m['home']} v {m['away']} -> {label} model {mp:.0%} mkt {price:.0%}{anti}")
            bets.append({"category": "moneyline", "match_id": str(m["match_id"]),
                         "bet": f"{m['home']} v {m['away']}: {label} (YES)",
                         "question": mk["question"], "token_id": token,
                         "model_p": round(mp, 4), "market_p": price,
                         "neg_risk": bool(mk.get("negRisk")), "stake_usdc": stake,
                         "edge": round(mp - price, 4)})
            break
        time.sleep(0.12)
    plan = {"created": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
            "note": "R32 top-up: flat result bets on remaining matches",
            "total_planned_usdc": round(sum(b["stake_usdc"] for b in bets), 2), "bets": bets}
    json.dump(plan, open(f"{BET}/state/plan.json", "w"), indent=2)
    print(f"\nbuilt {len(bets)} result bets = ${plan['total_planned_usdc']} -> plan.json")


if __name__ == "__main__":
    main()
