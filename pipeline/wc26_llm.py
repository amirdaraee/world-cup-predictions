"""LLM analyst sections for The Form Book (needs .venv: anthropic SDK).

  .venv/bin/python3 pipeline/wc26_llm.py sources    # freeze the source corpus
                                                    # (wiki summaries fetched ONCE)
  .venv/bin/python3 pipeline/wc26_llm.py generate   # fill missing analyses
      [--only teams|players|matches] [--limit N] [--force]
      [--refresh-days N]   # also REGENERATE forward-looking analysis for
                           # fixtures kicking off within N days (the daily
                           # cloud refresh runs this so previews/team/player
                           # notes track the latest refit model + results)

Grounding contract: every generation sees ONLY the frozen source dossier
plus our own model calculations — same inputs, reproducible outputs, no
invented facts. Output store: data/wc26_llm_analysis.json
  {teams: {name: {...}}, players: {name: {...}},
   matches: {mid: {preview: {...}, review: {...}}}}
Each entry: {text, generated, model}. Reviews are generated only after a
result exists and are NEVER regenerated (a finished match's verdict is
frozen — even --refresh-days leaves it alone); previews/teams/players are
write-once unless --force or in the --refresh-days upcoming window.

Key: ANTHROPIC_API_KEY env var or gitignored .anthropic_key at repo root.
Cost: ~190 short generations with claude-opus-4-8; the frozen system
prompt is cache_control'd so sequential runs hit the prompt cache.
"""
import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

MODEL = "claude-opus-4-8"
SOURCES = f"{DATA}/wc26_llm_sources.json"
STORE = f"{DATA}/wc26_llm_analysis.json"

# Wikipedia titles that don't follow "<team> national football team"
WIKI_TITLES = {
    "United States": "United States men's national soccer team",
    "Iran": "Iran national football team",
}

SYSTEM = """You are the resident analyst for The Form Book (wcformbook.com), \
a statistical research site covering the 2026 FIFA World Cup. The site's voice \
is plain-language, numbers-grounded and honest: a Dixon-Coles goal model and a \
100,000-simulation tournament ensemble produce probabilities, Polymarket \
prices sit beside them, and every prediction is graded in public.

You write short analysis sections for the site. Rules, in order of priority:
1. GROUNDING: use ONLY the facts in the dossier you are given. Never add \
players, results, injuries, history or any fact not present in it. If the \
dossier is thin, write less rather than inventing.
2. Probabilities are not certainties. When the model and the betting market \
disagree, say so and treat it as a genuine open question, never as proof \
either way.
3. Voice: confident, concrete, readable. Weave in 2-4 of the most telling \
numbers (percentages as percentages, prices in cents where given). No bullet \
lists, no headings, no hedging boilerplate, no "as an AI". Write flowing \
prose paragraphs.
4. Length: respect the word budget given in the request.
5. For match REVIEWS: lead with what actually happened, then how it compared \
to what the model and market expected — credit good calls and admit misses \
plainly."""


# ---------------- key / client ----------------
def api_key():
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        try:
            key = open(f"{ROOT}/.anthropic_key").read().strip()
        except FileNotFoundError:
            return None
    return key or None


def client():
    import anthropic
    return anthropic.Anthropic(api_key=api_key())


# ---------------- source corpus (frozen) ----------------
def wiki_summary(team):
    title = WIKI_TITLES.get(team, f"{team} national football team")
    url = ("https://en.wikipedia.org/api/rest_v1/page/summary/"
           + urllib.parse.quote(title.replace(" ", "_")))
    req = urllib.request.Request(url, headers={"User-Agent": "wcformbook.com research"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r).get("extract", "")
    except Exception as e:
        print(f"  wiki miss {team}: {e}", flush=True)
        return ""


def build_sources(force=False):
    """Freeze the per-team corpus: one-time wiki summary + internal data
    snapshot. Internal data is versioned by our own pipeline anyway; the
    wiki text is what must not drift between generations."""
    try:
        src = json.load(open(SOURCES))
    except FileNotFoundError:
        src = {"frozen_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
               "teams": {}}
    teams = json.load(open(f"{DATA}/fifa_world_cup_2026.json"))["teams"]
    vals = json.load(open(f"{DATA}/wc26_squad_values.json"))["values"]
    squads = json.load(open(f"{DATA}/wc26_players.json"))["squads"]
    for t in teams:
        name = t["country"]
        # empty wiki = an earlier rate-limited fetch; retry those
        if name in src["teams"] and src["teams"][name].get("wiki") \
                and not force:
            continue
        scorers = sorted(squads.get(name, []),
                         key=lambda p: -(p.get("intl_goals") or 0))[:8]
        src["teams"][name] = {
            "wiki": wiki_summary(name),
            "confederation": t["confederation"],
            "fifa_ranking": t["fifa_ranking"],
            "host": t.get("host", False),
            "last_10": [f"{m['date']} {m['result']} {m['score']} "
                        f"vs {m['opponent']} ({m['home_away']}, "
                        f"{m['competition']})"
                        for m in t.get("last_10_matches", [])],
            "squad_value_eur_m": vals.get(name),
            "key_players": [
                {"name": p["name"], "position": p.get("position"),
                 "intl_goals": p.get("intl_goals")} for p in scorers],
        }
        print(f"  sourced {name}", flush=True)
        time.sleep(1.2)   # wikipedia REST rate limit
    json.dump(src, open(SOURCES, "w"), indent=1, ensure_ascii=False)
    print(f"froze sources for {len(src['teams'])} teams -> {SOURCES}")


# ---------------- prompt builders (pure; unit-tested) ----------------
def team_calcs(name):
    tourney = json.load(open(f"{DATA}/wc26_tournament.json"))["teams"]
    return tourney.get(name, {})


def build_team_prompt(name, source, calcs):
    dossier = {"team": name, **source,
               "model_tournament_probabilities": calcs}
    return (f"Write the analysis section for the {name} team page. "
            f"Word budget: 150-200 words, two paragraphs.\n\nDOSSIER:\n"
            + json.dumps(dossier, ensure_ascii=False, indent=1))


def build_player_prompt(name, profile, team_source):
    dossier = {"player": name, "profile": profile,
               "team_context": {k: team_source.get(k) for k in
                                ("fifa_ranking", "confederation")}}
    return (f"Write the analysis section for the {name} player page. "
            f"Word budget: 120-180 words, one or two paragraphs. Focus on "
            f"club form by season and what it implies for the tournament.\n\n"
            f"DOSSIER:\n" + json.dumps(dossier, ensure_ascii=False, indent=1))


def _match_dossier(m, sim, mkt, src):
    compact = {}
    for side in (m["home"], m["away"]):
        s = src["teams"].get(side, {})
        compact[side] = {k: s.get(k) for k in
                         ("fifa_ranking", "confederation", "last_10",
                          "squad_value_eur_m", "key_players")}
    return {
        "fixture": f"{m['home']} v {m['away']}, "
                   f"{'group ' + m['group'] if m.get('group') else m.get('round', '')}, "
                   f"{m['date_utc'][:10]}, {m.get('venue')} ({m.get('city')})",
        "model": {k: sim.get(k) for k in
                  ("xg", "moneyline", "totals", "btts", "first_to_score",
                   "halftime", "top_scores")},
        "polymarket": {k: mkt.get(k) for k in ("moneyline", "totals", "btts")
                       if mkt.get(k) is not None},
        "teams": compact,
    }


def build_preview_prompt(m, sim, mkt, src):
    return (f"Write the pre-match analysis for {m['home']} v {m['away']}. "
            f"Word budget: 160-220 words, two paragraphs. Cover: the shape "
            f"of the matchup, what the model expects and why, and where (if "
            f"anywhere) the model and the market disagree.\n\nDOSSIER:\n"
            + json.dumps(_match_dossier(m, sim, mkt, src),
                         ensure_ascii=False, indent=1))


def build_review_prompt(m, sim, mkt, src, pred):
    d = _match_dossier(m, sim, mkt, src)
    # the live snapshot may hold post-settlement prices (1.0/0/0) — only the
    # pre-match prices preserved in the locked predictions file are honest
    # inputs for grading the market's view
    if pred.get("p_market"):
        d["polymarket"] = {"prematch_moneyline_HDA": pred["p_market"]}
    else:
        d.pop("polymarket", None)
    d["result"] = {"final_score": m.get("score"),
                   "locked_bracket_pick": {k: pred.get(k) for k in
                                           ("pred_score", "pred_result",
                                            "hit", "actual_result")
                                           if pred.get(k) is not None}}
    return (f"The match {m['home']} v {m['away']} has finished "
            f"{m.get('score')}. Write the post-match review. Word budget: "
            f"150-200 words, two paragraphs. Lead with what happened, then "
            f"grade the model's and the market's pre-match views honestly."
            f"\n\nDOSSIER:\n" + json.dumps(d, ensure_ascii=False, indent=1))


# ---------------- generation ----------------
def generate_one(cl, prompt, attempts=4):
    """One analysis, with backoff on transient API errors (429/529/5xx).
    The API being briefly overloaded must not abort a whole refresh run."""
    import anthropic
    for i in range(attempts):
        try:
            resp = cl.messages.create(
                model=MODEL,
                max_tokens=2000,
                thinking={"type": "adaptive"},
                system=[{"type": "text", "text": SYSTEM,
                         "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": prompt}],
            )
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
            status = getattr(e, "status_code", None)
            transient = isinstance(e, anthropic.APIConnectionError) or \
                status in (429, 500, 502, 503, 529)
            if not transient or i == attempts - 1:
                raise
            wait = 3 * (2 ** i)   # 3s, 6s, 12s
            print(f"  transient API error ({status or type(e).__name__}); "
                  f"retry {i + 1}/{attempts - 1} in {wait}s", flush=True)
            time.sleep(wait)
            continue
        if resp.stop_reason == "refusal":
            print("  refused — skipped", flush=True)
            return None
        return next((b.text for b in resp.content if b.type == "text"), None)


def load_store():
    try:
        return json.load(open(STORE))
    except FileNotFoundError:
        return {"model": MODEL, "teams": {}, "players": {}, "matches": {}}


def save_store(store):
    store["model"] = MODEL
    store["updated"] = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    json.dump(store, open(STORE, "w"), indent=1, ensure_ascii=False)


def entry(text):
    return {"text": text, "model": MODEL,
            "generated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())}


def upcoming_window(fixtures, now, days):
    """(match_ids, team names) for UNPLAYED fixtures kicking off within
    `days` of now — the daily-refresh scope. Pure: no I/O."""
    horizon = now + timedelta(days=days)
    mids, teams = set(), set()
    for m in fixtures:
        if m.get("score"):
            continue
        try:
            ko = datetime.fromisoformat(m["date_utc"])
        except (ValueError, KeyError, TypeError):
            continue
        if now <= ko <= horizon:
            mids.add(str(m["match_id"]))
            teams.add(m["home"])
            teams.add(m["away"])
    return mids, teams


def generate(only=None, limit=None, force=False, refresh_days=0):
    if not api_key():
        print("no ANTHROPIC_API_KEY / .anthropic_key — skipping LLM generation")
        return
    src = json.load(open(SOURCES))
    store = load_store()
    cl = client()
    sims = json.load(open(f"{DATA}/wc26_simulations.json"))["simulations"]
    prices = json.load(open(f"{DATA}/wc26_market_prices.json"))["prices"]
    fixtures = json.load(
        open(f"{DATA}/fifa_world_cup_2026_group_matches.json"))["matches"]
    try:   # knockout ties get previews/reviews too once teams are known
        fixtures = fixtures + json.load(
            open(f"{DATA}/wc26_knockout_matches.json"))["matches"]
    except FileNotFoundError:
        pass
    pred_by_mid = {}
    try:
        pred = json.load(open(f"{DATA}/wc26_predictions.json"))
        pred_by_mid = {str(p.get("match_id")): p
                       for p in pred.get("group_matches", [])}
    except FileNotFoundError:
        pass

    # daily refresh scope: forward-looking analysis for imminent fixtures
    refresh_mids, refresh_teams = (
        upcoming_window(fixtures, datetime.now(timezone.utc), refresh_days)
        if refresh_days else (set(), set()))

    todo = []   # (kind, key, prompt)
    if only in (None, "teams"):
        for name, s in src["teams"].items():
            if force or name not in store["teams"] or name in refresh_teams:
                todo.append(("teams", name,
                             build_team_prompt(name, s, team_calcs(name))))
    if only in (None, "players"):
        profiles = json.load(
            open(f"{DATA}/wc26_player_profiles.json"))["profiles"]
        for name, prof in profiles.items():
            if force or name not in store["players"] \
                    or prof.get("team") in refresh_teams:
                team_src = src["teams"].get(prof.get("team"), {})
                todo.append(("players", name,
                             build_player_prompt(name, prof, team_src)))
    if only in (None, "matches"):
        for m in fixtures:
            mid = str(m["match_id"])
            sim = sims.get(mid, {})
            mkt = prices.get(mid, {})
            slot = store["matches"].setdefault(mid, {})
            # a preview written AFTER the result would be look-ahead dressed
            # up as foresight — played matches only ever get reviews
            if not m.get("score") and \
                    (force or "preview" not in slot or mid in refresh_mids):
                todo.append(("preview", mid,
                             build_preview_prompt(m, sim, mkt, src)))
            if m.get("score") and (force or "review" not in slot):
                todo.append(("review", mid,
                             build_review_prompt(m, sim, mkt, src,
                                                 pred_by_mid.get(mid, {}))))
    if limit:
        todo = todo[:limit]
    print(f"{len(todo)} analyses to generate with {MODEL}")
    done = failed = 0
    for kind, key, prompt in todo:
        try:
            text = generate_one(cl, prompt)
        except Exception as e:   # one stubborn item must not abort the run
            failed += 1
            print(f"  SKIP {kind}: {key} — {type(e).__name__}: {e}",
                  flush=True)
            continue
        if not text:
            continue
        if kind == "teams":
            store["teams"][key] = entry(text)
        elif kind == "players":
            store["players"][key] = entry(text)
        elif kind == "preview":
            store["matches"].setdefault(key, {})["preview"] = entry(text)
        else:
            store["matches"].setdefault(key, {})["review"] = entry(text)
        done += 1
        save_store(store)   # checkpoint: a crash loses at most one call
        print(f"  [{done}/{len(todo)}] {kind}: {key}", flush=True)
    if done:
        from wc26_simulate import save_versioned
        save_versioned(STORE)
    print(f"generated {done} analyses"
          + (f", {failed} skipped after retries" if failed else "")
          + f" -> {STORE}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["sources", "generate"])
    ap.add_argument("--only", choices=["teams", "players", "matches"])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--refresh-days", type=int, default=0,
                    help="also regenerate forward-looking analysis for "
                         "fixtures within N days (reviews stay frozen)")
    a = ap.parse_args()
    if a.cmd == "sources":
        build_sources(force=a.force)
    else:
        generate(only=a.only, limit=a.limit, force=a.force,
                 refresh_days=a.refresh_days)
