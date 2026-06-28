"""Integration: betting components against real repo state — the merged
config, the (gitignored) ledger, run.py preflight over actual data files.
No network, no orders."""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "pipeline"))

from betting.find_bets import CFG


class TestMergedConfig(unittest.TestCase):
    """Whatever committed + local merge to, the caps must stay coherent."""
    def test_caps_sane(self):
        self.assertGreater(CFG["max_total_stake_usdc"], 0)
        self.assertGreater(CFG["max_per_bet_usdc"], 0)
        self.assertLessEqual(CFG["max_per_bet_usdc"],
                             CFG["max_total_stake_usdc"])
        self.assertTrue(0 < CFG["kelly_fraction"] <= 1)
        self.assertTrue(0 < CFG["min_edge_match"] < 0.5)

    def test_merge_kept_all_gates(self):
        """Deep-merge regression: a local include must never drop gates."""
        committed = json.load(open(os.path.join(ROOT, "betting", "config.json")))
        self.assertEqual(set(CFG["include"]), set(committed["include"]))


class TestRunPreflight(unittest.TestCase):
    def test_required_data_present_in_repo(self):
        import betting.run as run
        for f in run.REQUIRED_DATA:
            self.assertTrue(os.path.exists(os.path.join(run.DATA, f)), f)

    def test_stale_model_blocks_and_allow_stale_overrides(self):
        import betting.run as run
        saved = run.CFG.get("max_sims_age_hours")
        try:
            run.CFG["max_sims_age_hours"] = -1   # everything is "stale"
            self.assertTrue(any("--allow-stale" in f
                                for f in run.preflight(False, False)))
            self.assertFalse(any("--allow-stale" in f
                                 for f in run.preflight(False, True)))
        finally:
            run.CFG["max_sims_age_hours"] = saved


class TestLedgerInvariants(unittest.TestCase):
    """Run only where the (gitignored) ledger exists - i.e. on this machine."""
    LEDGER = os.path.join(ROOT, "betting", "state", "ledger.json")

    @unittest.skipUnless(os.path.exists(LEDGER), "no local ledger")
    def test_ledger_respects_caps(self):
        led = json.load(open(self.LEDGER))["placed"]
        total = sum(b["stake_usdc"] for b in led)
        self.assertLessEqual(total, CFG["max_total_stake_usdc"] + 0.01)
        for b in led:
            self.assertLessEqual(b["stake_usdc"],
                                 25.01,  # historical per-bet cap high-water
                                 b["bet"])

    @unittest.skipUnless(os.path.exists(LEDGER), "no local ledger")
    def test_no_market_held_both_sides(self):
        """The invariant that matters: never hold two DIFFERENT tokens for the
        same market (both sides locks in a loss). Repeat entries on the SAME
        token are legitimate top-ups (place_bets --allow-topup), so duplicate
        tokens/questions are fine as long as each market keeps to one side."""
        led = json.load(open(self.LEDGER))["placed"]
        by_q = {}
        for b in led:
            if b.get("question"):
                by_q.setdefault(b["question"], set()).add(b["token_id"])
        both = {q: t for q, t in by_q.items() if len(t) > 1}
        self.assertEqual(both, {}, f"markets held on both sides: {both}")


if __name__ == "__main__":
    unittest.main()
