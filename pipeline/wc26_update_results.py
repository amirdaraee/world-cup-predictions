"""Refresh actual WC2026 results and grade the locked predictions.

Run any time during the tournament:
  python3 wc26_update_results.py

- Pulls all World Cup fixtures from API-Football (league 1, season 2026).
- Updates scores/status in fifa_world_cup_2026_group_matches.json.
- Fills actuals into wc26_predictions.json and recomputes accuracy:
  group 1X2 hit rate, exact scores, Brier score on the locked probabilities,
  per-stage overlap of predicted vs actual qualifiers, champion check.
Never touches the locked predictions themselves.
"""
import json
import os
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")


def api_key():
    """Lazy: importing this module must work on key-less machines (tests)."""
    return os.environ.get("API_FOOTBALL_KEY") or \
        open(os.path.join(ROOT, ".api_football_key")).read().strip()

ALIAS = {
    "USA": "United States", "Korea Republic": "South Korea",
    "South Korea": "South Korea", "Türkiye": "Turkey", "Turkiye": "Turkey",
    "Congo DR": "DR Congo", "Bosnia & Herzegovina": "Bosnia and Herzegovina",
    "Cape Verde Islands": "Cape Verde", "Côte d'Ivoire": "Ivory Coast",
    "Cote D'Ivoire": "Ivory Coast",
}
FINISHED = {"FT", "AET", "PEN"}
KO_ROUNDS = {  # API round name fragment -> stage key as in predictions file
    "round of 32": "r16",      # winners of R32 reach R16
    "round of 16": "qf",
    "quarter": "sf",
    "semi": "final",
}


def norm(name):
    return ALIAS.get(name, name)


def fetch_all():
    url = "https://v3.football.api-sports.io/fixtures?league=1&season=2026"
    req = urllib.request.Request(url, headers={"x-apisports-key": api_key()})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)["response"]


def update_scorers(finished):
    """Tally tournament goalscorers from fixture events (Golden Boot race).
    Caches per-fixture events so each finished match is fetched once."""
    import time
    path = f"{DATA}/wc26_scorers.json"
    try:
        data = json.load(open(path))
    except FileNotFoundError:
        data = {"fetched_fixtures": [], "scorers": {}}
    done = set(data["fetched_fixtures"])
    new = [f for f in finished if f["fixture"]["id"] not in done]
    for f in new:
        fid = f["fixture"]["id"]
        url = f"https://v3.football.api-sports.io/fixtures/events?fixture={fid}"
        req = urllib.request.Request(url, headers={"x-apisports-key": api_key()})
        with urllib.request.urlopen(req, timeout=30) as r:
            events = json.load(r)["response"]
        for ev in events:
            if ev.get("type") != "Goal" or ev.get("detail") == "Missed Penalty":
                continue
            if ev.get("detail") == "Own Goal":
                continue   # own goals don't count toward the Golden Boot
            if "Penalty Shootout" in (ev.get("comments") or ""):
                continue
            player = (ev.get("player") or {}).get("name")
            team = norm((ev.get("team") or {}).get("name", ""))
            if not player:
                continue
            s = data["scorers"].setdefault(player, {"team": team, "goals": 0,
                                                    "assists_from": []})
            s["goals"] += 1
        done.add(fid)
        time.sleep(0.2)
    data["fetched_fixtures"] = sorted(done)
    data["scorers"] = dict(sorted(data["scorers"].items(),
                                  key=lambda kv: -kv[1]["goals"]))
    json.dump(data, open(path, "w"), indent=2, ensure_ascii=False)
    if new:
        print(f"scorers updated from {len(new)} newly finished matches; "
              f"race leader: {next(iter(data['scorers']), 'nobody yet')}")
    return data


def grade_group_matches(group_matches, by_id):
    """Fill actuals into the locked group picks and tally accuracy.

    Mutates each entry (actual_score / actual_result / hit) and returns
    {graded, res_hit, score_hit, mkt_n, briers, lls}. Pure apart from
    that mutation: fixtures come in via by_id, nothing is fetched."""
    import math
    res_hit = score_hit = graded = mkt_n = 0
    briers = {"blend": 0.0, "model": 0.0, "market": 0.0}
    lls = {"blend": 0.0, "model": 0.0, "market": 0.0}
    for p in group_matches:
        f = by_id.get(p["match_id"])
        if not f or f["fixture"]["status"]["short"] not in FINISHED:
            p.pop("actual_score", None)
            continue
        gh, ga = f["goals"]["home"], f["goals"]["away"]
        actual = "H" if gh > ga else "A" if ga > gh else "D"
        p["actual_score"] = f"{gh}-{ga}"
        p["actual_result"] = actual
        p["hit"] = p["pred_result"] == actual
        graded += 1
        res_hit += p["hit"]
        score_hit += p["pred_score"] == p["actual_score"]
        sources = {"blend": p["p"], "model": p.get("p_model")}
        if p.get("p_market"):
            tot = sum(p["p_market"].values())
            sources["market"] = {k: v / tot for k, v in p["p_market"].items()}
            mkt_n += 1
        for src, probs in sources.items():
            if not probs:
                continue
            briers[src] += sum((probs[k] - (1 if k == actual else 0)) ** 2
                               for k in ("H", "D", "A"))
            lls[src] -= math.log(max(probs[actual], 1e-9))
    return {"graded": graded, "res_hit": res_hit, "score_hit": score_hit,
            "mkt_n": mkt_n, "briers": briers, "lls": lls}


def knockout_actuals(finished):
    """Who actually reached each stage + the champion, from finished
    knockout fixtures. Winning the round-of-32 means reaching the R16,
    etc.; the 3rd-place playoff must never be mistaken for the final."""
    actual_stages = {k: set() for k in ("r16", "qf", "sf", "final")}
    champion = None
    for f in finished:
        rnd = f["league"]["round"].lower()
        h, a = norm(f["teams"]["home"]["name"]), norm(f["teams"]["away"]["name"])
        winner = h if f["teams"]["home"].get("winner") else \
            a if f["teams"]["away"].get("winner") else None
        for frag, stage in KO_ROUNDS.items():
            if frag in rnd and winner:
                actual_stages[stage].add(winner)
        if "final" in rnd and "semi" not in rnd and "3rd" not in rnd \
                and "third" not in rnd and winner:
            champion = winner
    return actual_stages, champion


def main():
    fixtures = fetch_all()
    finished = [f for f in fixtures
                if f["fixture"]["status"]["short"] in FINISHED]
    print(f"{len(fixtures)} WC fixtures known, {len(finished)} finished")
    update_scorers(finished)

    # ---- knockout fixtures (appear with real teams once the R32 is set) ----
    ko = []
    for f in fixtures:
        rnd = f["league"]["round"]
        if "Group" in rnd:
            continue
        h = norm(f["teams"]["home"]["name"] or "")
        a = norm(f["teams"]["away"]["name"] or "")
        if not h or not a or "winner" in h.lower() or "winner" in a.lower():
            continue   # placeholder pairings, teams not decided yet
        played = f["fixture"]["status"]["short"] in FINISHED
        ko.append({
            "match_id": f["fixture"]["id"], "round": rnd,
            "date_utc": f["fixture"]["date"],
            "home": h, "away": a,
            "venue": (f["fixture"]["venue"] or {}).get("name", ""),
            "city": (f["fixture"]["venue"] or {}).get("city", ""),
            "status": f["fixture"]["status"]["long"],
            "score": (f"{f['goals']['home']}-{f['goals']['away']}"
                      if played else None),
            # 'goals' includes extra time; markets (O/U, moneyline) settle on
            # the 90-minute score, which the API keeps in score.fulltime
            "score_90": (f"{f['score']['fulltime']['home']}-{f['score']['fulltime']['away']}"
                         if played and f["score"]["fulltime"]["home"] is not None
                         else None),
            "penalties": (f"{f['score']['penalty']['home']}-{f['score']['penalty']['away']}"
                          if played and f["score"]["penalty"]["home"] is not None
                          else None),
        })
    ko.sort(key=lambda m: m["date_utc"])
    json.dump({"updated": __import__("time").strftime("%Y-%m-%d %H:%M UTC",
                                                      __import__("time").gmtime()),
               "matches": ko},
              open(f"{DATA}/wc26_knockout_matches.json", "w"),
              indent=2, ensure_ascii=False)
    print(f"knockout fixtures with confirmed teams: {len(ko)}")

    # ---- refresh group matches file ----
    gm_path = f"{DATA}/fifa_world_cup_2026_group_matches.json"
    gm = json.load(open(gm_path))
    by_id = {f["fixture"]["id"]: f for f in fixtures}
    for m in gm["matches"]:
        f = by_id.get(m["match_id"])
        if f:
            m["status"] = f["fixture"]["status"]["long"]
            if f["fixture"]["status"]["short"] in FINISHED:
                m["score"] = f"{f['goals']['home']}-{f['goals']['away']}"
    json.dump(gm, open(gm_path, "w"), indent=2, ensure_ascii=False)

    # ---- grade predictions ----
    pred_path = f"{DATA}/wc26_predictions.json"
    if not os.path.exists(pred_path):
        print("no locked predictions file; run wc26_tournament.py first")
        return
    pred = json.load(open(pred_path))

    g = grade_group_matches(pred["group_matches"], by_id)
    graded, res_hit, score_hit, mkt_n = \
        g["graded"], g["res_hit"], g["score_hit"], g["mkt_n"]
    briers, lls = g["briers"], g["lls"]

    actual_stages, champion = knockout_actuals(finished)

    stage_acc = {}
    for stage, actual in actual_stages.items():
        if actual:
            predicted = set(pred["predicted_stage_teams"][stage])
            stage_acc[stage] = {
                "predicted_correct": len(predicted & actual),
                "of": len(predicted),
                "actual_so_far": len(actual),
            }

    pred["actuals"] = {
        "stages": {k: sorted(v) for k, v in actual_stages.items() if v},
        "champion": champion,
    }
    pred["accuracy"] = {
        "graded_group_matches": graded,
        "result_hits": res_hit,
        "result_pct": round(res_hit / graded, 4) if graded else None,
        "exact_score_hits": score_hit,
        "exact_score_pct": round(score_hit / graded, 4) if graded else None,
        "brier": round(briers["blend"] / graded, 4) if graded else None,
        "compare": ({src: {"brier": round(briers[src] / n, 4),
                           "logloss": round(lls[src] / n, 4)}
                     for src, n in (("blend", graded), ("model", graded),
                                    ("market", mkt_n)) if n}
                    if graded else None),
        "market_priced_matches": mkt_n,
        "stages": stage_acc,
        "champion_correct": (champion == pred["champion"]) if champion else None,
    }
    json.dump(pred, open(pred_path, "w"), indent=2, ensure_ascii=False)
    from wc26_simulate import save_versioned
    save_versioned(pred_path)
    acc = pred["accuracy"]
    if graded:
        print(f"graded {graded} group matches: "
              f"{res_hit} results ({acc['result_pct']:.0%}), "
              f"{score_hit} exact scores")
        for src, v in acc["compare"].items():
            print(f"  {src:7s} brier {v['brier']}  log-loss {v['logloss']}")
    else:
        print("no finished matches yet — nothing to grade")
    print("rebuild the site to refresh the Bracket page")


if __name__ == "__main__":
    main()
