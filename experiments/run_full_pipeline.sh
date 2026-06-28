#!/usr/bin/env bash
# Full local pipeline run (data + analysis + LLM + players + build + gate).
# Continues past a failing step so one blip doesn't hide the rest; records
# pass/fail per step and prints a summary at the end.
cd "$(dirname "$0")/.." || exit 1
PY=python3
VENV=.venv/bin/python3
declare -a RESULTS

step() {  # step "label" cmd...
  local label="$1"; shift
  echo ""; echo ">>> STEP: $label"; echo ">>> $*"
  local t0=$SECONDS
  if "$@"; then
    RESULTS+=("OK   ($((SECONDS-t0))s)  $label")
    echo "<<< $label OK ($((SECONDS-t0))s)"
  else
    local rc=$?
    RESULTS+=("FAIL[$rc] ($((SECONDS-t0))s)  $label")
    echo "<<< $label FAILED rc=$rc ($((SECONDS-t0))s)"
  fi
}

step "fetch last-10 (API-Football)"            $PY pipeline/wc26_fetch.py
step "refresh international_results.csv"        bash -c 'curl -sL https://raw.githubusercontent.com/martj42/international_results/master/results.csv -o data/international_results.csv && wc -l data/international_results.csv'
step "update_results (pull + grade + KO)"       $PY pipeline/wc26_update_results.py
step "polymarket prices"                        $PY pipeline/wc26_polymarket.py
step "espn ids"                                 $PY pipeline/wc26_espn_ids.py
step "simulate (fit + backtest)"                $PY pipeline/wc26_simulate.py
step "corners predict"                          $PY pipeline/wc26_corners.py predict
step "totals lock"                              $PY pipeline/wc26_totals_lock.py
step "elo second opinion"                       $PY pipeline/wc26_elo.py
step "tournament ensemble (100k)"               $VENV pipeline/wc26_tournament.py
step "players (squads + award prices)"          $PY pipeline/wc26_players.py
step "awards model"                             $VENV pipeline/wc26_awards.py
step "accuracy tracker snapshot"                $PY pipeline/wc26_accuracy_track.py
step "LLM analyst generate"                     $VENV pipeline/wc26_llm.py generate
step "player dossier pages"                      $PY pipeline/wc26_player_pages.py
step "build site"                               $PY pipeline/wc26_build_site.py
step "test gate"                                $PY -m unittest discover -s tests
step "build site + snapshot"                    $PY pipeline/wc26_build_site.py snapshot

echo ""; echo "======== PIPELINE SUMMARY ========"
for r in "${RESULTS[@]}"; do echo "  $r"; done
echo "=================================="
