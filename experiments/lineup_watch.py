"""EXPERIMENT: starting-lineup rotation watcher.

  python3 experiments/lineup_watch.py                  # today's WC fixtures
  python3 experiments/lineup_watch.py --fixture 1489369
  python3 experiments/lineup_watch.py --test Mexico    # prove the plumbing
                                                       # on a finished match

Lineups appear ~40 min before kickoff. Each team's announced XI is compared
to its expected first-choice XI (top-11 by international appearances).
Heavy rotation (dead rubbers, B-teams) shifts win probabilities before
slow corners of the market react — that's the window this watches.
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from wc26_simulate import params, score_grid, one_x_two  # noqa: E402

KEY = os.environ.get("API_FOOTBALL_KEY") or \
    open(f"{ROOT}/.api_football_key").read().strip()
P = params()
players = json.load(open(f"{ROOT}/wc26_players.json"))["squads"]
ids = {name: d["team_id"] for name, d in
       json.load(open(f"{ROOT}/wc26_matches.json")).items()}
sims = json.load(open(f"{ROOT}/wc26_simulations.json"))["simulations"]
fixtures = {str(m["match_id"]): m for m in json.load(
    open(f"{ROOT}/fifa_world_cup_2026_group_matches.json"))["matches"]}


def api(path, **q):
    url = f"https://v3.football.api-sports.io/{path}?" + urllib.parse.urlencode(q)
    req = urllib.request.Request(url, headers={"x-apisports-key": KEY})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["response"]


def expected_xi(team):
    squad = sorted(players.get(team, []), key=lambda p: -p["apps"])
    return {p["name"] for p in squad[:11]}


def goal_share(team, names):
    squad = players.get(team, [])
    total = sum(p["goals"] for p in squad) or 1
    return sum(p["goals"] for p in squad if p["name"] in names) / total


def analyse_lineup(team, starters):
    exp = expected_xi(team)
    rotated_out = exp - starters
    missing_share = goal_share(team, rotated_out)
    return rotated_out, missing_share


def check_fixture(fid, lineups):
    sim = sims.get(str(fid))
    print(f"\n== fixture {fid}"
          + (f": {sim['home']} v {sim['away']}" if sim else "") + " ==")
    docks = {}
    for lu in lineups:
        team = lu["team"]["name"]
        starters = {p["player"]["name"] for p in lu["startXI"]}
        rotated, share = analyse_lineup(team, starters)
        docks[team] = 1 - 0.5 * share
        print(f"  {team}: {len(rotated)} expected starters missing"
              + (f" ({', '.join(sorted(rotated))})" if rotated else "")
              + f" — {share:.0%} of team goals rotated out")
        if len(rotated) >= 4:
            print(f"  !! HEAVY ROTATION: {team} looks like a B-team today")
    if sim and docks:
        l1 = sim["xg"]["home"] * docks.get(sim["home"], 1)
        l2 = sim["xg"]["away"] * docks.get(sim["away"], 1)
        old = one_x_two(score_grid(sim["xg"]["home"], sim["xg"]["away"], P["rho"]))
        new = one_x_two(score_grid(l1, l2, P["rho"]))
        for label, o, n in (("home", old[0], new[0]), ("draw", old[1], new[1]),
                            ("away", old[2], new[2])):
            moved = "  <-- moved" if abs(n - o) >= 0.02 else ""
            print(f"  {label:5s} {o*100:5.1f}% -> {n*100:5.1f}%{moved}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", type=int)
    ap.add_argument("--test", help="team name: parse their last finished match")
    args = ap.parse_args()

    if args.test:
        import unicodedata
        norm = lambda s: unicodedata.normalize("NFC", s).lower()
        team = next(t for t in ids if norm(t) == norm(args.test))
        args.test = team
        last = api("fixtures", team=ids[team], last=1)[0]
        fid = last["fixture"]["id"]
        print(f"TEST MODE: last finished match of {args.test} — "
              f"{last['teams']['home']['name']} v {last['teams']['away']['name']} "
              f"({last['fixture']['date'][:10]})")
        lus = api("fixtures/lineups", fixture=fid)
        if not lus:
            print("no lineup data for that fixture")
            return
        check_fixture(fid, lus)
        return

    fids = [args.fixture] if args.fixture else [
        int(mid) for mid, f in fixtures.items()
        if f["date_utc"][:10] == datetime.now(timezone.utc).strftime("%Y-%m-%d")]
    if not fids:
        print("no WC fixtures today")
    for fid in fids:
        lus = api("fixtures/lineups", fixture=fid)
        if lus:
            check_fixture(fid, lus)
        else:
            print(f"fixture {fid}: lineups not announced yet "
                  f"({fixtures[str(fid)]['home']} v {fixtures[str(fid)]['away']}, "
                  f"{fixtures[str(fid)]['date_utc'][11:16]} UTC)")


if __name__ == "__main__":
    main()
