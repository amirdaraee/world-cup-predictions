"""Unit: knockout advancer grading in the accuracy tracker — pure function
only (synthetic fixtures, no file I/O)."""
import math
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "pipeline"))

from wc26_accuracy_track import ko_grade


def fixture(mid, status="Match Finished", score=None, penalties=None):
    return {"match_id": mid, "status": status, "score": score,
            "penalties": penalties}


def sim(home, draw, away):
    return {"moneyline": {"home": home, "draw": draw, "away": away}}


class TestKoGrade(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(ko_grade([], {}), {"graded": 0})

    def test_clean_win_hit(self):
        # model favours home (0.5 + 0.3/2 = 0.65), home wins 2-0 -> hit
        ko = [fixture(1, score="2-0")]
        sims = {"1": sim(0.5, 0.3, 0.2)}
        out = ko_grade(ko, sims)
        self.assertEqual(out["graded"], 1)
        self.assertEqual(out["hits"], 1)
        self.assertAlmostEqual(out["logloss"], round(-math.log(0.65), 4))

    def test_penalties_decider_on_level_tie(self):
        # 1-1 after 90', away wins the shootout 3-4; model favoured home
        # (0.55 adv) -> miss, log-loss on P(away adv) = 0.45
        ko = [fixture(2, score="1-1", penalties="3-4")]
        sims = {"2": sim(0.4, 0.3, 0.3)}
        out = ko_grade(ko, sims)
        self.assertEqual(out["graded"], 1)
        self.assertEqual(out["hits"], 0)
        self.assertAlmostEqual(out["logloss"], round(-math.log(0.45), 4))

    def test_unfinished_tie_ignored(self):
        ko = [fixture(3, status="Second Half", score="1-0"),
              fixture(4, status="Not Started")]
        sims = {"3": sim(0.5, 0.3, 0.2), "4": sim(0.5, 0.3, 0.2)}
        self.assertEqual(ko_grade(ko, sims), {"graded": 0})

    def test_level_tie_without_penalties_skipped(self):
        # finished 0-0 but no shootout recorded: nothing decided yet
        ko = [fixture(5, score="0-0", penalties=None)]
        sims = {"5": sim(0.4, 0.3, 0.3)}
        self.assertEqual(ko_grade(ko, sims), {"graded": 0})

    def test_hits_and_logloss_arithmetic(self):
        # tie A: home 3-1, model picks home @ 0.65 adv -> hit, ll -ln(0.65)
        # tie B: away 0-2, model picks home @ 0.60 adv -> miss, ll -ln(0.40)
        # tie C: 2-2, home wins pens 5-4, model picks home @ 0.55 -> hit
        ko = [fixture(10, score="3-1"),
              fixture(11, score="0-2"),
              fixture(12, score="2-2", penalties="5-4")]
        sims = {"10": sim(0.5, 0.3, 0.2),
                "11": sim(0.5, 0.2, 0.3),
                "12": sim(0.4, 0.3, 0.3)}
        out = ko_grade(ko, sims)
        self.assertEqual(out["graded"], 3)
        self.assertEqual(out["hits"], 2)
        want = (-math.log(0.65) - math.log(0.40) - math.log(0.55)) / 3
        self.assertAlmostEqual(out["logloss"], round(want, 4))

    def test_missing_sim_skipped(self):
        ko = [fixture(20, score="1-0")]
        self.assertEqual(ko_grade(ko, {}), {"graded": 0})


if __name__ == "__main__":
    unittest.main()
