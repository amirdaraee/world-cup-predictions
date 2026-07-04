"""Forward-lock model over/under (totals) probabilities for UNPLAYED
fixtures — group matches and, once their teams are confirmed, knockout
ties — so the report card can grade CALIBRATED totals this tournament;
the pre-tournament bracket was locked without them. Write-once per match,
and only while the match is still in the future, so every entry is a
genuine pre-kickoff prediction graded going forward as results land.
(Knockout totals grade on the 90-minute score, matching market settlement.)

  python3 pipeline/wc26_totals_lock.py     # lock any newly-lockable fixtures

Output (committed): data/wc26_totals_locked.json
  {locked_from, line, matches: {mid: {locked_at, kickoff, line,
   over_model, over_market, over_blend}}}
Disclosed on the report card as totals locked from a mid-tournament date,
kept separate from the pristine pre-tournament bracket on purpose.
"""
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wc26_simulate import blend, now_utc   # noqa: E402

OUT = f"{DATA}/wc26_totals_locked.json"
LINE = 2.5
KEY = f"over_{LINE}"


def lockable(m, store, now):
    """Lock a fixture only if unseen, unplayed and still pre-kickoff —
    anything else would be a duplicate or a look-ahead."""
    mid = str(m["match_id"])
    if mid in store["matches"] or m.get("score"):
        return False
    try:
        ko = datetime.fromisoformat(m["date_utc"])
    except (ValueError, KeyError, TypeError):
        return False
    return ko > now


def lock(now=None):
    sims = json.load(open(f"{DATA}/wc26_simulations.json"))["simulations"]
    prices = json.load(open(f"{DATA}/wc26_market_prices.json"))["prices"]
    fixtures = json.load(
        open(f"{DATA}/fifa_world_cup_2026_group_matches.json"))["matches"]
    try:
        fixtures = fixtures + json.load(
            open(f"{DATA}/wc26_knockout_matches.json"))["matches"]
    except FileNotFoundError:
        pass                       # bracket not known yet — group only
    try:
        store = json.load(open(OUT))
    except FileNotFoundError:
        store = {"locked_from": now_utc(), "line": LINE,
                 "note": "model over/under locked pre-kickoff for unplayed "
                         "fixtures (group + knockout); graded going forward",
                 "matches": {}}
    now = now or datetime.now(timezone.utc)
    added = 0
    for m in fixtures:
        if not lockable(m, store, now):
            continue
        mid = str(m["match_id"])
        sim = sims.get(mid)
        if not sim or KEY not in sim.get("totals", {}):
            continue
        om = sim["totals"][KEY]
        mk = (prices.get(mid, {}).get("totals") or {}).get(KEY)
        # the market O/U only counts as a live opinion; a settled or
        # untraded book pins to 0/1 and is not a forecast
        live_mkt = mk is not None and 0.02 < mk < 0.98
        ob = (blend({"O": om, "U": 1 - om}, {"O": mk, "U": 1 - mk})["O"]
              if live_mkt else om)
        store["matches"][mid] = {
            "locked_at": now_utc(), "kickoff": m["date_utc"], "line": LINE,
            "over_model": round(om, 4),
            "over_market": round(mk, 4) if live_mkt else None,
            "over_blend": round(ob, 4)}
        added += 1
    json.dump(store, open(OUT, "w"), indent=1, ensure_ascii=False)
    print(f"totals lock: +{added} fixtures "
          f"({len(store['matches'])} locked total) -> {OUT}")
    return store


if __name__ == "__main__":
    lock()
