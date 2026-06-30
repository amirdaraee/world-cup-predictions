"""Map our 72 fixtures to ESPN gameIds for live-score deep links.

  python3 wc26_espn_ids.py  ->  wc26_espn_ids.json

Free ESPN scoreboard API, no key. Matches on kickoff datetime + team names.
"""
import json
import os
import time
import unicodedata
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
ALIAS = {"czechia": "czech republic", "usa": "united states",
         "bosnia-herzegovina": "bosnia and herzegovina",
         "dr congo": "dr congo", "congo dr": "dr congo",
         "democratic republic of the congo": "dr congo", "congo": "dr congo",
         "turkiye": "turkey", "south korea": "south korea",
         "ivory coast": "ivory coast", "cape verde islands": "cape verde",
         "bosnia and herzegovina": "bosnia and herzegovina"}


def norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return ALIAS.get(s, s)


fixtures = json.load(open(f"{DATA}/fifa_world_cup_2026_group_matches.json"))["matches"]
try:
    fixtures += json.load(open(f"{DATA}/wc26_knockout_matches.json"))["matches"]
except FileNotFoundError:
    pass
days = sorted({m["date_utc"][:10].replace("-", "") for m in fixtures})
# keep ids already mapped so a transient ESPN/TLS blip on one run never loses
# them — and, since these are only live-score deep links, never fails the
# matchday publish over them (best-effort: skip a day we can't fetch)
try:
    out = json.load(open(f"{DATA}/wc26_espn_ids.json")).get("ids", {})
except (FileNotFoundError, ValueError):
    out = {}
events = []
for day in days:
    url = ("https://site.api.espn.com/apis/site/v2/sports/soccer/"
           f"fifa.world/scoreboard?dates={day}")
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            events += json.load(r).get("events", [])
    except Exception as e:                       # SSL/network/timeout — non-fatal
        print(f"  skip {day} (transient: {e})")
    time.sleep(0.2)

seen = {}
for e in events:
    teams = frozenset(norm(c["team"]["displayName"])
                      for c in e["competitions"][0]["competitors"])
    seen[(e["date"].replace("Z", ""), teams)] = e["id"]

missing = []
for m in fixtures:
    key = (m["date_utc"][:16].replace("+00:00", ""),
           frozenset((norm(m["home"]), norm(m["away"]))))
    # espn dates are minute-precision "2026-06-11T19:00"
    eid = seen.get((m["date_utc"][:16], key[1])) or seen.get(key)
    if eid:
        out[str(m["match_id"])] = eid            # merge over any prior mapping
    elif str(m["match_id"]) not in out:
        missing.append(f'{m["home"]} v {m["away"]} {m["date_utc"]}')

json.dump({"fetched_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
           "ids": out}, open(f"{DATA}/wc26_espn_ids.json", "w"), indent=2)
print(f"mapped {len(out)}/{len(fixtures)}")
for x in missing[:8]:
    print("  missing:", x)
