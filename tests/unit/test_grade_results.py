"""Unit: results-based bet settlement (betting/grade_results.py) and the
report.py fallback that uses it once Gamma delists a settled market.

Truth is SYNTHETIC — built directly in the shape load_truth() returns — so
these tests read no data files, hit no network, and need no betting/state/.
"""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

from betting import grade_results as G
from betting import report as R


def fx(home, away, score=None, pens=None, finished=None):
    return {"home": home, "away": away, "score": score,
            "finished": bool(score) if finished is None else finished,
            "pens": pens}


def truth(*fixtures, byid=None, group_winners=(), r32=()):
    return {"pair": {frozenset((f["home"], f["away"])): f for f in fixtures},
            "byid": {str(k): v for k, v in (byid or {}).items()},
            "group_winners": set(group_winners), "r32_teams": set(r32)}


TRUTH = truth(
    fx("France", "Denmark", "2-0"),               # home win
    fx("Chile", "Peru", "0-1"),                   # away win
    fx("Spain", "Italy", "1-1"),                  # group draw, no pens
    fx("Germany", "Paraguay", "1-1", pens="3-4"),  # KO tie, away wins shoot-out
    fx("Brazil", "Japan", "3-1"),                 # totals/team-total/spread props
    fx("Mexico", "Canada", "0-1"),
    fx("Argentina", "Bolivia", "3-0"),            # favourite covers -1.5
    fx("England", "Wales", "1-0"),                # favourite does NOT cover
    fx("Ghana", "Mali"),                          # scheduled, unplayed
    group_winners={"France", "Brazil"},
    r32={"Japan", "France", "Brazil"},
)


def bet(cat, s, match_id=None):
    b = {"category": cat, "bet": s}
    if match_id is not None:
        b["match_id"] = match_id
    return b


class TestMoneyline(unittest.TestCase):
    def test_home_win(self):
        self.assertTrue(G.resolve_bet(bet("moneyline", "France v Denmark: France"), TRUTH))
        self.assertFalse(G.resolve_bet(bet("moneyline", "France v Denmark: Denmark"), TRUTH))

    def test_away_win_and_yes_suffix(self):
        self.assertTrue(G.resolve_bet(bet("moneyline", "Chile v Peru: Peru (YES)"), TRUTH))
        self.assertFalse(G.resolve_bet(bet("moneyline", "Chile v Peru: Chile"), TRUTH))

    def test_draw_pick(self):
        self.assertTrue(G.resolve_bet(bet("moneyline", "Spain v Italy: Draw"), TRUTH))
        self.assertFalse(G.resolve_bet(bet("moneyline", "Spain v Italy: Spain"), TRUTH))

    def test_level_score_is_a_draw_even_with_penalties(self):
        """Polymarket moneylines settle on the 90' result: a pens-decided tie
        resolves Draw (verified against the settled price snapshots) — the
        shoot-out decides who advances, not this market."""
        self.assertFalse(G.resolve_bet(
            bet("moneyline", "Germany v Paraguay: Paraguay"), TRUTH))
        self.assertFalse(G.resolve_bet(
            bet("moneyline", "Germany v Paraguay: Germany"), TRUTH))
        self.assertTrue(G.resolve_bet(
            bet("moneyline", "Germany v Paraguay: Draw"), TRUTH))

    def test_unfinished_fixture_pending(self):
        self.assertIsNone(G.resolve_bet(bet("moneyline", "Ghana v Mali: Ghana"), TRUTH))

    def test_unknown_fixture_pending(self):
        self.assertIsNone(G.resolve_bet(bet("moneyline", "Foo v Bar: Foo"), TRUTH))

    def test_match_id_lookup_beats_name_parsing(self):
        t = truth(byid={101: fx("France", "Denmark", "2-0")})
        self.assertTrue(G.resolve_bet(
            bet("moneyline", "France v Denmark: France", match_id=101), t))


class TestTotals(unittest.TestCase):
    def test_over_both_sides(self):
        self.assertTrue(G.resolve_bet(bet("totals", "Brazil v Japan: O/U 2.5 Over"), TRUTH))
        self.assertFalse(G.resolve_bet(bet("totals", "Mexico v Canada: O/U 2.5 Over"), TRUTH))

    def test_under_both_sides(self):
        self.assertTrue(G.resolve_bet(bet("totals", "Mexico v Canada: O/U 2.5 Under"), TRUTH))
        self.assertFalse(G.resolve_bet(bet("totals", "Brazil v Japan: O/U 2.5 Under"), TRUTH))

    def test_unfinished_pending(self):
        self.assertIsNone(G.resolve_bet(bet("totals", "Ghana v Mali: O/U 2.5 Over"), TRUTH))


class TestTeamTotals(unittest.TestCase):
    def test_home_team_goals(self):          # Brazil scored 3
        self.assertTrue(G.resolve_bet(
            bet("team_totals", "Brazil v Japan: Brazil O/U 2.5 Over"), TRUTH))
        self.assertFalse(G.resolve_bet(
            bet("team_totals", "Brazil v Japan: Brazil O/U 2.5 Under"), TRUTH))

    def test_away_team_goals(self):          # Japan scored 1
        self.assertTrue(G.resolve_bet(
            bet("team_totals", "Brazil v Japan: Japan O/U 1.5 Under"), TRUTH))
        self.assertFalse(G.resolve_bet(
            bet("team_totals", "Brazil v Japan: Japan O/U 0.5 Under"), TRUTH))


class TestSpread(unittest.TestCase):
    def test_favourite_covers(self):         # Argentina by 3
        self.assertTrue(G.resolve_bet(
            bet("spread", "Argentina v Bolivia: Argentina (-1.5) — Argentina"), TRUTH))
        self.assertFalse(G.resolve_bet(
            bet("spread", "Argentina v Bolivia: Argentina (-1.5) — Bolivia"), TRUTH))

    def test_favourite_does_not_cover(self):  # England by only 1
        self.assertFalse(G.resolve_bet(
            bet("spread", "England v Wales: England (-1.5) — England"), TRUTH))
        self.assertTrue(G.resolve_bet(
            bet("spread", "England v Wales: England (-1.5) — Wales"), TRUTH))


class TestFutures(unittest.TestCase):
    def test_win_group_yes_no(self):
        self.assertTrue(G.resolve_bet(bet("futures", "France win_group YES"), TRUTH))
        self.assertFalse(G.resolve_bet(bet("futures", "Chile win_group YES"), TRUTH))
        self.assertTrue(G.resolve_bet(bet("futures", "Chile win_group NO"), TRUTH))
        self.assertFalse(G.resolve_bet(bet("futures", "France win_group NO"), TRUTH))

    def test_r32_yes_no(self):
        self.assertTrue(G.resolve_bet(bet("futures", "Japan r32 YES"), TRUTH))
        self.assertFalse(G.resolve_bet(bet("futures", "Ghana r32 YES"), TRUTH))
        self.assertTrue(G.resolve_bet(bet("futures", "Ghana r32 NO"), TRUTH))

    def test_r16_stays_pending(self):
        self.assertIsNone(G.resolve_bet(bet("futures", "France r16 YES"), TRUTH))


class TestOtherCategoriesPending(unittest.TestCase):
    def test_awards_and_props_none(self):
        for cat in ("awards", "first_to_score", "second_half"):
            self.assertIsNone(G.resolve_bet(
                bet(cat, "Brazil v Japan: whatever"), TRUTH))


class TestReportFallback(unittest.TestCase):
    """report.grade() settles Gamma-less positions from results — truth is
    injected by patching report.results_truth, so no data files are read."""

    def setUp(self):
        self._orig = R.results_truth
        R.results_truth = lambda: (G, TRUTH)

    def tearDown(self):
        R.results_truth = self._orig

    @staticmethod
    def ledger(*bets):
        return {"placed": list(bets)}

    def test_won_position_settles_from_results(self):
        b = {"bet": "France v Denmark: France", "category": "moneyline",
             "token_id": "tok", "stake_usdc": 5.0, "price_at_exec": 0.25}
        row = R.grade(self.ledger(b), {})[0]     # no Gamma market at all
        self.assertEqual(row["status"], "won")
        self.assertAlmostEqual(row["pnl"], 5.0 / 0.25 - 5.0)
        self.assertAlmostEqual(row["value"], 5.0 / 0.25)
        self.assertIsNone(row["cur"])            # still no market price
        self.assertEqual(row.get("settled_by"), "results")

    def test_lost_position_settles_from_results(self):
        b = {"bet": "Chile v Peru: Chile", "category": "moneyline",
             "token_id": "tok", "stake_usdc": 4.0, "price_at_exec": 0.40}
        row = R.grade(self.ledger(b), {})[0]
        self.assertEqual(row["status"], "lost")
        self.assertAlmostEqual(row["pnl"], -4.0)
        self.assertAlmostEqual(row["value"], 0.0)

    def test_pending_position_left_as_today(self):
        b = {"bet": "Ghana v Mali: Ghana", "category": "moneyline",
             "token_id": "tok", "stake_usdc": 3.0, "price_at_exec": 0.30}
        row = R.grade(self.ledger(b), {})[0]
        self.assertEqual(row["status"], "open")
        self.assertIsNone(row["cur"])
        self.assertEqual(row["pnl"], 0.0)
        self.assertNotIn("settled_by", row)

    def test_missing_truth_degrades_to_open(self):
        R.results_truth = lambda: None           # data file absent
        b = {"bet": "France v Denmark: France", "category": "moneyline",
             "token_id": "tok", "stake_usdc": 5.0, "price_at_exec": 0.25}
        row = R.grade(self.ledger(b), {})[0]
        self.assertEqual(row["status"], "open")

    def test_live_gamma_path_untouched(self):
        import json as _json
        b = {"bet": "Chile v Peru: Chile", "category": "moneyline",
             "token_id": "tok", "stake_usdc": 2.0, "price_at_exec": 0.50}
        mk = ({"outcomePrices": _json.dumps(["0.60", "0.40"]), "closed": False}, 0)
        row = R.grade(self.ledger(b), {"tok": mk})[0]
        self.assertEqual(row["status"], "open")  # priced -> marked to market,
        self.assertAlmostEqual(row["cur"], 0.60)  # results NOT consulted
        self.assertNotIn("settled_by", row)


if __name__ == "__main__":
    unittest.main()
