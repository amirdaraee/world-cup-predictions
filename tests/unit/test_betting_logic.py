"""Unit: betting math and safety logic — pure functions only
(no network, no keys, no orders, no repo state)."""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "pipeline"))

from betting.find_bets import (kelly_stake, started, build_plan, merge_local,
                               apply_elo_caution)
from betting.place_bets import plan_age_minutes, exec_price_ok, select_todo
from wc26_polymarket import parse_score_question


_MID = [0]


def cand(category, edge, model_p=None, market_p=0.30, match_id=None):
    if match_id is None:                 # each candidate is its own fixture
        _MID[0] += 1                     # unless a shared id is passed in
        match_id = f"m{_MID[0]}"
    return {"category": category, "bet": f"{category}@{edge}",
            "question": "?", "token_id": "t", "match_id": match_id,
            "model_p": model_p if model_p is not None else market_p + edge,
            "market_p": market_p, "edge": edge}


class TestKelly(unittest.TestCase):
    def test_kelly_zero_at_fair_price(self):
        self.assertAlmostEqual(kelly_stake(0.4, 0.4, 100), 0.0, places=9)

    def test_kelly_positive_with_edge(self):
        self.assertGreater(kelly_stake(0.5, 0.3, 100), 0)

    def test_kelly_scales_with_bankroll(self):
        self.assertAlmostEqual(kelly_stake(0.5, 0.3, 200),
                               2 * kelly_stake(0.5, 0.3, 100), places=9)

    def test_kelly_fraction_is_fractional(self):
        """Full Kelly for q=.5, p=.3 is f*=2/7 of bankroll; ours must be less."""
        self.assertLess(kelly_stake(0.5, 0.3, 100), 100 * (0.5 - 0.3) / 0.7)


class TestBuildPlan(unittest.TestCase):
    CFG = {"max_total_stake_usdc": 100.0, "max_per_bet_usdc": 10.0,
           "kelly_fraction": 0.4, "min_stake_usdc": 1.0, "max_bets": 3}

    def test_awards_always_make_the_plan(self):
        cands = [cand("moneyline", 0.20), cand("moneyline", 0.18),
                 cand("moneyline", 0.15), cand("golden_boot", 0.04)]
        plan, _ = build_plan(cands, self.CFG)
        cats = [c["category"] for c in plan]
        self.assertIn("golden_boot", cats)
        self.assertEqual(len(plan), 3)   # max_bets, award kept a slot

    def test_per_match_slots_filled_by_edge(self):
        cands = [cand("moneyline", e) for e in (0.10, 0.30, 0.20)] + \
                [cand("exact_score", 0.25)]
        plan, _ = build_plan(cands, self.CFG)
        self.assertEqual([c["edge"] for c in plan], [0.30, 0.25, 0.20])

    def test_per_bet_cap_respected(self):
        plan, _ = build_plan([cand("moneyline", 0.40, market_p=0.10)],
                             self.CFG)
        self.assertLessEqual(plan[0]["stake_usdc"],
                             self.CFG["max_per_bet_usdc"])

    def test_min_stake_floor(self):
        plan, _ = build_plan([cand("moneyline", 0.005, market_p=0.50)],
                             self.CFG)
        self.assertGreaterEqual(plan[0]["stake_usdc"],
                                self.CFG["min_stake_usdc"])

    def test_total_cap_scaling(self):
        cfg = dict(self.CFG, max_bets=40, max_total_stake_usdc=20.0)
        cands = [cand("moneyline", 0.30, market_p=0.10) for _ in range(20)]
        plan, total = build_plan(cands, cfg)
        self.assertLessEqual(total, 20.0 + 0.05)
        self.assertEqual(len(plan), 20)

    def test_one_moneyline_per_match(self):
        """Draw + underdog on the same fixture are mutually exclusive — keep
        only the best-edge side, not both."""
        cands = [cand("moneyline", 0.20, match_id="x"),   # underdog
                 cand("moneyline", 0.12, match_id="x"),   # draw, same match
                 cand("moneyline", 0.15, match_id="y")]   # a different match
        plan, _ = build_plan(cands, dict(self.CFG, max_bets=10))
        mids = [(c["match_id"], c["edge"]) for c in plan]
        self.assertEqual(sorted(mids), [("x", 0.20), ("y", 0.15)])

    def test_one_moneyline_per_match_off(self):
        cands = [cand("moneyline", 0.20, match_id="x"),
                 cand("moneyline", 0.12, match_id="x")]
        plan, _ = build_plan(cands, dict(self.CFG, max_bets=10,
                                         one_moneyline_per_match=False))
        self.assertEqual(len(plan), 2)

    def test_one_goal_market_per_match(self):
        """A match-total and a team-total on the same fixture are one goal
        opinion staked twice — keep only the best-edge one across BOTH
        categories."""
        cands = [cand("totals", 0.20, match_id="x"),
                 cand("team_totals", 0.15, match_id="x"),   # same opinion
                 cand("totals", 0.12, match_id="x"),        # same opinion
                 cand("team_totals", 0.10, match_id="y")]   # other fixture
        plan, _ = build_plan(cands, dict(self.CFG, max_bets=10))
        kept = [(c["match_id"], c["edge"]) for c in plan]
        self.assertEqual(sorted(kept), [("x", 0.20), ("y", 0.10)])

    def test_one_goal_market_per_match_off(self):
        cands = [cand("totals", 0.20, match_id="x"),
                 cand("team_totals", 0.15, match_id="x")]
        plan, _ = build_plan(cands, dict(self.CFG, max_bets=10,
                                         one_goal_market_per_match=False))
        self.assertEqual(len(plan), 2)

    def test_min_model_prob_floor_drops_longshots(self):
        """An outcome the model rates below the floor is skipped (a fat 'edge'
        over a ~0 market price is a thin-book artefact) — but awards, which are
        legitimately low-probability, are exempt."""
        cfg = dict(self.CFG, min_model_prob=0.10, max_bets=10)
        cands = [cand("futures", 0.30, model_p=0.04, market_p=0.01),  # 4% longshot
                 cand("moneyline", 0.20, model_p=0.50),               # solid
                 cand("golden_boot", 0.04, model_p=0.06)]             # award, exempt
        cats = [c["category"] for c in build_plan(cands, cfg)[0]]
        self.assertNotIn("futures", cats)
        self.assertIn("moneyline", cats)
        self.assertIn("golden_boot", cats)

    def test_empty_plan(self):
        plan, total = build_plan([], self.CFG)
        self.assertEqual((plan, total), ([], 0.0))

    def test_per_match_exposure_cap(self):
        """Correlated bets on one fixture must not stack past the cap;
        the highest-edge expressions of the opinion keep their size."""
        cfg = dict(self.CFG, max_bets=40, max_per_match_usdc=12.0)
        cands = [cand(c, e, market_p=0.10, match_id="fix1") for c, e in
                 (("moneyline", 0.40), ("spread", 0.35), ("totals", 0.30),
                  ("team_totals", 0.25), ("halftime", 0.20))] + \
                [cand("moneyline", 0.15, market_p=0.10, match_id="fix2")]
        plan, _ = build_plan(cands, cfg)
        per_match = {}
        for c in plan:
            per_match[c["match_id"]] = \
                per_match.get(c["match_id"], 0) + c["stake_usdc"]
        self.assertLessEqual(per_match["fix1"], 12.0 + 0.01)
        self.assertIn("fix2", per_match)   # other fixtures unaffected
        kept_edges = [c["edge"] for c in plan if c["match_id"] == "fix1"]
        self.assertEqual(kept_edges, sorted(kept_edges, reverse=True))

class TestKoDrawCaution(unittest.TestCase):
    """Knockout moneylines with a high model draw probability are partly a
    penalties coin-flip a 90-minute 1X2 never collects on — reduce-only."""
    CFG = {"max_total_stake_usdc": 100.0, "max_per_bet_usdc": 10.0,
           "kelly_fraction": 0.4, "min_stake_usdc": 1.0, "max_bets": 10}

    @staticmethod
    def ko_cand(edge, p_draw, ko=True, **kw):
        c = cand("moneyline", edge, **kw)
        c["ko"], c["p_draw"] = ko, p_draw
        return c

    def test_high_draw_knockout_stake_halved(self):
        cands = [self.ko_cand(0.20, 0.32, match_id="k1"),            # trimmed
                 self.ko_cand(0.20, 0.20, match_id="k2"),            # low draw
                 self.ko_cand(0.20, 0.32, ko=False, match_id="g1")]  # group
        plan, _ = build_plan(cands, self.CFG)
        by = {c["match_id"]: c["stake_usdc"] for c in plan}
        self.assertAlmostEqual(by["k1"], round(by["k2"] * 0.5, 2), places=2)
        self.assertEqual(by["g1"], by["k2"])   # non-KO untouched

    def test_scaled_below_min_stake_drops(self):
        cands = [self.ko_cand(0.005, 0.35, market_p=0.50, match_id="k1")]
        plan, _ = build_plan(cands, self.CFG)   # min-stake $1 halved -> $0.50
        self.assertEqual(plan, [])

    def test_gate_off_leaves_stakes(self):
        cands = [self.ko_cand(0.20, 0.40, match_id="k1"),
                 self.ko_cand(0.20, 0.10, match_id="k2")]
        plan, _ = build_plan(cands, dict(self.CFG, ko_draw_caution=False))
        by = {c["match_id"]: c["stake_usdc"] for c in plan}
        self.assertEqual(by["k1"], by["k2"])

    def test_non_moneyline_ko_untouched(self):
        c = cand("totals", 0.20, match_id="k1")
        c["ko"], c["p_draw"] = True, 0.35
        halved = self.ko_cand(0.20, 0.35, match_id="k2")
        plan, _ = build_plan([c, halved], self.CFG)
        by = {b["match_id"]: b["stake_usdc"] for b in plan}
        self.assertEqual(by["k1"], 2 * by["k2"])


class TestFullBudget(unittest.TestCase):
    """--full-budget: the remaining bankroll is a TARGET — stakes scale up
    toward it, but never past the per-bet / per-match caps, and reduce-only
    cautions are never re-inflated."""
    CFG = {"max_total_stake_usdc": 40.0, "max_per_bet_usdc": 10.0,
           "kelly_fraction": 0.4, "min_stake_usdc": 1.0, "max_bets": 10,
           "deploy_full_budget": True}

    def test_plan_scales_up_to_the_budget(self):
        cands = [cand("moneyline", 0.10), cand("totals", 0.10),
                 cand("btts", 0.10), cand("golden_boot", 0.05)]
        _, total = build_plan(cands, self.CFG)
        self.assertAlmostEqual(total, 40.0, delta=0.05)

    def test_per_bet_cap_still_binds(self):
        _, total = build_plan([cand("moneyline", 0.10)], self.CFG)
        self.assertAlmostEqual(total, 10.0, delta=0.01)

    def test_per_match_cap_still_binds(self):
        cands = [cand("moneyline", 0.10, match_id="m1"),
                 cand("btts", 0.10, match_id="m1")]
        _, total = build_plan(cands, dict(self.CFG, max_per_match_usdc=8.0))
        self.assertAlmostEqual(total, 8.0, delta=0.01)

    def test_awards_fill_past_the_match_cap(self):
        cands = [cand("golden_boot", 0.05), cand("golden_boot", 0.04)]
        _, total = build_plan(cands, dict(self.CFG, max_per_match_usdc=8.0))
        self.assertAlmostEqual(total, 20.0, delta=0.02)   # 2 x per-bet cap

    def test_reduced_ko_draw_bet_not_reinflated(self):
        c = cand("moneyline", 0.20, match_id="k1")
        c["ko"], c["p_draw"] = True, 0.35
        plan, _ = build_plan([c, cand("totals", 0.20, match_id="m2")],
                             self.CFG)
        by = {b["match_id"]: b["stake_usdc"] for b in plan}
        self.assertLessEqual(by["k1"], 5.0)       # stayed halved
        self.assertAlmostEqual(by["m2"], 10.0, delta=0.01)   # took the fill

    def test_off_by_default(self):
        _, total = build_plan([cand("moneyline", 0.10)],
                              dict(self.CFG, deploy_full_budget=False))
        self.assertLess(total, 10.0)              # pure Kelly, no fill

    def test_ledger_exposure_counts_against_the_match_cap(self):
        """A match the ledger already holds $8 on gets NO new plan stake —
        and the budget flows to positions that can actually place."""
        cands = [cand("btts", 0.20, match_id="held"),
                 cand("golden_boot", 0.05)]
        cfg = dict(self.CFG, max_per_match_usdc=8.0,
                   ledger_match_spent={"held": 8.0})
        plan, total = build_plan(cands, cfg)
        self.assertEqual([c["match_id"] for c in plan
                          if c["category"] == "btts"], [])
        self.assertAlmostEqual(total, 10.0, delta=0.01)  # award took the fill


class TestExactScoreScanner(unittest.TestCase):
    def test_parse_home_first(self):
        self.assertEqual(parse_score_question(
            "Exact Score: Mexico 2 - 1 South Africa?",
            "Mexico", "South Africa"), "2-1")

    def test_parse_away_named_first(self):
        self.assertEqual(parse_score_question(
            "Exact Score: South Africa 2 - 1 Mexico?",
            "Mexico", "South Africa"), "1-2")

    def test_parse_aliases(self):
        self.assertEqual(parse_score_question(
            "Exact Score: Türkiye 1 - 0 Paraguay?",
            "Turkey", "Paraguay"), "1-0")

    def test_parse_other_and_junk(self):
        self.assertEqual(parse_score_question(
            "Exact Score: Any Other Score?", "Mexico", "South Africa"),
            "other")
        self.assertIsNone(parse_score_question(
            "Will Mexico win on 2026-06-11?", "Mexico", "South Africa"))

    def test_started_guard(self):
        times = {"1": "2000-01-01T12:00:00+00:00",
                 "2": "2099-01-01T12:00:00+00:00"}
        self.assertTrue(started("1", times))
        self.assertFalse(started("2", times))
        self.assertFalse(started("3", times))   # unknown id: no guard claim

class TestEloCaution(unittest.TestCase):
    CFG = {"use_elo_caution": True, "elo_caution_pp": 15,
           "elo_caution_factor": 0.5}
    ELO = {"101": {"disagreement": 0.20}, "102": {"disagreement": 0.05}}

    @staticmethod
    def bet(mid, stake=4.0):
        return {"bet": f"b{mid}", "match_id": mid, "stake_usdc": stake}

    def test_wide_disagreement_halves_stake(self):
        kept, flagged = apply_elo_caution(
            [self.bet("101"), self.bet("102")], self.ELO, self.CFG)
        by = {b["match_id"]: b for b in kept}
        self.assertEqual(by["101"]["stake_usdc"], 2.0)
        self.assertEqual(by["102"]["stake_usdc"], 4.0)   # within noise
        self.assertEqual(flagged, ["b101"])

    def test_scaled_below_dollar_drops(self):
        kept, _ = apply_elo_caution([self.bet("101", 1.5)], self.ELO, self.CFG)
        self.assertEqual(kept, [])

    def test_reduce_only_and_fail_open(self):
        bets = [self.bet("101")]
        kept, flagged = apply_elo_caution(bets, None, self.CFG)   # no file
        self.assertEqual(kept[0]["stake_usdc"], 4.0)
        kept, _ = apply_elo_caution(bets, self.ELO,
                                    {**self.CFG, "use_elo_caution": False})
        self.assertEqual(kept[0]["stake_usdc"], 4.0)

    def test_futures_ids_untouched(self):
        kept, flagged = apply_elo_caution(
            [self.bet("future:Spain")], self.ELO, self.CFG)
        self.assertEqual((kept[0]["stake_usdc"], flagged), (4.0, []))


class TestMergeLocal(unittest.TestCase):
    def test_include_deep_merged(self):
        """A local include that flips two gates must not wipe the rest."""
        base = {"max_total_stake_usdc": 20,
                "include": {"moneyline": True, "totals": False,
                            "corners": False}}
        merged = merge_local(dict(base), {"max_total_stake_usdc": 500,
                                          "include": {"totals": True}})
        self.assertEqual(merged["max_total_stake_usdc"], 500)
        self.assertTrue(merged["include"]["moneyline"])   # survived
        self.assertTrue(merged["include"]["totals"])      # flipped
        self.assertFalse(merged["include"]["corners"])    # untouched

    def test_local_without_include(self):
        base = {"include": {"moneyline": True}}
        merged = merge_local(dict(base), {"max_bets": 9})
        self.assertEqual(merged["include"], {"moneyline": True})
        self.assertEqual(merged["max_bets"], 9)


class TestExecutionGuards(unittest.TestCase):
    def test_plan_age(self):
        from datetime import datetime, timezone
        now = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)
        self.assertAlmostEqual(
            plan_age_minutes("2026-06-12 10:30 UTC", now=now), 90.0)

    def test_price_within_band_ok(self):
        ok, _ = exec_price_ok(0.51, 0.50, max_slip=0.02, max_drop=0.10)
        self.assertTrue(ok)

    def test_price_rise_refused(self):
        """The edge was sized at the plan price; pay more and it's gone."""
        ok, why = exec_price_ok(0.53, 0.50, max_slip=0.02, max_drop=0.10)
        self.assertFalse(ok)
        self.assertIn("slippage", why)

    def test_price_collapse_refused(self):
        """A big drop is information we don't have, not a discount."""
        ok, why = exec_price_ok(0.35, 0.50, max_slip=0.02, max_drop=0.10)
        self.assertFalse(ok)
        self.assertIn("information", why)

    def test_small_favorable_drop_ok(self):
        ok, _ = exec_price_ok(0.45, 0.50, max_slip=0.02, max_drop=0.10)
        self.assertTrue(ok)


class TestSelectTodo(unittest.TestCase):
    CFG = {"max_per_bet_usdc": 10.0, "max_total_stake_usdc": 100.0}
    TIMES = {"past": "2000-01-01T12:00:00+00:00",
             "future": "2099-01-01T12:00:00+00:00"}

    @staticmethod
    def bet(token="t1", q="q1", mid="future", stake=5.0):
        return {"token_id": token, "question": q, "match_id": mid,
                "stake_usdc": stake, "bet": f"{token}/{q}"}

    def pick(self, bets, ledger=None, cfg=None, allow_topup=False):
        import io
        from contextlib import redirect_stdout
        with redirect_stdout(io.StringIO()):
            return select_todo(bets, ledger or {"placed": []},
                               cfg or self.CFG, self.TIMES,
                               allow_topup=allow_topup)

    def test_ledger_token_dedup(self):
        led = {"placed": [{"token_id": "t1", "question": "other",
                           "stake_usdc": 5.0}]}
        self.assertEqual(self.pick([self.bet(token="t1")], led), [])

    def test_ledger_market_dedup_blocks_other_side(self):
        """Holding YES then buying NO of the same market locks in a loss."""
        led = {"placed": [{"token_id": "yes-tok", "question": "q1",
                           "stake_usdc": 5.0}]}
        self.assertEqual(self.pick([self.bet(token="no-tok", q="q1")], led), [])

    def test_in_batch_market_dedup(self):
        """Both sides of one market inside a single plan: only one places."""
        todo = self.pick([self.bet(token="yes-tok", q="q1"),
                          self.bet(token="no-tok", q="q1")])
        self.assertEqual(len(todo), 1)

    def test_kickoff_rechecked_at_execution(self):
        """A plan built pre-kickoff must not execute post-kickoff."""
        todo = self.pick([self.bet(mid="past"),
                          self.bet(token="t2", q="q2", mid="future")])
        self.assertEqual([b["token_id"] for b in todo], ["t2"])

    def test_futures_ids_have_no_kickoff(self):
        todo = self.pick([self.bet(mid="future:Spain")])
        self.assertEqual(len(todo), 1)

    def test_caps_enforced(self):
        led = {"placed": [{"token_id": "x", "question": "qx",
                           "stake_usdc": 95.0}]}
        bets = [self.bet(stake=11.0),                       # > per-bet cap
                self.bet(token="t2", q="q2", stake=6.0)]    # > total cap
        self.assertEqual(self.pick(bets, led), [])

    def test_per_match_cap_combined_ledger_and_batch(self):
        """Hand-built plans must not stack a fixture past the cap: exposure
        is ledger + batch combined, trimmed to the remaining room."""
        cfg = dict(self.CFG, max_per_match_usdc=8.0)
        led = {"placed": [{"token_id": "x", "question": "qx",
                           "match_id": "future", "stake_usdc": 5.0}]}
        bets = [self.bet(token="t2", q="q2", stake=2.0),   # fits (room 3)
                self.bet(token="t3", q="q3", stake=4.0),   # trimmed to 1
                self.bet(token="t4", q="q4", stake=3.0)]   # room 0: skipped
        todo = self.pick(bets, led, cfg)
        self.assertEqual([(b["token_id"], b["stake_usdc"]) for b in todo],
                         [("t2", 2.0), ("t3", 1.0)])

    def test_per_match_cap_applies_to_topups(self):
        """allow_topup relaxes the dedup, never the exposure cap."""
        cfg = dict(self.CFG, max_per_match_usdc=8.0)
        led = {"placed": [{"token_id": "t1", "question": "q1",
                           "match_id": "future", "stake_usdc": 6.0}]}
        todo = self.pick([self.bet(stake=5.0)], led, cfg, allow_topup=True)
        self.assertEqual([b["stake_usdc"] for b in todo], [2.0])

    def test_per_match_cap_ignores_empty_match_id(self):
        """Awards and futures share match_id '' — they are not one fixture
        and must never be pooled under the per-match cap."""
        cfg = dict(self.CFG, max_per_match_usdc=8.0)
        led = {"placed": [{"token_id": "x", "question": "qx",
                           "match_id": "", "stake_usdc": 7.0}]}
        bets = [self.bet(token="a1", q="qa", mid="", stake=5.0),
                self.bet(token="a2", q="qb", mid="", stake=5.0)]
        todo = self.pick(bets, led, cfg)
        self.assertEqual([b["stake_usdc"] for b in todo], [5.0, 5.0])

    def test_no_per_match_cap_key_skips_enforcement(self):
        led = {"placed": [{"token_id": "x", "question": "qx",
                           "match_id": "future", "stake_usdc": 50.0}]}
        todo = self.pick([self.bet(stake=5.0, token="t9", q="q9")], led)
        self.assertEqual(len(todo), 1)   # only the total cap applies


if __name__ == "__main__":
    unittest.main()
