"""Integration: contracts between the canonical data files. Every scanner
and page builder assumes these shapes line up; if a pipeline change breaks
the chain, this is where it should fail first — loudly and offline."""
import json
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(ROOT, "data")


def load(name):
    return json.load(open(os.path.join(DATA, name)))


def known_fixture_ids():
    """All real fixtures the pipeline may price: the 72 group matches plus
    any knockout ties whose teams are confirmed. Sims/snapshot/corners
    legitimately cover knockouts once the bracket fills, so contracts check
    against this union, not the group file alone."""
    ids = {str(m["match_id"]) for m in
           load("fifa_world_cup_2026_group_matches.json")["matches"]}
    try:
        ids |= {str(m["match_id"]) for m in
                load("wc26_knockout_matches.json")["matches"]}
    except FileNotFoundError:
        pass
    return ids


class TestFixturesSimsAlignment(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = load("fifa_world_cup_2026_group_matches.json")["matches"]
        cls.sims = load("wc26_simulations.json")["simulations"]
        cls.snap = load("wc26_market_prices.json")["prices"]

    def test_72_unique_group_fixtures(self):
        ids = [m["match_id"] for m in self.fixtures]
        self.assertEqual(len(ids), 72)
        self.assertEqual(len(set(ids)), 72)

    def test_every_fixture_is_simulated(self):
        group = {str(m["match_id"]) for m in self.fixtures}
        known = known_fixture_ids()
        # every group fixture must have a sim ...
        self.assertTrue(group <= set(self.sims),
                        group - set(self.sims))
        # ... and sims may only cover real fixtures (group + knockout)
        self.assertTrue(set(self.sims) <= known, set(self.sims) - known)

    def test_snapshot_only_covers_known_fixtures(self):
        fids = known_fixture_ids()
        self.assertTrue(set(self.snap) <= fids, set(self.snap) - fids)
        for mid, rec in self.snap.items():
            self.assertIn("slug", rec, mid)

    def test_sim_probabilities_coherent(self):
        for mid, sim in self.sims.items():
            ml = sim["moneyline"]
            self.assertAlmostEqual(sum(ml.values()), 1.0, places=2, msg=mid)
            self.assertAlmostEqual(sum(sim["exact_scores"].values()), 1.0,
                                   places=2, msg=mid)
            self.assertEqual(len(sim["exact_scores"]), 17, mid)
            for part in ("halftime", "second_half", "first_to_score"):
                self.assertAlmostEqual(sum(sim[part].values()), 1.0,
                                       places=2, msg=f"{mid} {part}")

    def test_totals_monotone_in_line(self):
        for mid, sim in self.sims.items():
            probs = [sim["totals"][f"over_{line}.5"] for line in range(6)]
            self.assertEqual(probs, sorted(probs, reverse=True), mid)
            for side in ("home", "away"):
                tt = sim["team_totals"][side]
                tp = [tt[k] for k in sorted(tt)]
                self.assertEqual(tp, sorted(tp, reverse=True), f"{mid} {side}")


class TestTournamentContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.teams = load("wc26_tournament.json")["teams"]

    def test_48_teams(self):
        self.assertEqual(len(self.teams), 48)

    def test_stage_probabilities_monotone(self):
        """Reaching a later stage can never be likelier than an earlier one."""
        ladder = ("r32", "r16", "qf", "sf", "final", "champion")
        for team, t in self.teams.items():
            probs = [t[s] for s in ladder]
            for earlier, later in zip(probs, probs[1:]):
                self.assertGreaterEqual(earlier + 1e-9, later, team)
            self.assertLessEqual(t["win_group"], t["r32"] + 1e-9, team)


class TestCornersContract(unittest.TestCase):
    def test_corner_lines_monotone_and_bounded(self):
        fids = known_fixture_ids()
        for mid, m in load("wc26_corners.json")["matches"].items():
            self.assertIn(mid, fids)
            lines = sorted((k for k in m if k.startswith("over_")),
                           key=lambda k: float(k.split("_")[1]))
            probs = [m[k] for k in lines]
            self.assertEqual(probs, sorted(probs, reverse=True), mid)
            self.assertTrue(all(0 < p < 1 for p in probs), mid)


class TestEloContract(unittest.TestCase):
    ELO = os.path.join(DATA, "wc26_elo.json")

    @unittest.skipUnless(os.path.exists(os.path.join(DATA, "wc26_elo.json")),
                         "elo file not generated")
    def test_second_opinion_coherent_and_display_only(self):
        elo = load("wc26_elo.json")
        fids = {str(m["match_id"]) for m in
                load("fifa_world_cup_2026_group_matches.json")["matches"]}
        self.assertIn("display-only", elo["method"])
        self.assertGreater(elo["backtest"]["logloss_1x2"], 0)
        self.assertTrue(set(elo["matches"]) <= fids)
        self.assertGreaterEqual(len(elo["matches"]), 60)
        for mid, m in elo["matches"].items():
            self.assertAlmostEqual(sum(m["moneyline"].values()), 1.0,
                                   places=2, msg=mid)
            self.assertTrue(0 <= m["disagreement"] <= 1, mid)


class TestTotalsLockedContract(unittest.TestCase):
    @unittest.skipUnless(
        os.path.exists(os.path.join(DATA, "wc26_totals_locked.json")),
        "totals not locked yet")
    def test_forward_locked_totals_are_pre_kickoff_and_coherent(self):
        store = load("wc26_totals_locked.json")
        fids = known_fixture_ids()   # locks cover group + confirmed knockout
        for mid, rec in store["matches"].items():
            self.assertIn(mid, fids, mid)
            self.assertTrue(0 < rec["over_model"] < 1, mid)
            self.assertTrue(0 < rec["over_blend"] < 1, mid)
            if rec["over_market"] is not None:
                self.assertTrue(0 < rec["over_market"] < 1, mid)


class TestAwardsContract(unittest.TestCase):
    def test_award_entries_have_model_probs(self):
        awards = load("wc26_awards.json")
        for cat in ("golden_boot", "top_scorer_nation"):
            self.assertTrue(awards[cat])
            key = "player" if cat == "golden_boot" else "team"
            for entry in awards[cat]:
                self.assertIn(key, entry)
                self.assertTrue(0 <= entry["p_model"] <= 1, entry)


class TestLockedPredictionsContract(unittest.TestCase):
    def test_locked_picks_grade_ready(self):
        pred = load("wc26_predictions.json")
        self.assertEqual(len(pred["group_matches"]), 72)
        for p in pred["group_matches"]:
            self.assertIn(p["pred_result"], "HDA")
            self.assertAlmostEqual(sum(p["p"].values()), 1.0, places=2,
                                   msg=p["match_id"])
        for stage in ("r16", "qf", "sf", "final"):
            self.assertIn(stage, pred["predicted_stage_teams"])


if __name__ == "__main__":
    unittest.main()
