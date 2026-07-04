# World Cup Predictions

Personal research tool for betting on FIFA World Cup 2026 (Polymarket). Static site
("The Form Book", https://wcformbook.com) generated from JSON data + a Dixon-Coles
simulation model.

## Layout

- `pipeline/` — all model/site scripts (run from repo root: `python3 pipeline/...`)
- `data/` — every JSON/CSV/NPZ input and canonical model output
- `docs/` — generated site, GitHub Pages root, never edit by hand
  (report.html = accuracy report, regraded nightly; archive/ = immutable snapshots)
- `betting/` — Polymarket executor (own README)
- `tests/` — gate suite in four layers (unit/integration/e2e/smoke, see
  tests/README.md; coverage via `.venv/bin/python3 -m coverage run -m
  unittest discover -s tests`); `runs/` — time-coded archives of every
  result write
- Nightly build runs in the cloud: `.github/workflows/matchday.yml`
  (scheduled GitHub Action, 2x/day UTC 05:00+21:00) reproduces the pipeline
  and pushes docs+data to main. Repo secrets: `API_FOOTBALL_KEY` (always),
  `ANTHROPIC_API_KEY` (the once-daily LLM refresh). Never bets. The 05:00
  run regenerates forward-looking LLM analysis for fixtures within 2 days
  (`wc26_llm.py generate --refresh-days 2`); past-match reviews stay frozen.
  `wc26_matchday.sh` is the legacy local launchd entry point (same pipeline,
  no LLM/betting) — kept as a manual/offline fallback

## Pipeline (order matters)

```
python3 pipeline/wc26_fetch.py        # refresh each team's last-10 matches (API-Football)
curl -sL https://raw.githubusercontent.com/martj42/international_results/master/results.csv \
     -o data/international_results.csv     # refresh match history for the model
python3 pipeline/wc26_simulate.py     # fit model, backtest, write data/wc26_simulations.json
python3 pipeline/wc26_corners.py predict    # corner O/U from the base-rate NegBin (after simulate)
python3 pipeline/wc26_totals_lock.py  # forward-lock O/U pre-kickoff -> wc26_totals_locked.json
                                      # (write-once per unplayed match; calibrated Totals scorecard)
python3 pipeline/wc26_elo.py          # Elo second opinion -> wc26_elo.json (after simulate;
                                      # display-only + betting tripwire; `tune`/`compare` subcommands)
python3 pipeline/wc26_half_split.py   # (occasional) refit half_split from API-Football HT scores
python3 pipeline/wc26_corners.py backfill && python3 pipeline/wc26_corners.py fit
                                      # (occasional) corners history + LOTO validation
python3 pipeline/wc26_simulate.py tune      # (occasional) re-tune hyperparams -> data/wc26_params.json
python3 pipeline/wc26_simulate.py gridtest  # exact-score calibration audit (17-cell log-loss)
python3 pipeline/wc26_simulate.py tuneboost # (occasional) refit min2_boost on training windows
.venv/bin/python3 pipeline/wc26_tournament.py  # 200-model ensemble + 100k sims (needs numpy venv)
                                      # -> data/wc26_tournament.json (futures) + wc26_predictions.json
                                      #    (locked bracket; --force to re-lock, normally never)
python3 pipeline/wc26_polymarket.py   # Polymarket prices incl. exact-score books (free, no key)
python3 pipeline/wc26_players.py      # squads + player intl goals + award market prices (~300 API calls)
python3 pipeline/wc26_player_pages.py # (occasional) candidate dossiers for /players pages (~140 calls)
python3 pipeline/wc26_espn_ids.py     # (once) ESPN gameId mapping for live links
.venv/bin/python3 pipeline/wc26_awards.py   # Golden Boot model etc (after tournament.py)
.venv/bin/python3 pipeline/wc26_llm.py sources   # (once) freeze LLM source corpus (wiki + internal)
.venv/bin/python3 pipeline/wc26_llm.py generate  # AI analyst sections (claude-opus-4-8) — MANUAL ONLY,
                                      # never in matchday automation (API cost); user runs it
python3 pipeline/wc26_update_results.py     # during tournament: pull actual results, grade predictions
python3 pipeline/wc26_build_site.py   # regenerate docs/ live pages
python3 pipeline/wc26_charts.py       # (occasional) regenerate method-page SVG charts
python3 -m unittest discover -s tests # gate: matchday script runs this before publishing
python3 pipeline/wc26_build_site.py snapshot  # ...plus freeze docs/archive/<date>/ (once per matchday)
python3 -m http.server 8742 --directory docs  # browse
```

## Data files (`data/`)

- `fifa_world_cup_2026.json` — 48 teams: confederation, FIFA ranking, last 10 matches
- `fifa_world_cup_2026_group_matches.json` — 72 group fixtures (match_id = API-Football fixture id)
- `wc26_params.json` — tuned model hyperparameters (incl. min2_boost, kept honest at 1.0)
- `wc26_simulations.json` — per-match probabilities (moneyline/totals 0.5-5.5/BTTS/spread/
  top scores + `exact_scores` 17-cell book + `team_totals` + `first_to_score` +
  `halftime`/`second_half` 1X2 via the fitted half_split)
- `wc26_corners.json` + `wc26_corners_model.json` + `wc26_corners_history.json` —
  total-corners NegBin (intercept-only: the xG slope failed LOTO validation)
- `wc26_tournament.json` — per-team futures probabilities (win group → champion)
- `wc26_totals_locked.json` — forward-locked O/U 2.5 (model/market/blend)
  for UNPLAYED fixtures (group + confirmed knockout ties; KO graded on the
  90-minute `score_90`), write-once + pre-kickoff only (no look-ahead);
  the report card's calibrated Totals scorecard grades these going forward.
  Kept separate from the locked bracket on purpose (totals weren't in the
  pre-tournament lock). `pipeline/wc26_totals_lock.py` writes it nightly;
  predict_bracket now also stores totals at lock time for a future clean lock
- `wc26_elo.json` — Elo second-opinion model: per-match moneyline +
  ratings + `disagreement` vs DC. The 50/50 blend FAILED same-set
  validation (0.831 vs DC 0.819) so Elo never feeds published numbers.
  Site shows only the disagreement FLAG on match pages (no Elo
  percentages — nothing to average in); the Elo figures + finding live on
  the method page. find_bets uses wide disagreement (>= elo_caution_pp)
  only to scale stakes down (reduce-only).
- `wc26_market_prices.json` — Polymarket snapshots: moneylines + exact-score books
  + totals/BTTS/spread (from the per-match `-more-markets` sibling event;
  re-fetch near kickoff; late-listed fixtures get picked up on re-run)
- `wc26_squad_values.json` — Transfermarkt squad values (refresh occasionally;
  pipeline/wc26_value_test.py revalidates beta)
- `wc26_llm_sources.json` — FROZEN source corpus for LLM analyses (wiki summaries
  fetched once + internal snapshots); `wc26_llm_analysis.json` — generated analyst
  sections (teams/players/match previews+reviews), write-once unless --force
- `wc26_predictions.json` — LOCKED bracket (see Conventions)
- `wc26_model_notes.md` (repo root) — method R&D, validation numbers, known blind spots

## Betting module (`betting/`)

- `run.py` — one-command entry point: refresh Polymarket snapshot →
  `find_bets.py` (plan from RAW-model edges vs live prices, Kelly-sized,
  sized to the bankroll REMAINING after the ledger) → `place_bets.py`
  (dry-run default, `--live` to execute, `--limit N`). Preflight refuses
  stale model outputs (`max_sims_age_hours`), an exhausted cap, and live
  mode without config.local.json. Uses the py-clob-client-v2 SDK
  (Polymarket migrated exchanges 2026-04-28).
- Categories are gated in config `include`; everything except `moneyline`
  and the two awards ships OFF in the committed config (a test enforces it:
  exact_score, totals, team_totals, btts, spread, halftime, second_half,
  first_to_score, futures, corners) — enable only via config.local.json. Started matches are never scanned
  (pre-match model vs in-play prices), and illiquid/placeholder books are
  rejected (`min_liquidity_usdc` / `max_book_spread` — untraded lines sit
  at ~50/50 and fake a fat edge).
- ALL personal betting data is gitignored and must stay that way:
  `betting/.env` (wallet key + API creds), `betting/config.local.json`
  (real caps), `betting/state/` (plans + ledger). The committed
  `betting/config.json` holds only generic placeholder caps.
- `news_check.py` — LLM news gate (opt-in: `run.py --news-check`, or
  standalone; `holdings` mode sell-flags open positions, never trades).
  Dossiers from API-Football injuries/lineups + Open-Meteo weather + web
  search; analyst returns clear/caution/veto per bet; code applies them
  reduce-only (veto removes, caution scales by `news_caution_factor`);
  edges over `news_big_edge_cents` get a why-does-the-market-disagree
  check. Analyst failure = plan unchanged (fail-open, loudly).
  Absence-adjusted moneylines stay advisory: `apply_lineup_adjustments`
  ships false (test-enforced) until the news_checks.json log proves them.
- The ledger enforces the total cap across runs and prevents double-betting
  (token- AND market-level, across runs AND within one batch); never edit
  it by hand. place_bets also refuses plans older than `max_plan_age_min`,
  re-checks kickoff per bet at execution, checks the live CLOB ask both
  ways (`max_slippage_cents` up / `max_price_drop_cents` down — a collapsed
  price is information, not a bargain), and verifies the wallet USDC
  balance before the first live order. config.local.json is deep-merged:
  its `include` overlays the committed gates instead of replacing them.
- Every find_bets scan logs all candidates to betting/state/paper.json
  (gitignored); `python3 betting/paper.py` grades CLV + resolved PnL per
  category — check it after a few matchdays before trusting any edge.

## Conventions

- Team names are normalized to the main JSON's spelling ("United States", "Turkey",
  "DR Congo", "South Korea"); API-Football and the history CSV use variants — alias
  maps exist in the scripts.
- Data sources: API-Football (Pro plan; key read from env API_FOOTBALL_KEY or the
  gitignored `.api_football_key` file at repo root) for fixtures/results;
  martj42/international_results for model training. Prefer free APIs; ask user for
  keys when needed.
- Mostly stdlib Python; `.venv/` holds numpy (wc26_tournament.py) and the
  anthropic SDK (wc26_llm.py) — everything else must stay stdlib.
- Anthropic API key for the analyst sections: env ANTHROPIC_API_KEY or the
  gitignored `.anthropic_key` at repo root (chmod 600). Site LLM output is
  colour, clearly labeled AI-written; it never feeds the model or the
  published numbers. The betting news gate (betting/news_check.py) is the
  one sanctioned LLM→betting path and it is reduce-only by construction:
  it can veto or shrink a planned bet, never add/raise one. User-triggered
  only (run.py --news-check or standalone) — never in matchday automation.
- `data/wc26_predictions.json` is LOCKED (pre-tournament bracket picks for accuracy
  grading). Only `wc26_update_results.py` may write to it (fills actuals);
  never regenerate it without the user explicitly asking (--force).
- Every result-writing script also archives a time-coded copy in `runs/`
  (via `save_versioned` in wc26_simulate.py). The site always shows the
  canonical "latest" files; run timestamps appear in the site footer.
- Group-match picks/probabilities are model×market blends (w=0.35 on
  Polymarket, log-opinion pool) where priced; `p_model`/`p_market` are kept
  alongside so the scorecard can grade model vs market vs blend separately.
