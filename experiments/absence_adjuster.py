"""EXPERIMENT: absence-aware match re-pricing.

  python3 experiments/absence_adjuster.py                  # scan injury feed
  python3 experiments/absence_adjuster.py --days 7
  python3 experiments/absence_adjuster.py --what-if "France:K. Mbappé"

Converts the API-Football injuries feed + each player's share of his team's
goals into a re-priced match card, and flags any pending bets on affected
matches. Assumptions (stated, untuned — this is an experiment):
  - an absent attacker's replacement produces HALF the absent man's share
  - an absent first-choice keeper costs ~8% more goals conceded
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from wc26_simulate import params, score_grid, one_x_two  # noqa: E402

KEY = os.environ.get("API_FOOTBALL_KEY") or \
    open(f"{ROOT}/.api_football_key").read().strip()
P = params()
REPLACEMENT_QUALITY = 0.5     # replacement produces half the absent share
KEEPER_PENALTY = 1.08         # backup keeper concedes ~8% more

players = json.load(open(f"{ROOT}/wc26_players.json"))["squads"]
sims = json.load(open(f"{ROOT}/wc26_simulations.json"))["simulations"]
fixtures = {str(m["match_id"]): m for m in json.load(
    open(f"{ROOT}/fifa_world_cup_2026_group_matches.json"))["matches"]}


def api(path, **q):
    url = f"https://v3.football.api-sports.io/{path}?" + urllib.parse.urlencode(q)
    req = urllib.request.Request(url, headers={"x-apisports-key": KEY})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["response"]


def _norm(s):
    import unicodedata
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def share_of(team, player_name):
    squad = players.get(team, [])
    total = sum(p["goals"] for p in squad) or 1
    target = _norm(player_name)
    # exact first, then surname (API formats vary: "K. Mbappé" / "Kylian Mbappé")
    for p in squad:
        if _norm(p["name"]) == target:
            return p["goals"] / total, p["position"]
    last = target.split()[-1]
    hits = [p for p in squad if _norm(p["name"]).split()[-1] == last]
    if len(hits) == 1:
        return hits[0]["goals"] / total, hits[0]["position"]
    return None, None


def first_keeper(team):
    gks = [p for p in players.get(team, []) if p["position"] == "Goalkeeper"]
    return max(gks, key=lambda p: p["apps"])["name"] if gks else None


def reprice(mid, absent):
    """absent: list of (team, name). Returns (old, new) 1X2 + meta."""
    sim = sims[mid]
    l1, l2 = sim["xg"]["home"], sim["xg"]["away"]
    notes = []
    for team, name in absent:
        s, pos = share_of(team, name)
        if s is None:
            notes.append(f"  ?? {name} not found in {team} squad")
            continue
        is_home = team == sim["home"]
        if pos == "Goalkeeper" and name == first_keeper(team):
            if is_home:
                l2 *= KEEPER_PENALTY
            else:
                l1 *= KEEPER_PENALTY
            notes.append(f"  {name} ({team}, first-choice GK): opponent xG x{KEEPER_PENALTY}")
        else:
            dock = 1 - REPLACEMENT_QUALITY * s
            if is_home:
                l1 *= dock
            else:
                l2 *= dock
            notes.append(f"  {name} ({team}): carries {s:.0%} of team goals -> attack x{dock:.3f}")
    old = one_x_two(score_grid(sim["xg"]["home"], sim["xg"]["away"], P["rho"]))
    new = one_x_two(score_grid(l1, l2, P["rho"]))
    return old, new, notes


def pending_bet_tokens():
    try:
        led = json.load(open(f"{ROOT}/betting/state/ledger.json"))["placed"]
        return {b["bet"]: b for b in led}
    except FileNotFoundError:
        return {}


def report(mid, absent):
    sim = sims[mid]
    old, new, notes = reprice(mid, absent)
    print(f"\n== {sim['home']} v {sim['away']} "
          f"({fixtures[mid]['date_utc'][:16]}) ==")
    for n in notes:
        print(n)
    for label, o, n_ in (("home", old[0], new[0]), ("draw", old[1], new[1]),
                         ("away", old[2], new[2])):
        moved = "  <-- moved" if abs(n_ - o) >= 0.02 else ""
        print(f"  {label:5s} {o*100:5.1f}% -> {n_*100:5.1f}%{moved}")
    bets = pending_bet_tokens()
    hit = [b for b in bets if sim["home"] in b and sim["away"] in b]
    for b in hit:
        print(f"  !! YOU HOLD A BET ON THIS MATCH: {b} "
              f"(${bets[b]['stake_usdc']})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=4)
    ap.add_argument("--what-if", help='e.g. "France:K. Mbappé"')
    args = ap.parse_args()

    if args.what_if:
        team, name = args.what_if.split(":", 1)
        mids = [mid for mid, s in sims.items() if team in (s["home"], s["away"])]
        mid = min(mids, key=lambda m: fixtures[m]["date_utc"])
        print(f"WHAT-IF: {name} ruled out of {team}'s next match")
        report(mid, [(team, name)])
        return

    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=args.days)
    upcoming = [mid for mid, f in fixtures.items()
                if now.isoformat() <= f["date_utc"] <= horizon.isoformat()]
    print(f"scanning injuries for {len(upcoming)} fixtures in next {args.days} days...")
    found = False
    for mid in sorted(upcoming, key=lambda m: fixtures[m]["date_utc"]):
        inj = api("injuries", fixture=mid)
        absent = [(r["team"]["name"], r["player"]["name"]) for r in inj]
        if absent:
            found = True
            report(mid, absent)
    if not found:
        print("no injuries listed yet (feed fills as kickoffs approach) — "
              "use --what-if to test the pipeline")


if __name__ == "__main__":
    main()
