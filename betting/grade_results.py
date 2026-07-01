"""Grade the real betting ledger against ACTUAL match results — LOCAL ONLY.

report.py grades via live Polymarket prices, but settled markets get delisted
from Gamma, so finished bets show up as 'no live price' and never resolve. This
grader instead settles each bet against the real results we already have
(scores, standings, who reached R32), so resolved wins/losses (e.g. the
Australia v Turkey moneyline) actually count.

Resolves: moneyline, totals, team_totals, spread, and futures win_group / r32.
Leaves pending (no local truth yet): unplayed knockout ties, futures r16+,
awards (top scorer / golden boot), first_to_score, second_half.

  python3 betting/grade_results.py     # prints corrected P&L; reads only local data
Nothing is published; the ledger stays gitignored.
"""
import json
import os
import re
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")


def load(p):
    return json.load(open(p))


def load_truth():
    """Truth index from the repo's own data files: finished fixtures keyed by
    (home, away) pair and by match_id, plus the group winners and the set of
    teams that reached the Round of 32. Importable (with resolve_bet) so
    report.py can settle Gamma-delisted markets with the same logic."""
    gm = load(f"{DATA}/fifa_world_cup_2026_group_matches.json")["matches"]
    try:
        ko = load(f"{DATA}/wc26_knockout_matches.json")["matches"]
    except FileNotFoundError:
        ko = []
    by_pair, by_id = {}, {}
    for m in gm + ko:
        fin = str(m.get("status", "")).startswith("Match Finished") and m.get("score")
        rec = {"home": m["home"], "away": m["away"],
               "score": m.get("score"), "finished": bool(fin),
               "pens": m.get("penalties")}
        by_pair[frozenset((m["home"], m["away"]))] = rec
        if m.get("match_id"):
            by_id[str(m["match_id"])] = rec
    # group winners (pts, then GD, GF)
    tab = defaultdict(lambda: defaultdict(lambda: [0, 0, 0]))
    for m in gm:
        if not (str(m.get("status", "")).startswith("Match Finished") and m.get("score")):
            continue
        hs, as_ = (int(x) for x in m["score"].split("-"))
        for t, gf, ga in ((m["home"], hs, as_), (m["away"], as_, hs)):
            r = tab[m["group"]][t]
            r[0] += 3 if gf > ga else 1 if gf == ga else 0
            r[1] += gf - ga
            r[2] += gf
    winners = {}
    for g, teams in tab.items():
        order = sorted(teams, key=lambda t: (-teams[t][0], -teams[t][1], -teams[t][2]))
        if order:
            winners[order[0]] = g
    r32_teams = {t for m in ko if m.get("round") == "Round of 32"
                 for t in (m["home"], m["away"])}
    return {"pair": by_pair, "byid": by_id,
            "group_winners": set(winners), "r32_teams": r32_teams}


def goals(rec):
    h, a = (int(x) for x in rec["score"].split("-"))
    return h, a


def fixture_for(bet, match_id, truth):
    if match_id and str(match_id) in truth["byid"]:
        return truth["byid"][str(match_id)]
    m = re.match(r"(.+?) v (.+?):", bet)
    if m:
        return truth["pair"].get(frozenset((m.group(1).strip(),
                                            m.group(2).strip())))
    return None


def resolve_bet(b, truth):
    """Return True (won), False (lost), or None (pending/ungradable)."""
    cat, bet = b.get("category"), b.get("bet", "")
    fx = fixture_for(bet, b.get("match_id"), truth)

    if cat == "moneyline":
        if not fx or not fx["finished"]:
            return None
        h, a = goals(fx)
        # Polymarket moneylines settle on the 90-MINUTE result — a pens-decided
        # KO tie resolves 'Draw' (verified empirically: both 1-1 R32 ties
        # settled draw=1.0 in the price snapshot). Pens decide who ADVANCES,
        # which is a different market; never credit a win bet from a shoot-out.
        winner = fx["home"] if h > a else fx["away"] if a > h else "Draw"
        pick = bet.split(": ", 1)[1].replace(" (YES)", "").strip()
        return pick == winner

    if cat in ("totals", "team_totals"):
        if not fx or not fx["finished"]:
            return None
        h, a = goals(fx)
        line = float(re.search(r"O/U (\d+\.?\d*)", bet).group(1))
        over_side = bet.strip().endswith("Over")
        if cat == "totals":
            val = h + a
        else:                                   # team total: whose goals?
            team = re.search(r": (.+?) O/U", bet).group(1).strip()
            val = h if team == fx["home"] else a
        return (val > line) == over_side

    if cat == "spread":
        if not fx or not fx["finished"]:
            return None
        h, a = goals(fx)
        fav = re.search(r": (.+?) \(-1\.5\)", bet).group(1).strip()
        pick = bet.rsplit("— ", 1)[1].strip()
        fav_margin = (h - a) if fav == fx["home"] else (a - h)
        fav_covers = fav_margin >= 2
        return fav_covers if pick == fav else not fav_covers

    if cat == "futures":
        team = bet.split(" win_group")[0].split(" r32")[0].split(" r16")[0].strip()
        yes = bet.strip().endswith("YES")
        if "win_group" in bet:
            return (team in truth["group_winners"]) == yes
        if " r32 " in bet:
            return (team in truth["r32_teams"]) == yes
        return None                              # r16+ not decided

    return None                                  # awards / 1st-to-score / 2nd-half


def main():
    truth = load_truth()
    led = load(f"{HERE}/state/ledger.json")["placed"]

    agg = {"deployed": 0.0, "won": 0, "lost": 0, "pnl": 0.0,
           "pending_n": 0, "pending_stake": 0.0}
    cats = defaultdict(lambda: {"n": 0, "stake": 0.0, "won": 0, "lost": 0,
                                "pnl": 0.0, "pending": 0})
    wins = []
    for b in led:
        stake = b.get("stake_usdc", 0.0)
        entry = b.get("price_at_exec") or b.get("price_seen") or b.get("market_p")
        agg["deployed"] += stake
        c = cats[b.get("category", "?")]
        c["n"] += 1
        c["stake"] += stake
        r = resolve_bet(b, truth)
        if r is None:
            agg["pending_n"] += 1
            agg["pending_stake"] += stake
            c["pending"] += 1
            continue
        shares = stake / entry if entry else 0.0
        pnl = (shares - stake) if r else -stake
        agg["pnl"] += pnl
        c["pnl"] += pnl
        if r:
            agg["won"] += 1
            c["won"] += 1
            wins.append((pnl, b.get("bet"), stake, entry))
        else:
            agg["lost"] += 1
            c["lost"] += 1

    n_settled = agg["won"] + agg["lost"]
    roi = agg["pnl"] / (agg["deployed"] - agg["pending_stake"]) if n_settled else 0.0
    print("=== Ledger graded against ACTUAL results (local) ===")
    print(f"deployed ${agg['deployed']:.2f} across {len(led)} bets")
    print(f"settled: {n_settled}  ({agg['won']}W / {agg['lost']}L)  "
          f"realized P&L ${agg['pnl']:+.2f}  "
          f"(ROI {roi*100:+.1f}% on ${agg['deployed']-agg['pending_stake']:.2f} settled stake)")
    print(f"pending: {agg['pending_n']} bets, ${agg['pending_stake']:.2f} still at risk "
          f"(unplayed KO, futures r16+, awards, 1st-to-score, 2nd-half)")
    print("\nby category:")
    print(f"  {'cat':<18}{'n':>3}{'stake':>9}{'W/L':>7}{'pending':>8}{'P&L':>10}")
    for c, v in sorted(cats.items(), key=lambda kv: -kv[1]["stake"]):
        print(f"  {c:<18}{v['n']:>3}{v['stake']:>9.2f}"
              f"{f'{v[chr(119)+chr(111)+chr(110)]}/{v[chr(108)+chr(111)+chr(115)+chr(116)]}':>7}"
              f"{v['pending']:>8}{v['pnl']:>+10.2f}")
    print("\nbiggest realized wins:")
    for pnl, bet, stake, entry in sorted(wins, reverse=True)[:6]:
        print(f"  +${pnl:7.2f}  {bet}  (${stake:.0f} @ {entry})")


if __name__ == "__main__":
    main()
