"""One-command betting run: refresh prices -> scan all enabled categories
-> build plan -> place orders. Every safety rail in find_bets/place_bets
still applies; this script only chains them and refuses to start on
stale or broken inputs.

  .venv/bin/python3 betting/run.py            # full run, DRY (no orders)
  .venv/bin/python3 betting/run.py --live     # full run, REAL MONEY
  .venv/bin/python3 betting/run.py --live --limit 5
  .venv/bin/python3 betting/run.py --skip-refresh   # reuse today's snapshot
  .venv/bin/python3 betting/run.py --allow-stale    # bypass freshness gate

Preflight (hard stops, before any network call):
  - model outputs must exist and be fresher than max_sims_age_hours
    (a stale model prices yesterday's teams; run the pipeline first)
  - cap headroom: if the ledger has consumed the total cap, stop here
    rather than scan for bets that can never be placed
  - --live additionally requires POLYMARKET_PRIVATE_KEY (betting/.env)
"""
import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
sys.path.insert(0, HERE)
from find_bets import CFG   # merged committed + local config  # noqa: E402
from place_bets import load_env, load_ledger   # noqa: E402

REQUIRED_DATA = ("wc26_simulations.json", "wc26_market_prices.json",
                 "wc26_tournament.json", "wc26_awards.json")


def file_age_hours(path):
    return (time.time() - os.path.getmtime(path)) / 3600


def preflight(live, allow_stale, news=False):
    """Return a list of hard failures (empty = good to go)."""
    fails = []
    if news:
        from wc26_llm import api_key
        if not api_key():
            fails.append("--news-check needs an Anthropic key (env "
                         "ANTHROPIC_API_KEY or .anthropic_key) — without it "
                         "the gate would be silently skipped")
    for f in REQUIRED_DATA:
        if not os.path.exists(f"{DATA}/{f}"):
            fails.append(f"missing data/{f} — run the pipeline first")
    sims = f"{DATA}/wc26_simulations.json"
    max_age = CFG.get("max_sims_age_hours", 36)
    if os.path.exists(sims) and file_age_hours(sims) > max_age \
            and not allow_stale:
        fails.append(
            f"model output is {file_age_hours(sims):.0f}h old "
            f"(limit {max_age}h) — run the pipeline (or the matchday "
            f"script) first, or pass --allow-stale")
    spent = sum(b["stake_usdc"] for b in load_ledger()["placed"])
    headroom = CFG["max_total_stake_usdc"] - spent
    if headroom < CFG.get("min_stake_usdc", 1):
        fails.append(
            f"cap exhausted: ledger holds ${spent:.2f} of the "
            f"${CFG['max_total_stake_usdc']} total cap — raise "
            f"max_total_stake_usdc in betting/config.local.json to bet more")
    if live and not load_env().get("POLYMARKET_PRIVATE_KEY"):
        fails.append("POLYMARKET_PRIVATE_KEY missing — put it in "
                     "betting/.env (gitignored)")
    if live and not os.path.exists(f"{HERE}/config.local.json"):
        fails.append("no betting/config.local.json — refusing to bet real "
                     "money on the committed placeholder caps; create it "
                     "with your own caps first")
    return fails


def step(title, cmd, fatal=True):
    print(f"\n=== {title} ===", flush=True)
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        if fatal:
            sys.exit(f"ABORTED: '{title}' failed (exit {r.returncode}) — "
                     f"nothing was placed beyond what the ledger records")
        print(f"WARNING: '{title}' failed (exit {r.returncode}) — continuing")
    return r.returncode == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true",
                    help="place real orders (default: dry run)")
    ap.add_argument("--limit", type=int, default=None,
                    help="place at most N orders")
    ap.add_argument("--skip-refresh", action="store_true",
                    help="skip the Polymarket snapshot refresh")
    ap.add_argument("--allow-stale", action="store_true",
                    help="run even if model outputs exceed max_sims_age_hours")
    ap.add_argument("--news-check", action="store_true",
                    help="LLM news gate between plan and execution "
                         "(needs Anthropic key; costs API spend)")
    ap.add_argument("--full-budget", action="store_true",
                    help="treat the remaining cap headroom as a TARGET: "
                         "scale qualifying stakes up until it (or a cap) "
                         "is reached, instead of pure Kelly sizing")
    args = ap.parse_args()
    py = sys.executable

    fails = preflight(args.live, args.allow_stale, args.news_check)
    if fails:
        print("PREFLIGHT FAILED:")
        for f in fails:
            print(f"  - {f}")
        sys.exit(1)
    on = sorted(k for k, v in CFG["include"].items() if v)
    print(f"mode: {'LIVE — REAL MONEY' if args.live else 'dry run'}")
    print(f"categories on: {', '.join(on)}")

    if not args.skip_refresh:
        # late-listed fixtures only enter the snapshot on a re-fetch;
        # non-fatal because scan prices come live from Gamma anyway
        step("refresh Polymarket snapshot",
             [py, "pipeline/wc26_polymarket.py"], fatal=False)
    scan_cmd = [py, "betting/find_bets.py"]
    if args.full_budget:
        scan_cmd.append("--full-budget")
    step("scan markets + build plan", scan_cmd)

    if args.news_check:
        # fatal: if the user asked for the gate, executing without it
        # would silently bypass an intended safety layer
        step("LLM news gate", [py, "betting/news_check.py"])

    cmd = [py, "betting/place_bets.py"]
    if args.live:
        cmd.append("--live")
    if args.limit:
        cmd += ["--limit", str(args.limit)]
    step("place orders" if args.live else "place orders (dry run)", cmd)

    spent = sum(b["stake_usdc"] for b in load_ledger()["placed"])
    print(f"\ndone — ledger ${spent:.2f} / "
          f"${CFG['max_total_stake_usdc']} cap; "
          f"grade the edge-finder anytime: python3 betting/paper.py")


if __name__ == "__main__":
    main()
