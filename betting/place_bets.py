"""Execute the bet plan on Polymarket (CLOB API).

  .venv/bin/python3 betting/place_bets.py           # DRY RUN (default)
  .venv/bin/python3 betting/place_bets.py --live    # places real orders

Safety rails, all enforced here regardless of what the plan says:
  - refuse plans older than max_plan_age_min (prices + kickoffs move;
    re-run find_bets) unless --ignore-plan-age
  - re-checks kickoff at execution time: a started match is never bet,
    even if the plan predates kickoff (pre-match model vs in-play prices)
  - hard refuse if total placed (ledger) + new stakes > max_total_stake_usdc
  - hard refuse any single stake > max_per_bet_usdc
  - never re-places a token_id already in the ledger, never both sides of
    one market — across runs (ledger) AND within a single batch
  - live ask checked per order: refuse if it rose past max_slippage_cents
    OR fell past max_price_drop_cents (a collapse is information — lineup
    news, a goal — that the plan doesn't have; re-scan instead of "buying
    the dip")
  - wallet USDC balance checked before the first live order
  - dry run is the default; --live is required to spend money

Secrets: put your Polygon wallet private key in betting/.env (gitignored):
  POLYMARKET_PRIVATE_KEY=0x...
  POLYMARKET_FUNDER=0x...        # only for email/Magic accounts (proxy wallet)
Your wallet needs USDC on Polygon with Polymarket allowances already set
(easiest: deposit + one manual trade via the UI first).
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from find_bets import merge_local, kickoff_times, started   # noqa: E402

CFG = json.load(open(f"{HERE}/config.json"))
if os.path.exists(f"{HERE}/config.local.json"):   # gitignored personal caps
    CFG = merge_local(CFG, json.load(open(f"{HERE}/config.local.json")))
LEDGER_PATH = f"{HERE}/state/ledger.json"


def load_env():
    env = {}
    path = f"{HERE}/.env"
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    env.update({k: v for k, v in os.environ.items()
                if k.startswith("POLYMARKET_")})
    return env


def load_ledger():
    if os.path.exists(LEDGER_PATH):
        return json.load(open(LEDGER_PATH))
    return {"placed": []}


def plan_age_minutes(created, now=None):
    """Age of a plan from its 'YYYY-MM-DD HH:MM UTC' stamp."""
    t = datetime.strptime(created, "%Y-%m-%d %H:%M UTC") \
        .replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return (now - t).total_seconds() / 60


def exec_price_ok(ask, plan_p, max_slip, max_drop):
    """Is the live ask still the price the plan reasoned about?

    Up past max_slip: the edge we sized for is gone. Down past max_drop:
    somebody knows something we don't — that 'bargain' is the most
    expensive kind. Either way the answer is a fresh scan, not this order."""
    if ask > plan_p + max_slip:
        return False, (f"price moved {plan_p:.3f} -> {ask:.3f} "
                       f"(over the {max_slip * 100:.0f}c slippage cap)")
    if ask < plan_p - max_drop:
        return False, (f"price collapsed {plan_p:.3f} -> {ask:.3f} "
                       f"(over {max_drop * 100:.0f}c — new information; "
                       f"re-run find_bets instead of buying blind)")
    return True, ""


def select_todo(bets, ledger, cfg, times, allow_topup=False):
    """Filter the plan down to what is actually safe to place (pure
    selection: no network). Enforces ledger dedup, in-batch dedup,
    kickoff, per-bet cap and the total cap. allow_topup intentionally
    relaxes the LEDGER dedup so an existing position can be added to (the
    in-batch dedup, kickoff and caps still apply)."""
    spent = sum(b["stake_usdc"] for b in ledger["placed"])
    done_tokens = {b["token_id"] for b in ledger["placed"]}
    # market-level dedup: never touch a market we already hold a side of —
    # buying the complementary token of an earlier bet locks in a loss
    done_markets = {b["question"] for b in ledger["placed"] if b.get("question")}
    # per-match exposure cap enforced at EXECUTION, not just planning:
    # hand-built plans and top-ups must not stack past it either. Combined
    # ledger + batch, keyed by match_id; entries without one (awards and
    # futures share "") are exempt — they are not one fixture.
    match_cap = cfg.get("max_per_match_usdc")
    match_spent = {}
    if match_cap:
        for b in ledger["placed"]:
            if b.get("match_id"):
                match_spent[b["match_id"]] = \
                    match_spent.get(b["match_id"], 0.0) + b["stake_usdc"]
    todo, batch_tokens, batch_markets = [], set(), set()
    for b in bets:
        if b["token_id"] in batch_tokens or \
                (b["token_id"] in done_tokens and not allow_topup):
            print(f"skip (already placed): {b['bet']}")
            continue
        q = b.get("question")
        if q and (q in batch_markets or (q in done_markets and not allow_topup)):
            print(f"skip (market already held, other side?): {b['bet']}")
            continue
        if started(str(b.get("match_id") or ""), times):
            print(f"skip (match has kicked off): {b['bet']}")
            continue
        if b["stake_usdc"] < 1:
            continue
        if b["stake_usdc"] > cfg["max_per_bet_usdc"]:
            print(f"REFUSE (> per-bet cap): {b['bet']} ${b['stake_usdc']}")
            continue
        mid = b.get("match_id")
        if match_cap and mid:
            room = match_cap - match_spent.get(mid, 0.0)
            if room < 1:
                print(f"skip (per-match cap ${match_cap} reached): {b['bet']}")
                continue
            if b["stake_usdc"] > room:
                b["stake_usdc"] = round(room, 2)
        if spent + sum(t["stake_usdc"] for t in todo) + b["stake_usdc"] \
                > cfg["max_total_stake_usdc"]:
            print(f"REFUSE (would exceed total cap): {b['bet']}")
            continue
        if match_cap and mid:
            match_spent[mid] = match_spent.get(mid, 0.0) + b["stake_usdc"]
        todo.append(b)
        batch_tokens.add(b["token_id"])
        if q:
            batch_markets.add(q)
    return todo


def live_ask(token_id):
    """Best ask from the public CLOB book — no auth needed."""
    import urllib.request
    url = f"https://clob.polymarket.com/price?token_id={token_id}&side=buy"
    try:
        # explicit UA required: the CDN 403s python's default user agent
        req = urllib.request.Request(url, headers={"User-Agent": "wc26-research"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return float(json.load(r)["price"])
    except Exception as e:
        print(f"  live price check failed: {e}")
        return None


def usdc_balance(client):
    """Wallet USDC (collateral) balance via the CLOB; None if unknown."""
    try:
        from py_clob_client_v2.clob_types import (AssetType,
                                                  BalanceAllowanceParams)
        r = client.get_balance_allowance(
            BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
        return int(r["balance"]) / 1e6
    except Exception as e:
        print(f"  balance check failed: {e}")
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true",
                    help="actually place orders (default: dry run)")
    ap.add_argument("--limit", type=int, default=None,
                    help="place at most N orders from the top of the plan")
    ap.add_argument("--ignore-plan-age", action="store_true",
                    help="execute a plan older than max_plan_age_min anyway")
    ap.add_argument("--allow-topup", action="store_true",
                    help="intentionally add to positions already in the ledger "
                         "(relaxes the ledger dedup; caps/kickoff still apply)")
    args = ap.parse_args()

    plan = json.load(open(f"{HERE}/state/plan.json"))
    age = plan_age_minutes(plan["created"])
    max_age = CFG.get("max_plan_age_min", 90)
    if age > max_age and not args.ignore_plan_age:
        sys.exit(f"REFUSED: plan is {age:.0f} min old (limit {max_age}) — "
                 f"prices and kickoffs have moved since it was built.\n"
                 f"Re-run betting/find_bets.py (or betting/run.py), or pass "
                 f"--ignore-plan-age if you really mean it.")

    ledger = load_ledger()
    spent = sum(b["stake_usdc"] for b in ledger["placed"])
    todo = select_todo(plan["bets"], ledger, CFG, kickoff_times(),
                       allow_topup=args.allow_topup)
    if args.limit:
        todo = todo[:args.limit]
    batch = sum(b["stake_usdc"] for b in todo)
    print(f"\nledger so far: ${spent:.2f} · this batch: ${batch:.2f} "
          f"· cap: ${CFG['max_total_stake_usdc']}")
    if not todo:
        print("nothing to place")
        return

    if not args.live:
        print("\nDRY RUN — would place:")
        for b in todo:
            print(f"  BUY ${b['stake_usdc']:>6.2f} YES @ ~{b['market_p']:.2f}  {b['bet']}")
        print("\nre-run with --live to place for real")
        return

    env = load_env()
    key = env.get("POLYMARKET_PRIVATE_KEY")
    if not key:
        sys.exit("POLYMARKET_PRIVATE_KEY missing — put it in betting/.env "
                 "(gitignored) or the environment")

    # V2 SDK — required since Polymarket's 2026-04-28 exchange migration
    from py_clob_client_v2.client import ClobClient
    from py_clob_client_v2.clob_types import (ApiCreds, MarketOrderArgsV2,
                                              OrderType,
                                              PartialCreateOrderOptions)

    funder = env.get("POLYMARKET_FUNDER")
    # 1 = email/Magic login, 2 = MetaMask/browser-wallet login
    sig_type = int(env.get("POLYMARKET_SIGNATURE_TYPE", "1"))
    if funder:   # normal Polymarket accounts trade through a proxy wallet
        client = ClobClient("https://clob.polymarket.com", key=key,
                            chain_id=137, signature_type=sig_type, funder=funder)
    else:        # plain EOA trading directly (rare)
        client = ClobClient("https://clob.polymarket.com", key=key, chain_id=137)
    # derive CLOB creds from the signing key (canonical path); fall back to
    # any explicitly provided creds if derivation fails
    try:
        client.set_api_creds(client.create_or_derive_api_key())
    except Exception as e:
        if env.get("CLOB_API_KEY"):
            print(f"derive failed ({e}); trying provided CLOB creds")
            client.set_api_creds(ApiCreds(
                api_key=env["CLOB_API_KEY"], api_secret=env["CLOB_SECRET"],
                api_passphrase=env["CLOB_PASSPHRASE"]))
        else:
            raise

    bal = usdc_balance(client)
    if bal is not None:
        print(f"wallet balance: ${bal:.2f} USDC")
        if bal + 0.01 < batch:
            sys.exit(f"REFUSED: batch needs ${batch:.2f} but the wallet "
                     f"holds ${bal:.2f} — deposit USDC or re-run with "
                     f"--limit to place fewer orders")
    else:
        print("WARNING: could not verify wallet balance — "
              "underfunded orders will fail at the exchange (FOK)")

    max_slip = CFG.get("max_slippage_cents", 2) / 100
    max_drop = CFG.get("max_price_drop_cents", 10) / 100
    for b in todo:
        print(f"placing ${b['stake_usdc']:.2f} on {b['bet']} ...", flush=True)
        # execution-time price protection: the plan's price is stale; a FOK
        # market order fills at the book, so refuse if it moved on us
        ask = live_ask(b["token_id"])
        if ask is None:
            print("  SKIP: cannot verify live price")
            continue
        ok, why = exec_price_ok(ask, b["market_p"], max_slip, max_drop)
        if not ok:
            print(f"  SKIP: {why}")
            continue
        b["price_at_exec"] = ask
        try:
            # multi-outcome books (moneylines, awards, group winners) are
            # neg-risk; standalone binaries (totals, BTTS, stage-reach)
            # are NOT — signing with the wrong flavor is rejected by the
            # exchange ("invalid POLY_PROXY signature")
            order = client.create_market_order(
                MarketOrderArgsV2(token_id=b["token_id"],
                                  amount=b["stake_usdc"], side="BUY"),
                PartialCreateOrderOptions(neg_risk=b.get("neg_risk", True)))
            resp = client.post_order(order, OrderType.FOK)
            ok = bool(resp and resp.get("success"))
            print("  ->", resp)
        except Exception as e:
            print(f"  FAILED: {e}")
            ok = False
        if ok:
            ledger["placed"].append({
                "at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
                "bet": b["bet"], "token_id": b["token_id"],
                "question": b.get("question", ""),
                "match_id": b.get("match_id", ""),
                "stake_usdc": b["stake_usdc"], "price_seen": b["market_p"],
                "price_at_exec": b.get("price_at_exec"),
                "model_p": b["model_p"], "category": b["category"],
            })
            json.dump(ledger, open(LEDGER_PATH, "w"), indent=2)
        time.sleep(1)

    total = sum(x["stake_usdc"] for x in ledger["placed"])
    print(f"\nledger total now ${total:.2f} / ${CFG['max_total_stake_usdc']} cap "
          f"({LEDGER_PATH})")


if __name__ == "__main__":
    main()
