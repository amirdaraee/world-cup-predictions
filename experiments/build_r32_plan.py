"""Build an R32 plan: the de-correlated value bets from the scan PLUS one flat
result (moneyline) bet on each remaining R32 match (model's pick), minus the
anti-model Brazil filler. Writes betting/state/plan.json for place_bets. Reuses
find_bets' Gamma lookup for the filler tokens.

  python3 experiments/build_r32_plan.py [budget_usdc]

budget defaults to the remaining cap headroom (max_total_stake_usdc minus what
the ledger already holds), so no figure is hard-coded.
"""
import json, os, re, sys, time
from collections import defaultdict
HERE = os.path.dirname(os.path.abspath(__file__)); BET = os.path.join(HERE, "..", "betting")
sys.path.insert(0, BET)
import find_bets as F  # noqa
from find_bets import CFG  # noqa

DATA = F.DATA
SKIP_FILLER = {"Brazil", "Japan"}   # drop the anti-model Brazil v Japan result


def value_bets(budget):
    plan = json.load(open(f"{BET}/state/plan.json"))["bets"]
    led = json.load(open(f"{BET}/state/ledger.json"))["placed"]
    ht = {b["token_id"] for b in led}; hq = {b.get("question", "") for b in led if b.get("question")}
    new = [b for b in plan if b["token_id"] not in ht and b.get("question", "") not in hq]
    new.sort(key=lambda b: -b["edge"])
    seen, val = set(), []
    for b in new:                       # de-correlate: one total-line per team/match
        k = None
        if b["category"] == "team_totals":
            t = re.search(r": (.+?) O/U", b["bet"]); k = ("tt", b.get("match_id"), t.group(1) if t else b["bet"])
        elif b["category"] == "totals":
            k = ("to", b.get("match_id"))
        if k and k in seen:
            continue
        if k:
            seen.add(k)
        val.append(b)
    LO, HI, MM = 5.0, 20.0, 40.0
    w = {id(b): max(b["edge"], 0.001) for b in val}; tw = sum(w.values())
    st = {id(b): min(HI, max(LO, round(budget * w[id(b)] / tw, 2))) for b in val}
    pm = defaultdict(float)
    for b in sorted(val, key=lambda b: -b["edge"]):
        mid = b.get("match_id") or b["bet"]; st[id(b)] = max(0, min(st[id(b)], round(MM - pm[mid], 2))); pm[mid] += st[id(b)]
    val = [b for b in val if st[id(b)] >= LO]
    for b in val:
        b["stake_usdc"] = st[id(b)]
    return val


def filler_bets(value, total_value, budget):
    sims = json.load(open(f"{DATA}/wc26_simulations.json"))["simulations"]
    snap = json.load(open(f"{DATA}/wc26_market_prices.json"))["prices"]
    ko = [m for m in json.load(open(f"{DATA}/wc26_knockout_matches.json"))["matches"]
          if m.get("round") == "Round of 32"]
    ml_have = {b.get("match_id") for b in value if b["category"] == "moneyline"}
    targets = []
    for m in ko:
        mid = str(m["match_id"])
        if mid in ml_have or {m["home"], m["away"]} & SKIP_FILLER:
            continue
        sim = sims.get(mid)
        if not sim:
            continue
        ml = sim["moneyline"]; pick = max(ml, key=ml.get)
        targets.append((m, pick, ml[pick], snap.get(mid, {}).get("slug")))
    stake = round((budget - total_value) / len(targets), 2)
    fillers = []
    for m, pick, mp, slug in targets:
        try:
            evs = F.gamma("/events", slug=slug)
        except Exception as e:
            print(f"  filler fetch failed {m['home']} v {m['away']}: {e}"); continue
        if not evs:
            continue
        chosen = None
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
            chosen = {"category": "moneyline", "match_id": mid,
                      "bet": f"{m['home']} v {m['away']}: {label} (YES)",
                      "question": mk["question"], "token_id": token,
                      "model_p": round(mp, 4), "market_p": price,
                      "neg_risk": bool(mk.get("negRisk")), "stake_usdc": stake,
                      "edge": round(mp - price, 4)}
            break
        if chosen:
            fillers.append(chosen)
        else:
            print(f"  no tradeable result market: {m['home']} v {m['away']} ({pick})")
        time.sleep(0.12)
    return fillers


def main():
    led = json.load(open(f"{BET}/state/ledger.json"))["placed"]
    spent = sum(b["stake_usdc"] for b in led)
    budget = (float(sys.argv[1]) if len(sys.argv) > 1
              else CFG["max_total_stake_usdc"] - spent)
    val = value_bets(budget); tv = round(sum(b["stake_usdc"] for b in val), 2)
    fil = filler_bets(val, tv, budget)
    bets = val + fil
    # hard combined per-match cap: value + filler on one fixture <= $40; trim
    # the filler (the -EV leg) first, drop it if it falls below the $5 floor
    MM = 40.0
    spent = defaultdict(float)
    for b in val:
        spent[b.get("match_id")] += b["stake_usdc"]
    kept = list(val)
    for b in sorted(fil, key=lambda b: -b["edge"]):
        room = round(MM - spent[b.get("match_id")], 2)
        if room < 5.0:
            continue
        b["stake_usdc"] = min(b["stake_usdc"], room)
        spent[b.get("match_id")] += b["stake_usdc"]
        kept.append(b)
    bets = kept
    tf = round(sum(b["stake_usdc"] for b in bets) - tv, 2)
    fil = [b for b in bets if b not in val]
    plan = {"created": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
            "note": "R32: de-correlated value + flat result fillers (Brazil dropped)",
            "total_planned_usdc": round(tv + tf, 2), "bets": bets}
    json.dump(plan, open(f"{BET}/state/plan.json", "w"), indent=2)
    print(f"\nbuilt plan: {len(val)} value (${tv}) + {len(fil)} result fillers (${tf}) "
          f"= ${tv + tf:.2f} across {len(bets)} bets -> betting/state/plan.json")


if __name__ == "__main__":
    main()
