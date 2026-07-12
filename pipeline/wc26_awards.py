"""Award predictions: Golden Boot, top-scoring nation, Golden Glove, Golden Ball.

  .venv/bin/python3 wc26_awards.py   ->  wc26_awards.json

Golden Boot / nation are MODELLED: each player's tournament goals are
simulated as Binomial(team goals in that simulated tournament, player's
share of team goals 2024-26), over the same 100k tournaments that produced
the futures (wc26_team_goals.npz). Ties split fractionally.
Golden Glove is a labelled heuristic (reach-final prob x defensive record).
Golden Ball is market-priced only (subjective award), shown with context.
"""
import json
import os
import re
import unicodedata

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

players = json.load(open(f"{DATA}/wc26_players.json"))
teams_meta = {t["country"]: t
              for t in json.load(open(f"{DATA}/fifa_world_cup_2026.json"))["teams"]}
fut = json.load(open(f"{DATA}/wc26_tournament.json"))["teams"]
# npz written by our own wc26_tournament.py; plain int16/unicode arrays,
# so no pickle needed
npz = np.load(f"{DATA}/wc26_team_goals.npz")
GOALS, TEAMS = npz["goals"], list(npz["teams"])
S = GOALS.shape[0]


def norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return s.lower()


# ---------------- Golden Boot ----------------
# The race must be conditioned on reality: ~100 tournament goals have real
# scorers. Re-simulating them from career-share priors kept "re-awarding"
# them by binomial draw (Haaland 23% while eliminated on 4 actual goals vs
# Messi's actual 7). Credit each candidate's REAL tally and simulate only
# the goals their team hasn't scored yet.
try:
    scorers = json.load(open(f"{DATA}/wc26_scorers.json"))["scorers"]
except FileNotFoundError:
    scorers = {}


def played_goals():
    """Goals each team has already scored this tournament (group + KO)."""
    gm = json.load(open(f"{DATA}/fifa_world_cup_2026_group_matches.json"))["matches"]
    try:
        ko = json.load(open(f"{DATA}/wc26_knockout_matches.json"))["matches"]
    except FileNotFoundError:
        ko = []
    out = {}
    for m in gm + ko:
        if not (str(m.get("status", "")).startswith("Match Finished")
                and m.get("score")):
            continue
        h, a = (int(x) for x in m["score"].split("-"))
        out[m["home"]] = out.get(m["home"], 0) + h
        out[m["away"]] = out.get(m["away"], 0) + a
    return out


PLAYED = played_goals()


def actual_tally(name, team):
    """This tournament's goals for a squad player — scorer names come from
    fixture events and squad names from the squads endpoint, so match on
    normalized full name first, then same-team last name."""
    n = norm(name)
    last = n.split()[-1]
    for k, v in scorers.items():
        if v.get("team") == team and (norm(k) == n
                                      or norm(k).split()[-1] == last):
            return v["goals"]
    return 0


cand_names, cand_teams, cand_shares, cand_meta, cand_actual = [], [], [], [], []
for team, squad in players["squads"].items():
    total = sum(p["goals"] for p in squad)
    if team not in TEAMS or total == 0:
        continue
    top = sorted(squad, key=lambda p: -p["goals"])[:6]
    for p in top:
        if p["goals"] == 0:
            continue
        share = 0.9 * p["goals"] / total   # 0.9: own-goals/squad rotation slack
        cand_names.append(p["name"])
        cand_teams.append(team)
        cand_shares.append(share)
        cand_actual.append(actual_tally(p["name"], team))
        cand_meta.append({"intl_goals": p["goals"], "apps": p["apps"],
                          "position": p["position"]})

rng = np.random.default_rng(2026)
C = len(cand_names)
mat = np.zeros((C, S), dtype=np.int16)
for i in range(C):
    tg = GOALS[:, TEAMS.index(cand_teams[i])]
    future = np.clip(tg - PLAYED.get(cand_teams[i], 0), 0, None)
    mat[i] = cand_actual[i] + rng.binomial(future, cand_shares[i])
mx = mat.max(axis=0)
winners = (mat == mx) & (mx > 0)
ties = winners.sum(axis=0)
ties[ties == 0] = 1
boot_p = (winners / ties).sum(axis=1) / S
exp_goals = mat.mean(axis=1)

mkt_boot = {norm(k): v for k, v in players["market"]["golden_boot"].items()}


def market_price(name):
    n = norm(name)
    if n in mkt_boot:
        return mkt_boot[n]
    last = n.split()[-1]
    hits = [v for k, v in mkt_boot.items() if k.split()[-1] == last]
    return hits[0] if len(hits) == 1 else None


order = np.argsort(-boot_p)
boot = []
for i in order[:25]:
    price = market_price(cand_names[i])
    boot.append({
        "player": cand_names[i], "team": cand_teams[i],
        "intl_goals_24_26": cand_meta[i]["intl_goals"],
        "exp_goals": round(float(exp_goals[i]), 2),
        "p_model": round(float(boot_p[i]), 4),
        "market": price,
        "edge": round(float(boot_p[i]) - price, 4) if price is not None else None,
    })

# ---------------- top scoring nation ----------------
mxn = GOALS.max(axis=1)
wn = (GOALS == mxn[:, None])
tn = wn.sum(axis=1).astype(float)
nation_p = (wn / tn[:, None]).sum(axis=0) / S
mkt_nation = {norm(k): v for k, v in players["market"]["top_scorer_nation"].items()}
nation = []
for i in np.argsort(-nation_p)[:15]:
    name = TEAMS[i]
    price = mkt_nation.get(norm(name))
    nation.append({"team": name, "exp_goals": round(float(GOALS[:, i].mean()), 2),
                   "p_model": round(float(nation_p[i]), 4), "market": price,
                   "edge": round(float(nation_p[i]) - price, 4) if price else None})

# ---------------- Golden Glove (heuristic) ----------------
glove = []
for team, squad in players["squads"].items():
    if team not in fut:
        continue
    gks = [p for p in squad if p["position"] == "Goalkeeper"]
    if not gks:
        continue
    gk = max(gks, key=lambda p: p["apps"])
    ms = teams_meta[team]["last_10_matches"]
    ga = sum(int(m["score"].split("-")[1]) for m in ms) / len(ms)
    glove.append({"player": gk["name"], "team": team,
                  "score": fut[team]["final"] / (0.5 + ga),
                  "p_final": fut[team]["final"], "conceded_pg": round(ga, 2)})
glove.sort(key=lambda g: -g["score"])
mkt_glove = {norm(k): v for k, v in players["market"]["golden_glove"].items()}
for g in glove:
    last = norm(g["player"]).split()[-1]
    hits = [v for k, v in mkt_glove.items() if k.split()[-1] == last]
    g["market"] = hits[0] if len(hits) == 1 else None
    g["score"] = round(g["score"], 3)

# ---------------- Golden Ball (market only) ----------------
ball = []
for name, price in players["market"]["golden_ball"].items():
    if name == "Other":
        continue
    team = None
    last = norm(name).split()[-1]
    best_goals = -1
    for tm, squad in players["squads"].items():
        for p in squad:
            if norm(p["name"]).split()[-1] == last and p["goals"] > best_goals:
                team, best_goals = tm, p["goals"]
    ball.append({"player": name, "market": price, "team": team,
                 "team_champion_p": fut.get(team, {}).get("champion") if team else None})
ball.sort(key=lambda b: -b["market"])

from wc26_simulate import now_utc, save_versioned
out = {
    "generated": now_utc(),
    "method": ("Boot/nation: Binomial player-share model over the 100k simulated "
               "tournaments; shares from international goals 2024-26. "
               "Glove: heuristic rank (reach-final prob x defensive record). "
               "Ball: Polymarket prices only."),
    "golden_boot": boot,
    "top_scorer_nation": nation,
    "golden_glove": glove[:15],
    "golden_ball": ball[:15],
    "prices_at": players["fetched_at"],
}
json.dump(out, open(f"{DATA}/wc26_awards.json", "w"), indent=2, ensure_ascii=False)
save_versioned(f"{DATA}/wc26_awards.json")
print("golden boot top 5:")
for b in boot[:5]:
    print(f"  {b['player']} ({b['team']}): {b['p_model']:.1%} model, "
          f"market {b['market']}, xG {b['exp_goals']}")
print("nation:", [(n['team'], n['p_model']) for n in nation[:3]])
print("glove lean:", [(g['player'], g['team']) for g in glove[:3]])
