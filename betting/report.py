"""Personal betting dashboard — LOCAL ONLY, never committed.

  python3 betting/report.py        # writes betting/state/report.html + prints a summary
  open betting/state/report.html

Joins the real-money ledger with live Polymarket prices to show, in one
place: capital deployed vs cap, resolved profit/loss, open positions
marked to market with closing-line value, and a per-category scoreboard.
It reads betting/state/ (ledger, paper log, news-check log) and writes
betting/state/report.html — all gitignored, because it shows real
positions and stakes. This is the betting cockpit; nothing here is public.
"""
import html
import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = f"{HERE}/state"
OUT = f"{STATE}/report.html"


def load(name, default=None):
    try:
        return json.load(open(f"{STATE}/{name}"))
    except FileNotFoundError:
        return default


def cfg():
    c = json.load(open(f"{HERE}/config.json"))
    if os.path.exists(f"{HERE}/config.local.json"):
        c.update(json.load(open(f"{HERE}/config.local.json")))
    return c


def entry_price(b):
    """The price we actually got in at (executed ask if recorded)."""
    return b.get("price_at_exec") or b.get("price_seen") or b.get("market_p")


def results_truth():
    """Fallback grader: settled Polymarket markets get delisted from Gamma, so
    finished bets would sit at 'no live price' forever. grade_results settles
    them against the repo's own match data instead. Returns (module, truth) or
    None when the module/data files are unavailable — the report then degrades
    to today's behavior (position stays 'open', unpriced)."""
    try:
        import sys
        if HERE not in sys.path:
            sys.path.insert(0, HERE)
        import grade_results
        return grade_results, grade_results.load_truth()
    except Exception:
        return None


def grade(ledger, markets):
    """Ledger positions + live market lookup -> per-position rows.
    markets maps token_id -> (gamma_market, outcome_index). Positions Gamma
    can't price (delisted after settlement) fall back to results-based
    settlement via grade_results; truth is loaded lazily, once, only if a
    position actually needs it."""
    rows, fallback = [], []
    for b in ledger.get("placed", []):
        ep = entry_price(b)
        stake = b.get("stake_usdc", 0.0)
        hit = markets.get(b.get("token_id"))
        cur = closed = None
        if hit:
            mk, idx = hit
            try:
                cur = float(json.loads(mk["outcomePrices"])[idx])
            except (KeyError, ValueError, IndexError):
                cur = None
            closed = bool(mk.get("closed"))
        shares = (stake / ep) if ep else 0.0
        row = {"bet": b.get("bet", "?"), "category": b.get("category", "?"),
               "stake": stake, "entry": ep, "cur": cur, "closed": closed,
               "status": "open", "pnl": 0.0, "clv": None,
               "value": stake}
        if cur is not None and ep:
            row["clv"] = cur - ep
            if closed:
                won = cur > 0.5
                row["status"] = "won" if won else "lost"
                row["pnl"] = (shares - stake) if won else -stake
                row["value"] = shares if won else 0.0
            else:
                row["value"] = shares * cur           # mark to market
                row["pnl"] = row["value"] - stake     # unrealized
        elif cur is None:                             # Gamma can't price it
            if not fallback:
                fallback.append(results_truth())
            if fallback[0]:
                gr, truth = fallback[0]
                res = gr.resolve_bet(b, truth)
                if res is not None:                   # None -> leave as today
                    row["status"] = "won" if res else "lost"
                    row["pnl"] = (shares - stake) if res else -stake
                    row["value"] = shares if res else 0.0
                    row["settled_by"] = "results"     # cur stays None
        rows.append(row)
    return rows


def totals(rows):
    agg = {"deployed": 0.0, "resolved_pnl": 0.0, "won": 0, "lost": 0,
           "open_n": 0, "open_stake": 0.0, "open_value": 0.0,
           "unknown": 0, "clv_sum": 0.0, "clv_n": 0}
    cats = {}
    for r in rows:
        agg["deployed"] += r["stake"]
        c = cats.setdefault(r["category"], {
            "n": 0, "stake": 0.0, "resolved_pnl": 0.0, "won": 0, "lost": 0,
            "open_pnl": 0.0})
        c["n"] += 1
        c["stake"] += r["stake"]
        if r["clv"] is not None:
            agg["clv_sum"] += r["clv"]
            agg["clv_n"] += 1
        if r["status"] == "won":
            agg["won"] += 1
            agg["resolved_pnl"] += r["pnl"]
            c["won"] += 1
            c["resolved_pnl"] += r["pnl"]
        elif r["status"] == "lost":
            agg["lost"] += 1
            agg["resolved_pnl"] += r["pnl"]
            c["lost"] += 1
            c["resolved_pnl"] += r["pnl"]
        elif r["cur"] is None:
            agg["unknown"] += 1
        else:
            agg["open_n"] += 1
            agg["open_stake"] += r["stake"]
            agg["open_value"] += r["value"]
            c["open_pnl"] += r["pnl"]
    return agg, cats


# ---------------- rendering ----------------
CSS = """
body{font:15px/1.5 -apple-system,system-ui,sans-serif;background:#0d1014;
     color:#dce3ea;max-width:1080px;margin:0 auto;padding:24px}
h1{font-size:22px;margin-bottom:2px} h2{font-size:16px;margin-top:30px;
   border-bottom:1px solid #28313d;padding-bottom:5px}
.dim{color:#8b97a5}.pos{color:#5ad18a}.neg{color:#ff7a8a}
.cards{display:flex;flex-wrap:wrap;gap:12px;margin:14px 0}
.card{background:#141a22;border:1px solid #222b37;border-radius:9px;
      padding:12px 16px;min-width:150px}
.card .k{color:#8b97a5;font-size:12px;text-transform:uppercase}
.card .v{font-size:22px;font-variant-numeric:tabular-nums;margin-top:3px}
table{border-collapse:collapse;width:100%;margin:8px 0;font-size:13px}
th,td{text-align:right;padding:5px 9px;border-bottom:1px solid #1b222c}
th{color:#8b97a5;font-weight:600;font-size:11px;text-transform:uppercase}
td.l,th.l{text-align:left}.num{font-variant-numeric:tabular-nums}
.won{color:#5ad18a}.lost{color:#ff7a8a}.open{color:#9db2c8}
"""


def esc(s):
    return html.escape(str(s if s is not None else ""))


def money(x):
    cls = "pos" if x > 0.005 else "neg" if x < -0.005 else "dim"
    return f'<span class="{cls}">{x:+.2f}</span>'


def card(k, v, sub=""):
    return (f'<div class="card"><div class="k">{esc(k)}</div>'
            f'<div class="v">{v}</div>'
            f'{f"<div class=dim>{sub}</div>" if sub else ""}</div>')


def render(rows, agg, cats, conf, paper, news):
    cap = conf.get("max_total_stake_usdc", 0)
    headroom = cap - agg["deployed"]
    total_pnl = agg["resolved_pnl"] + (agg["open_value"] - agg["open_stake"])
    avg_clv = (agg["clv_sum"] / agg["clv_n"] * 100) if agg["clv_n"] else 0.0
    last_news = (news or {}).get("runs", [])
    last_news = last_news[-1]["at"] if last_news else "never"
    paper_n = sum(len(r["candidates"]) for r in (paper or {}).get("runs", []))

    cards = "".join([
        card("Deployed", f"${agg['deployed']:.2f}",
             f"of ${cap:.0f} cap · ${headroom:.2f} free"),
        card("Resolved P&L", money(agg["resolved_pnl"]),
             f"{agg['won']}W / {agg['lost']}L"),
        card("Open positions", f"{agg['open_n']}",
             f"${agg['open_stake']:.2f} staked → ${agg['open_value']:.2f} now"),
        card("Unrealized", money(agg["open_value"] - agg["open_stake"]),
             "mark-to-market"),
        card("Total P&L", money(total_pnl), "resolved + unrealized"),
        card("Avg CLV (open)", f"{avg_clv:+.1f}¢",
             "market moved our way?"),
    ])

    crows = "".join(
        f'<tr><td class="l">{esc(c)}</td><td class="num">{v["n"]}</td>'
        f'<td class="num">${v["stake"]:.2f}</td>'
        f'<td class="num">{v["won"]}/{v["lost"]}</td>'
        f'<td class="num">{money(v["resolved_pnl"])}</td>'
        f'<td class="num">{money(v["open_pnl"])}</td></tr>'
        for c, v in sorted(cats.items(), key=lambda kv: -kv[1]["stake"]))

    def prow(r):
        clv = f'{r["clv"]*100:+.1f}¢' if r["clv"] is not None else "—"
        cur = f'{r["cur"]:.2f}' if r["cur"] is not None else "—"
        st = {"won": "WON", "lost": "LOST", "open": "open"}[r["status"]]
        return (f'<tr><td class="l">{esc(r["bet"])}</td>'
                f'<td class="l dim">{esc(r["category"])}</td>'
                f'<td class="num">${r["stake"]:.2f}</td>'
                f'<td class="num">{r["entry"]:.2f}</td>'
                f'<td class="num">{cur}</td>'
                f'<td class="num">{clv}</td>'
                f'<td class="num {r["status"]}">{st}</td>'
                f'<td class="num">{money(r["pnl"])}</td></tr>')

    settled = [r for r in rows if r["status"] in ("won", "lost")]
    openp = sorted((r for r in rows if r["status"] == "open"),
                   key=lambda r: (r["clv"] is None, -(r["clv"] or 0)))
    unknown = [r for r in rows if r["cur"] is None and r["status"] == "open"]

    def section(title, items):
        if not items:
            return f"<h2>{esc(title)}</h2><p class='dim'>none</p>"
        return (f"<h2>{esc(title)} ({len(items)})</h2><table>"
                "<tr><th class='l'>bet</th><th class='l'>cat</th><th>stake</th>"
                "<th>entry</th><th>now</th><th>CLV</th><th>status</th>"
                "<th>P&L</th></tr>" + "".join(prow(r) for r in items)
                + "</table>")

    return (f"<!doctype html><meta charset='utf-8'>"
            f"<title>Betting report — local</title><style>{CSS}</style>"
            f"<h1>Betting report <span class='dim'>local only — never "
            f"published</span></h1>"
            f"<p class='dim'>generated {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}"
            f" · last news-gate run {esc(last_news)} · "
            f"{paper_n} paper candidates logged · "
            f"{agg['unknown']} position(s) with no live price</p>"
            f"<div class='cards'>{cards}</div>"
            f"<h2>By category</h2><table>"
            f"<tr><th class='l'>category</th><th>n</th><th>staked</th>"
            f"<th>W/L</th><th>resolved P&L</th><th>open P&L</th></tr>"
            f"{crows}</table>"
            f"{section('Settled', settled)}"
            f"{section('Open', openp)}"
            f"{section('No live price (delisted / not found)', unknown) if unknown else ''}"
            f"<p class='dim'>Entry = the ask we actually filled at. CLV = "
            f"current price minus entry (positive = the market moved toward "
            f"our view). Open P&L marks shares to the current price; it is not "
            f"realized until the market settles. Raw data: "
            f"betting/state/ledger.json.</p>")


def main():
    import paper as P   # reuse the batched Gamma lookup
    ledger = load("ledger.json", {"placed": []})
    tokens = [b["token_id"] for b in ledger["placed"] if b.get("token_id")]
    markets = P.gamma_markets(tokens) if tokens else {}
    rows = grade(ledger, markets)
    agg, cats = totals(rows)
    page = render(rows, agg, cats, cfg(), load("paper.json"),
                  load("news_checks.json"))
    os.makedirs(STATE, exist_ok=True)
    open(OUT, "w").write(page)

    tot = agg["resolved_pnl"] + (agg["open_value"] - agg["open_stake"])
    print(f"deployed ${agg['deployed']:.2f} · resolved {money_txt(agg['resolved_pnl'])}"
          f" ({agg['won']}W/{agg['lost']}L) · open {agg['open_n']}"
          f" (unrealized {money_txt(agg['open_value']-agg['open_stake'])})"
          f" · total {money_txt(tot)}")
    if agg["unknown"]:
        print(f"  ({agg['unknown']} position(s) had no live price — delisted "
              f"or not yet found on Gamma)")
    print(f"report written: {OUT}")


def money_txt(x):
    return f"${x:+.2f}"


if __name__ == "__main__":
    main()
