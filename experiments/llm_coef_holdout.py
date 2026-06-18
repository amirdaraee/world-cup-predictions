"""Test an LLM goal-rate coefficient on round 1, look-ahead-free.

Idea: let an LLM read each match's PRE-MATCH dossier (frozen pre-tournament
form, FIFA ranking, squad value, and our own DC model's expected goals +
1X2) and output a multiplicative coefficient on each team's expected goals —
the qualitative adjustment the goal model can't see (key absences it knows of,
style mismatch, motivation). Apply c_home/c_away to (lambda1, lambda2),
recompute the grid, and score 1X2 on the 24 played matches vs the unadjusted
model.

LOOK-AHEAD SAFEGUARDS (the whole experiment is worthless without these):
  - The DC model is fit at cutoff 2026-06-10 (CSV verified WC-blind).
  - claude-opus-4-8's training cutoff is Jan 2026 < the June 2026 tournament,
    so it cannot have memorized results.
  - The prompt carries ONLY pre-match data. No web search, no tools, no score,
    no post-match text. The model is told these are upcoming fixtures.
  - The raw LLM JSON is cached to experiments/llm_coef_cache.json so scoring
    re-runs don't re-bill and the exact coefficients are auditable.

Run with the venv that has the anthropic SDK:
  .venv/bin/python3 experiments/llm_coef_holdout.py
"""
import hashlib
import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pipeline"))
import wc26_simulate as S          # noqa: E402

DATA = S.DATA
CUTOFF = "2026-06-10"
CACHE = os.path.join(os.path.dirname(__file__), "llm_coef_cache.json")
MODEL = "claude-opus-4-8"
RES_IDX = {"H": 0, "D": 1, "A": 2}
COEF_LO, COEF_HI = 0.80, 1.20     # clamp the LLM can't exceed


def load_key():
    if os.environ.get("ANTHROPIC_API_KEY"):
        return os.environ["ANTHROPIC_API_KEY"]
    p = os.path.join(S.ROOT, ".anthropic_key")
    return open(p).read().strip()


def dossiers():
    teams = {t["country"]: t for t in
             json.load(open(f"{DATA}/fifa_world_cup_2026.json"))["teams"]}
    sv = json.load(open(f"{DATA}/wc26_squad_values.json"))
    vals, dflt = sv["values"], sv["default_for_missing"]
    fixtures = {str(m["match_id"]): m for m in
                json.load(open(f"{DATA}/fifa_world_cup_2026_group_matches.json"))["matches"]}
    pred = json.load(open(f"{DATA}/wc26_predictions.json"))

    def walk(o):
        out = []
        if isinstance(o, dict):
            if "actual_result" in o and "p_model" in o:
                out.append(o)
            for v in o.values():
                out += walk(v)
        elif isinstance(o, list):
            for v in o:
                out += walk(v)
        return out

    dc = S.fit(S.load_matches(CUTOFF, S.params()["half_life"],
                              S.params()["friendly_w"], S.params()["margin_cap"]),
               S.params()["shrink"])
    rho = S.params()["rho"]

    def form(name):
        t = teams.get(name, {})
        last = t.get("last_10_matches", [])[-6:]
        s = "; ".join(f"{m['result']} {m['score']} v {m['opponent']}"
                      f"({m['home_away']},{m.get('competition','')[:4]})" for m in last)
        return s or "no recent form on file"

    rows = []
    for r in walk(pred):
        fx = fixtures.get(str(r["match_id"]))
        if not fx:
            continue
        vc = S.CITY_COUNTRY.get(fx["city"], "United States")
        hf, af = r["home"] == vc, r["away"] == vc
        l1, l2 = S.lambdas(dc, r["home"], r["away"], hf)
        if af:
            l2a, l1a = S.lambdas(dc, r["away"], r["home"], True)
            l1, l2 = l1a, l2a
        pH, pD, pA = S.one_x_two(S.score_grid(l1, l2, rho))
        rows.append({
            "id": str(r["match_id"]), "home": r["home"], "away": r["away"],
            "result": r["actual_result"], "rho": rho, "l1": l1, "l2": l2,
            "dossier": {
                "fifa_rank": [teams.get(r["home"], {}).get("fifa_ranking"),
                              teams.get(r["away"], {}).get("fifa_ranking")],
                "squad_value_eur_m": [vals.get(r["home"], dflt),
                                      vals.get(r["away"], dflt)],
                "host_advantage": (r["home"] if hf else r["away"] if af else None),
                "home_form": form(r["home"]), "away_form": form(r["away"]),
                "model_xg": [round(l1, 2), round(l2, 2)],
                "model_1x2_pct": [round(pH * 100), round(pD * 100), round(pA * 100)],
            },
        })
    return rows


def build_prompt(rows):
    fixtures = []
    for i, r in enumerate(rows):
        d = r["dossier"]
        fixtures.append({
            "i": i, "home": r["home"], "away": r["away"],
            "fifa_rank_home_away": d["fifa_rank"],
            "squad_value_m_home_away": d["squad_value_eur_m"],
            "host_advantage_team": d["host_advantage"],
            "home_recent_form": d["home_form"],
            "away_recent_form": d["away_form"],
            "dc_model_expected_goals_home_away": d["model_xg"],
            "dc_model_win_draw_loss_pct": d["model_1x2_pct"],
        })
    instr = (
        "You are a football analyst adjusting a Dixon-Coles goal model for "
        "UPCOMING World Cup 2026 group fixtures. These matches have NOT been "
        "played. For each fixture, the model gives expected goals and a "
        "win/draw/loss split from team ratings alone — it cannot see squad "
        "depth nuance, tactical matchups, or motivation beyond what the form "
        "and ratings imply.\n\n"
        "For each fixture output two multiplicative coefficients on expected "
        f"goals, each in [{COEF_LO}, {COEF_HI}]: home_mult scales the home "
        "team's expected goals, away_mult the away team's. 1.0 = no change "
        "(use 1.0 unless you have a real reason). Base your judgment ONLY on "
        "the pre-match data provided plus general football knowledge up to "
        "your training cutoff. Do not assume any result.\n\n"
        "Return STRICT JSON only: {\"adj\":[{\"i\":0,\"home_mult\":1.0,"
        "\"away_mult\":1.0,\"why\":\"...\"}, ...]} with one entry per fixture, "
        "why <= 12 words.\n\nFIXTURES:\n" + json.dumps(fixtures, indent=1))
    return instr


def call_llm(rows):
    key = hashlib.sha256(build_prompt(rows).encode()).hexdigest()[:16]
    if os.path.exists(CACHE):
        c = json.load(open(CACHE))
        if c.get("key") == key:
            print(f"using cached LLM coefficients ({CACHE})")
            return c["adj"]
    import anthropic
    client = anthropic.Anthropic(api_key=load_key())
    print(f"calling {MODEL} for {len(rows)} coefficients (one request)...")
    msg = client.messages.create(
        model=MODEL, max_tokens=4000,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": build_prompt(rows)}],
    )
    text = "".join(b.text for b in msg.content if b.type == "text")
    start, end = text.find("{"), text.rfind("}") + 1
    adj = json.loads(text[start:end])["adj"]
    json.dump({"key": key, "model": MODEL, "adj": adj}, open(CACHE, "w"), indent=1)
    print(f"cached -> {CACHE}")
    return adj


def score(rows, get_mult):
    ll = brier = 0.0
    hits = n = 0
    for r in rows:
        ch, ca = get_mult(r)
        p = S.one_x_two(S.score_grid(r["l1"] * ch, r["l2"] * ca, r["rho"]))
        res = RES_IDX[r["result"]]
        ll -= math.log(max(p[res], 1e-9))
        for k in range(3):
            brier += (p[k] - (1.0 if k == res else 0.0)) ** 2
        hits += int(max(range(3), key=lambda k: p[k]) == res)
        n += 1
    return {"logloss": ll / n, "brier": brier / n, "hits": hits, "n": n}


def clamp(x):
    try:
        return max(COEF_LO, min(COEF_HI, float(x)))
    except (TypeError, ValueError):
        return 1.0


def main():
    rows = dossiers()
    adj = call_llm(rows)
    by_i = {a["i"]: a for a in adj}
    for i, r in enumerate(rows):
        a = by_i.get(i, {})
        r["ch"] = clamp(a.get("home_mult", 1.0))
        r["ca"] = clamp(a.get("away_mult", 1.0))
        r["why"] = a.get("why", "")

    base = score(rows, lambda r: (1.0, 1.0))
    llm = score(rows, lambda r: (r["ch"], r["ca"]))
    # half-strength variant: shrink the LLM nudge 50% toward 1.0 (reduce-only-ish)
    half = score(rows, lambda r: (1 + (r["ch"] - 1) / 2, 1 + (r["ca"] - 1) / 2))

    print(f"\n{len(rows)} matches; LLM coefficients applied to DC expected goals\n")
    print(f"{'variant':<22}{'log-loss':>9}{'Δ':>9}{'brier':>8}{'hit':>10}")
    print("-" * 58)
    for name, s in [("DC baseline", base), ("DC × LLM coef", llm),
                    ("DC × ½·LLM coef", half)]:
        d = "" if name == "DC baseline" else f"{s['logloss'] - base['logloss']:+.4f}"
        print(f"{name:<22}{s['logloss']:>9.4f}{d:>9}{s['brier']:>8.4f}"
              f"{s['hits']:>4}/{s['n']} {s['hits']/s['n']:>3.0%}")

    moved = [r for r in rows if abs(r["ch"] - 1) > 0.01 or abs(r["ca"] - 1) > 0.01]
    print(f"\nLLM moved {len(moved)}/{len(rows)} matches. Notable nudges:")
    for r in sorted(moved, key=lambda r: -(abs(r["ch"] - 1) + abs(r["ca"] - 1)))[:8]:
        print(f"  {r['home'][:13]:13} x{r['ch']:.2f} / {r['away'][:13]:13} "
              f"x{r['ca']:.2f}  (actual {r['result']}) — {r['why']}")


if __name__ == "__main__":
    main()
