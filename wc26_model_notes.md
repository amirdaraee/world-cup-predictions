# WC26 Simulation — Method Notes

## Methods considered

| Method | Verdict |
|---|---|
| Plain Poisson (Maher 1982) | Too crude — misprices draws and low scores |
| **Dixon-Coles (1997)** | **Chosen.** Industry baseline; attack/defense ratings + low-score correction; fits in seconds |
| Elo→goals hybrids (538 SPI style) | Comparable accuracy, more moving parts, no public Elo feed needed anyway |
| ML (boosting/NN) | Needs player-level features to beat DC; overkill for laptop + group stage |
| LLM-based prediction | Rejected: expensive, unverifiable, no calibrated probabilities |

## Model

- Data: martj42/international_results (49k matches), completed internationals 2018 → present (`international_results.csv`).
- Weighted Poisson MLE per team (attack + defense), fixed-point iteration, pure stdlib Python.
- Time decay: 18-month half-life. Friendlies weighted ×0.6.
- Shrinkage: 8 pseudo-matches toward average (protects thin-data teams: Curaçao, Haiti, NZ).
- Dixon-Coles ρ = −0.10 low-score adjustment on the 13×13 score grid (exact probabilities, no Monte Carlo noise).
- Home advantage (+0.277 log-goals, fitted) applied only when a team plays in its own country (USA/Mexico/Canada fixtures at home venues).

## Validation (held-out)

Fit on data to 2025-06-01, tested on the following 1,071 internationals:
log-loss **0.897** vs 1.046 frequency baseline · Brier 0.528 · accuracy 59.9% vs 48.5% baseline.

## Outputs (Polymarket categories)

Per match (`wc26_simulations.json`, rendered on match pages): moneyline (home/draw/away),
totals O/U 1.5/2.5/3.5, BTTS yes/no, spread ±1.5, top-5 scorelines, expected goals.
Probabilities double as fair prices in ¢ (Polymarket shares settle at $1).

## Known blind spots

No injuries/suspensions/lineups, no motivation effects (dead rubbers on matchday 3),
no weather/altitude, tournament friendlies in form data can be soft (e.g. "Ghana B").
Treat as a fair-value anchor, not an oracle.

## Pipeline

```
python3 wc26_fetch.py        # refresh team last-10 (API-Football)
curl ... international_results.csv   # refresh history (see notes)
python3 wc26_simulate.py     # backtest + 72 simulations
python3 wc26_build_site.py   # regenerate site
```

---

## Upgrade pass (2026-06-09, evening)

**Hyperparameter tuning** (coordinate descent, held-out 2025-06→2026-06):
half-life 548→**1000d**, friendlies ×0.6→**×0.8**, shrinkage 8→**4**, ρ −0.10→**−0.05**.
Log-loss **0.897 → 0.854**. Longer memory + lighter shrinkage won: international teams
are more stable than club sides. Caveat: tuned on a single 1,071-match window.

**Tournament Monte Carlo** (`wc26_tournament.py`, 20k sims): exact group schedule,
sampled from DC grids; top-2 + 8 best thirds; R32 draw & bracket approximated
(randomized per sim, no same-group R32 ties); KO draws split proportionally (ET/pens);
host advantage in KO: USA all rounds, MEX/CAN through R16. Favourites: ARG 20.8%,
ESP 10.5%, BRA 9.4%, ENG 6.8%, JPN 6.1%. **France 3.9% diverges hard from market
(~10%)** — soft recent form + Group I (Senegal, Norway); treat as model blind spot
or value case, investigate before staking futures.

**Polymarket integration** (`wc26_polymarket.py`, free Gamma API): moneyline-only at
match level (3 binary markets per event). Slug `fifwc-{code}-{code}-{local-date}`;
question text uses "Türkiye"/"Côte d'Ivoire"/"Czechia" (alias map in script).
64/72 fixtures priced; missing 8 not listed yet. Match pages show fair vs market
with edge column (±3¢ highlight). First snapshot: market overprices Mexico v RSA
home win by ~7¢ vs model.

## Ensemble upgrade (2026-06-09, late)

`wc26_tournament.py` rewritten (numpy in `.venv/`):
- **200-model Bayesian bootstrap** (exponential re-weighting) — sims draw from
  parameter uncertainty; Argentina champion 20.8% → 19.2% (honest shrink).
- **Official FIFA bracket** from Wikipedia: R32 template matches 73–88 with
  third-place slot group constraints (backtracking allocation), real flow to final.
- **100k sims** for futures; deterministic mean-model bracket **locked** in
  `wc26_predictions.json` (all 72 group matches w/ modal scores + KO picks to the
  final). Predicted: Argentina over Spain in the final, Brazil third.
- `wc26_update_results.py` grades the locked file as results land (1X2 hit rate,
  exact scores, Brier, per-stage overlap); site "Bracket" page shows the scorecard.

## Accuracy package (2026-06-09, night)

1. **Multi-window tuning** (4 rolling 12-month windows, 4,228 test matches):
   picked the *same* params as the single window — they generalize. Honest CV
   log-loss is **0.891** (the oft-quoted 0.854 was the easiest window).
2. **Margin capping: rejected by the data.** cap=4 and cap=3 both lost to
   no-cap on CV log-loss. Blowout information apparently is information.
3. **Market blend** (log-opinion pool, w=0.35 on normalized Polymarket prices):
   group-match picks/probs now blended where priced (64/72). Flipped 7
   near-coin-flip picks toward the market. Locked file stores p_model /
   p_market / p (blend); `wc26_update_results.py` grades all three so the
   tournament itself will settle which source prices best.
4. **Run archive**: every output JSON is copied to `runs/<stamp>_<name>.json`;
   site footer shows model-run and price-snapshot timestamps.

## Anomaly model (2026-06-10)

Tournament sims (`wc26_tournament.py`) now include zero-mean anomaly variance:
- **Form shock**: per-tournament per-team N(0, 0.06) on log scoring rates —
  injuries/chemistry/camp variance the goals data can't see.
- **KO attrition**: each knockout tie, 10% chance per team of a lasting
  −0.04 log-attack knock (suspensions, injuries); **ET fatigue**: −0.03
  next match after going 120 minutes.
- Magnitudes are stated assumptions, not estimates. All shocks are zero-mean:
  nobody is helped on average, but favourites lose tail mass to the field —
  Argentina champion 19.0% → 16.6%, which moves the model toward market consensus.
- Implementation switched tournament sampling to vectorised plain Poisson
  (DC rho kept for match-card pricing only; ~1% draw-rate effect in sims).
- Locked bracket unaffected (deterministic mean-model path; shocks are zero-mean).

## Squad-value prior (2026-06-10, pre-matchday-1) — the Nate Silver borrow

Ratings now get `att += B*z/2, def -= B*z/2` where z = standardized log squad
market value (Transfermarkt, all 211→top-100 teams, minnow default €8m).
- **Validation** (fit to 2025-06, test on following 1,071 matches): log-loss
  0.854 → **0.818** at the B=0.4 optimum, clean peak (worse by 1.0). Shipped
  B=0.35 as a look-ahead haircut (current values partly reflect past results).
  Single biggest upgrade in the project; reaches ~market-grade calibration.
- **Caveats**: single-window validation (historical values unavailable);
  values are a 2026-06 snapshot, refresh wc26_squad_values.json occasionally.
- **Effect**: France champion 4.0→8.2% (was the documented blind spot),
  Spain 9.0→14.1, England 6.1→10.5; Japan 6.0→2.9, Colombia 5.0→2.9.
  Remaining model-vs-market gaps are now defensible disagreements, not
  blindness. Locked bracket unchanged (it testifies for the old model).
- Files: wc26_squad_values.json (data), wc26_value_test.py (falsification
  harness — rerun after value refreshes).

## Exact-score grid calibration (2026-06-11, matchday 1) — a clean negative

Polymarket listed per-match exact-score books (sibling events,
`{slug}-exact-score`, 17 cells: 0-0..3-3 + other), so the score grid is now
a bettable surface, not just bracket decoration. Audit before betting it:
- **New harness**: `wc26_simulate.py gridtest` — 17-class log-loss +
  per-cell observed/predicted on the holdout; `tuneboost` fits a `min2_boost`
  multiplier on cells where both sides score 2+ (the region DC's tau
  doesn't reach), trained on the 2022-25 windows.
- **Holdout (1,071 matches, 2025-06→2026-06)**: 17-class log-loss 2.4247.
  Tuned rho=-0.05 leaves its four target cells essentially perfect
  (0-0/1-0/0-1/1-1 obs/pred 0.98-1.02). The min2 region looked 28% hot
  (2-2 obs/pred 1.42) — **but it doesn't replicate**: training windows give
  ratios 0.93/1.16/0.93, and the fitted boost is exactly 1.0 (likelihood
  degrades monotonically above it). The holdout blip was a ~2σ window fluke.
- **Conclusion**: the DC grid is already calibrated for exact scores; no
  correction shipped (`min2_boost` stays 1.0 in params, plumbing kept in
  score_grid/grid_np for re-checks as tournament data accrues).
- **Betting**: `exact_scores` (17 cells) now emitted per match in
  wc26_simulations.json; wc26_polymarket.py snapshots the books
  (64/64 listed fixtures on day 1); find_bets.py scans them behind
  `include.exact_score` — **ships OFF**. Cell edges are the model's
  thinnest-evidence claims and mostly re-express the 1X2 opinion; the
  moneyline is usually the better instrument for the same view. Started
  matches are never scanned (pre-match model vs in-play prices).

## Market expansion pass (2026-06-12, pre-matchday-2)

Six new model surfaces, each priced from machinery that already existed or
from one validated parameter — plus a second clean negative.
- **Team totals & extra O/U lines (0.5/4.5/5.5)**: grid marginals and sums —
  zero new assumptions, emitted per match.
- **First to score**: Poisson race argument, P(home first)=λ1/(λ1+λ2)·(1−P(0-0)).
  Spot check vs market (USA-PAR): model 59/31/10 vs prices 56/34/10.
- **Half markets**: `half_split=0.447` — share of goals before HT, fit on
  2,360 goals across six tournaments (per-tournament 0.37-0.46, WC22 lowest
  at 0.399; `pipeline/wc26_half_split.py` refits). A half is a shorter DC
  match: scale λs, reuse the grid. Spot check (USA-PAR HT): model 39/45/17
  vs prices 34/46/19.
- **Futures scanning**: group winners + reach-R32/R16/QF/SF/final/champion
  books vs the 100k-sim ensemble (no new model; the scanner just never
  looked). Both sides of every binary are scanned.
- **Corners (negative result #2)**: 250-fixture backfill (WC22, AFCON23,
  Asian23, Euro24, Copa24; `wc26_corners.py`). NegBin total-corners with an
  xG slope **lost** leave-one-tournament-out to intercept-only (NLL 2.7050
  vs 2.6888) — attacking quality does not transfer to corner counts across
  tournaments. Shipped intercept-only (μ=9.18, k≈21): a base-rate anchor
  against lazy thin books, nothing more.
- **Skipped deliberately**: per-match scorer/shots/assists props — Polymarket
  lists shots-on-target only, books carry ~$4 liquidity, and our shares are
  opposition-unadjusted. No model beats no market.
All new categories ship OFF in betting config; both-sides scanning with the
liquidity/spread filter and pre-kickoff guard apply everywhere.

## LLM analyst sections (2026-06-12) — colour, not calculation

Each match (preview + post-result review), team and player page gains an
AI-written analysis section, generated by claude-opus-4-8 via
`pipeline/wc26_llm.py` (anthropic SDK lives in .venv, like numpy).
- **Grounding contract**: generations see ONLY a frozen source corpus
  (`wc26_llm_sources.json`: one-time Wikipedia summaries + internal data
  snapshots) plus our own model calculations. Same inputs → reproducible
  outputs; the system prompt forbids outside facts and is prompt-cached.
- **Separation of concerns**: the LLM text is presentation-layer colour,
  clearly labeled AI-written with model + timestamp. It never feeds the
  Dixon-Coles model, the simulations, or the betting scanner.
- Write-once per entry (reviews appear after results). Generation is
  MANUAL ONLY — deliberately excluded from matchday automation (API cost);
  run `.venv/bin/python3 pipeline/wc26_llm.py generate` when wanted.

## External review fixes (2026-06-12, post-matchday-2)

An external code review (codex) flagged five issues; two were confirmed
real bugs and all five are addressed:
1. **Value-prior validation contamination (real bug)**: after the prior
   shipped inside `fit()`, `wc26_value_test.py`'s beta sweep stacked on top
   of the already-applied beta=0.35 — its "baseline" wasn't. Fixed with
   `fit(..., value_beta=)`; the clean rerun reproduces the original
   numbers exactly (0.854 -> 0.818 at beta=0.4), so the published claim
   stands — the harness, not the history, was broken.
2. **Tournament nondeterminism (real bug)**: five `np.random.*` call sites
   used numpy's unseeded global generator (`random.seed` doesn't touch it).
   All randomness now flows through the seeded `default_rng(2026)`.
3. **Execution-time price protection**: place_bets.py now checks the live
   CLOB ask before each order and refuses fills more than
   `max_slippage_cents` (2c) above the planned price; the executed ask is
   recorded in the ledger.
4. **Paper trading + CLV**: find_bets.py logs every candidate (staked or
   not) to betting/state/paper.json; `betting/paper.py` grades first-seen
   entries for CLV (did the line move toward the model before close?) and
   resolved flat-stake PnL per category — the edge-finder's own public
   out-of-sample scoreboard.
5. **Test side effects**: the site integration tests now build into a
   temp dir; `unittest` no longer rewrites docs/.
Known and accepted: the value prior's mild look-ahead (today's values
grade last year's matches) — shipped below optimum as a haircut and now
also flagged in the README. The honest position, now stated publicly:
backtests are setup, the tournament is the experiment.

## Round-1 self-assessment (2026-06-18, group matchday 1 complete)

First full round graded (24/24). Blend log-loss **0.980**, model 1.016,
market 0.912; results called 13/24 (54%); 9 draws (38% vs ~25% baseline).
Convergence log (`wc26_accuracy_log.json`): blend 1.006 → 1.102 → **0.980**
at 12/16/24 graded — the mid-round dip was a partial-round artifact;
+0.16 off the 0.819 backtest and trending down. Three findings below; all
analysis was WC-blind and nothing in the model or params was changed.
Probes parked in `experiments/` (excluded from the matchday commit),
re-runnable each round.

**1. The "market beats the model" headline was apples-to-oranges.** The
compare block grades the model over all 24 graded but the market over only
the 21 it priced. Like-for-like on the same 21: locked model 0.960 vs
market 0.909 (market ahead 0.05); the full-24 model 1.016 is **dominated
by 3 unpriced matches** — Spain 0-0 Cape Verde (model 75% Spain) and
Canada 1-1 Bosnia (76% Canada), favourites held to draws, plus Korea.
The market never had to forecast those, so it cannot lose on them. A model
**refit through the eve of kickoff** scores 0.887 on the 21 — ahead of the
market; the 0.07 gap to the locked 0.960 is the cost of freezing the
bracket early. The live site uses the refit, so the published model is
competitive-to-ahead of the market on priced matches. The locked figure
the scorecard grades is the pessimistic one. Takeaway: report model and
market on the **same** match set.

**2. WC-blind tuning probe (`experiments/tune_holdout.py`): no retune
justified.** Fit every variant at cutoff 2026-06-10 (CSV verified WC-blind),
score on the 24, then re-check each promising knob on the rolling-window
historical CV (4,188 matches) — a tweak only counts if it helps the round
*without* costing on the big sample. The draw-heavy round flatters anything
adding draw mass: rho −0.15 wins the holdout (−0.016) but is **worse** on
history (0.88245 vs 0.88178); shrink 12 (−0.005 holdout) and margin_cap 4
are the same trap (history 0.89113 / 0.88208, both worse) — adopting them
would fit a 38%-draw fluke. Only survivor: **rho −0.05 → −0.10** is a
costless tie on history (0.88180) and the largest honest live gain
(−0.0085) — track it, optionally flip after the draw rate normalizes; even
this is within 24-match noise. Squad-value prior **vindicated live**:
turning it off costs +0.028, strengthening to 0.6 costs +0.035 — shipped
β=0.35 sits at the live optimum, independent of the historical validation.

**3. Alt-method bake-off (`experiments/altmethods_holdout.py`): the
goal-grid earns its keep.** Five families fit WC-blind, scored on the
common 21-match subset (all can price): **DC 0.887**, market 0.909,
ordered-logit-on-Elo-gap 1.019, Elo→Poisson 1.027, frequency floor 1.052.
Our Dixon-Coles is the best method on the round — ahead of the market and
well ahead of both pure rating-gap approaches, which barely clear the
no-skill floor. Modeling the 3-way outcome directly from a rating
difference (with or without a goal grid on top) throws away most of the
signal the full team-by-team attack/defense Poisson captures — real
evidence the DC complexity is not decoration.

**Net:** the round-1 "accuracy drop" was (a) a draw cluster and (b) a
mismatched model-vs-market comparison — not a model defect. Params held
as-is; re-run all three probes after round 2.

## LLM goal-rate coefficient — tested, not adopted (2026-06-18)

Tested the idea of an LLM coefficient inside the formula: let
claude-opus-4-8 read each match's pre-match dossier and output two
multiplicative coefficients in [0.80, 1.20] on the DC expected goals
(`lambda1·c_home`, `lambda2·c_away`), then rescore the grid.
`experiments/llm_coef_holdout.py`; coefficients frozen in
`experiments/llm_coef_cache.json` (re-scoring never re-bills).
- **Look-ahead-free by construction**: DC fit at cutoff 2026-06-10; Opus
  4.8's training cutoff (Jan 2026) predates the June tournament; the prompt
  carried ONLY pre-match data (frozen form to 2026-06-09, FIFA rank, squad
  value, our own xG + 1X2) with no web search, no tools, no result.
- **Result on the 24**: DC baseline log-loss 0.9818 → DC×LLM **0.9772**
  (−0.0045), half-strength 0.9793 (−0.0025); hit-rate unchanged 14/24. The
  LLM nudged 8/24 matches, all gentle, with sensible rationales (downweight
  Sweden's poor form, upweight Norway/Colombia attack) — but direction was a
  wash (it downweighted Sweden 0.90; Sweden won 5-1).
- **Verdict: no demonstrated edge, real downside.** −0.0045 over 24 matches
  is ~−0.1 total — under a quarter of one match's log-loss swing, i.e.
  noise. The coefficient is a *sampled* generation, not a fitted parameter,
  so it isn't even reproducible (re-run → different nudges), and wiring it to
  *live* news would reintroduce the look-ahead/contamination risk the project
  is built to avoid. This is a quantitative confirmation of the standing
  rule: LLM output stays colour, not calculation; the one sanctioned
  LLM→numbers path (`betting/news_check.py`) is reduce-only by design. If
  ever revisited, the only defensible form is reduce-only — let it dampen
  overconfident favourites, never amplify — and only once there are enough
  matches to measure. Not adopted; kept as an experiment.
