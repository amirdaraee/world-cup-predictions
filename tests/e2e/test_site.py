"""Site-builder unit tests + offline integration tests.

The integration test builds the real site into docs/ (idempotent) and then
audits it: template leaks, broken internal links, page counts, both languages.
"""
import os
import re
import sys
import unittest
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "pipeline"))

import wc26_build_site as b


class TestGoalsGrade(unittest.TestCase):
    """Report-card Goals section: total-goals grading from locked picks."""
    PICKS = [
        {"pred_score": "1-0", "actual_score": "2-0"},  # pred 1, act 2, err 1, U/U
        {"pred_score": "2-1", "actual_score": "2-1"},  # exact 3, O/O
        {"pred_score": "1-1", "actual_score": "0-0"},  # pred 2, act 0, err 2, U/U
        {"pred_score": "2-2", "actual_score": "3-1"},  # pred 4, act 4, exact, O/O
    ]

    def test_totals_and_error(self):
        g = b.goals_grade(self.PICKS)
        self.assertEqual(g["n"], 4)
        self.assertAlmostEqual(g["act_per"], (2 + 3 + 0 + 4) / 4)
        self.assertAlmostEqual(g["pred_per"], (1 + 3 + 2 + 4) / 4)
        self.assertAlmostEqual(g["mae"], (1 + 0 + 2 + 0) / 4)
        self.assertEqual(g["exact"], 2)        # 2-1 and 2-2/3-1 totals match

    def test_over_under_direction(self):
        g = b.goals_grade(self.PICKS)
        # pred>2.5 vs act>2.5: (F,F)hit (T,T)hit (F,F)hit (T,T)hit -> 4/4
        self.assertEqual((g["ou_hit"], g["ou_n"]), (4, 4))

    def test_empty_is_none(self):
        self.assertIsNone(b.goals_grade([]))
        self.assertIsNone(b.goals_grade([{"pred_score": "1-0"}]))  # no actual

    def test_section_renders_and_caveats(self):
        html = b.goals_section(self.PICKS)
        self.assertIn("<h2>Goals</h2>", html)
        self.assertIn("Goals/game actual", html)
        self.assertIn("most-likely", html)   # the honesty caveat is present
        self.assertEqual(b.goals_section([]), "")


class TestTotalsGrade(unittest.TestCase):
    """Calibrated O/U 2.5 grading of the forward-locked totals."""
    LOCKED = {
        "1": {"line": 2.5, "over_model": 0.55, "over_market": 0.60,
              "over_blend": 0.57},   # actual over -> all 'over' calls right
        "2": {"line": 2.5, "over_model": 0.40, "over_market": 0.45,
              "over_blend": 0.42},   # actual under -> all 'under' calls right
        "3": {"line": 2.5, "over_model": 0.70, "over_market": None,
              "over_blend": 0.70},   # market missing -> only model/blend
    }
    ACTUAL = {"1": 4, "2": 1, "3": 0}   # totals: over, under, under

    def test_hits_and_sources(self):
        g = b.totals_grade(self.LOCKED, self.ACTUAL)
        self.assertEqual(g["n"], 3)
        self.assertEqual(g["over_model"]["of"], 3)
        self.assertEqual(g["over_market"]["of"], 2)   # one had no market
        # model calls: .55>.5 over(act over ✓), .40<.5 under(act under ✓),
        #              .70>.5 over(act under ✗) -> 2/3
        self.assertEqual(g["over_model"]["hit"], 2)

    def test_brier_is_squared_error(self):
        g = b.totals_grade({"1": self.LOCKED["1"]}, {"1": 4})  # over=1
        self.assertAlmostEqual(g["over_model"]["brier"], (0.55 - 1) ** 2)

    def test_none_until_a_locked_match_finishes(self):
        self.assertIsNone(b.totals_grade(self.LOCKED, {}))   # no actuals
        self.assertIsNone(b.totals_grade({}, self.ACTUAL))

    def test_unscored_locked_skipped(self):
        g = b.totals_grade(self.LOCKED, {"1": 4})   # only match 1 has a total
        self.assertEqual(g["n"], 1)

    def test_section_renders_when_a_locked_match_finishes(self):
        saved = b.TOTALS_LOCKED
        try:
            b.TOTALS_LOCKED = {"locked_from": "2026-06-14",
                               "matches": {"1": self.LOCKED["1"]}}
            html = b.totals_section([{"match_id": 1, "actual_score": "3-1"}])
            self.assertIn("Totals (Over/Under 2.5)", html)
            self.assertIn("Model", html)
            self.assertIn("pre-kickoff from 2026-06-14", html)
        finally:
            b.TOTALS_LOCKED = saved

    def test_section_absent_with_no_locked_finishers(self):
        saved = b.TOTALS_LOCKED
        try:
            b.TOTALS_LOCKED = {"matches": {"1": self.LOCKED["1"]}}
            self.assertEqual(b.totals_section([]), "")   # nothing graded yet
        finally:
            b.TOTALS_LOCKED = saved


class TestStandings(unittest.TestCase):
    def setUp(self):
        self._saved = [dict(m) for m in b.MATCHES]

    def tearDown(self):
        for m, s in zip(b.MATCHES, self._saved):
            m.clear()
            m.update(s)

    def test_standings_none_before_results(self):
        for m in b.MATCHES:
            m["score"] = None
        self.assertIsNone(b.group_standings("A"))

    def test_standings_order_and_points(self):
        for m in b.MATCHES:
            m["score"] = None
            if m["group"] == "A" and m["matchday"] == 1:
                m["score"] = "2-0" if m["home"] == "Mexico" else "1-1"
        s = b.group_standings("A")
        self.assertEqual(s[0][0], "Mexico")
        self.assertEqual(s[0][1]["pts"], 3)
        self.assertEqual(s[-1][1]["pts"], 0)
        self.assertEqual(sum(r["pts"] for _, r in s), 3 + 1 + 1 + 0)


class TestHelpers(unittest.TestCase):
    def test_actual_result(self):
        self.assertEqual(b.actual_result("2-1"), "home")
        self.assertEqual(b.actual_result("0-0"), "draw")
        self.assertEqual(b.actual_result("0-3"), "away")

    def test_no_live_chip_rendered(self):
        """Static pages can't know liveness; the LIVE badge was removed
        as misleading (matches between rebuilds showed it for hours)."""
        for name in ("index.html", "matches.html"):
            self.assertNotIn("livechip", (Path(b.OUT) / name).read_text(),
                             name)

    def test_slug_accents(self):
        self.assertEqual(b.slug("Kylian Mbappé"), "kylian-mbappe")
        self.assertEqual(b.slug("Curaçao"), "curacao")
        self.assertEqual(b.slug("Brahim Díaz"), "brahim-diaz")

    def test_ko_slug_suffix(self):
        m = {"home": "Mexico", "away": "Japan", "round": "Round of 32"}
        self.assertEqual(b.match_slug(m), "mexico-vs-japan-r32")


class TestCompleteness(unittest.TestCase):
    def test_every_team_has_flag(self):
        for t in b.TEAMS:
            self.assertTrue(b.flag(t), f"no flag for {t}")

    def test_every_team_has_id(self):
        for t in b.TEAMS:
            self.assertIn(t, b.TEAM_IDS)

    def test_every_team_has_squad_value(self):
        import json
        vals = json.load(open(Path(b.DATA) / "wc26_squad_values.json"))["values"]
        for t in b.TEAMS:
            self.assertIn(t, vals, f"no Transfermarkt value for {t}")

    def test_sims_cover_all_fixtures(self):
        for m in b.MATCHES:
            self.assertIn(str(m["match_id"]), b.SIMS)

    def test_sims_probabilities_valid(self):
        for mid, s in b.SIMS.items():
            ml = s["moneyline"]
            self.assertAlmostEqual(sum(ml.values()), 1.0, places=2, msg=mid)
            t = s["totals"]
            self.assertGreaterEqual(t["over_1.5"] + 1e-9, t["over_2.5"])
            self.assertGreaterEqual(t["over_2.5"] + 1e-9, t["over_3.5"])

    def test_sims_new_surfaces_valid(self):
        for mid, s in b.SIMS.items():
            for key in ("halftime", "second_half", "first_to_score"):
                self.assertAlmostEqual(sum(s[key].values()), 1.0,
                                       places=2, msg=f"{mid} {key}")
            for side in ("home", "away"):
                t = s["team_totals"][side]
                self.assertGreaterEqual(t["over_0.5"] + 1e-9, t["over_1.5"])
                self.assertGreaterEqual(t["over_1.5"] + 1e-9, t["over_2.5"])

    def test_sims_exact_scores_valid(self):
        for mid, s in b.SIMS.items():
            es = s["exact_scores"]
            self.assertEqual(len(es), 17, mid)   # 0-0..3-3 + other
            self.assertAlmostEqual(sum(es.values()), 1.0, places=2, msg=mid)
            top = s["top_scores"][0]   # mode can exceed 3 goals in blowouts
            if top["score"] in es:
                self.assertAlmostEqual(es[top["score"]], top["p"],
                                       places=4, msg=mid)


class TestBuiltSite(unittest.TestCase):
    """Integration: build and audit the artifact — into a TEMP dir, so the
    test suite never rewrites the production docs/ tree."""

    @classmethod
    def setUpClass(cls):
        import tempfile
        cls._tmp = tempfile.TemporaryDirectory()
        cls._out_saved = b.OUT
        b.OUT = Path(cls._tmp.name)
        b.build_all(snapshot=False)
        cls.docs = Path(b.OUT)
        cls.pages = list(cls.docs.glob("*.html")) \
            + list((cls.docs / "teams").glob("*.html")) \
            + list((cls.docs / "matches").glob("*.html")) \
            + list((cls.docs / "players").glob("*.html"))

    @classmethod
    def tearDownClass(cls):
        b.OUT = cls._out_saved
        cls._tmp.cleanup()

    def test_glossary_page(self):
        """The newcomer glossary builds, is linked in the nav, and defines
        the core terms across the field."""
        gloss = self.docs / "glossary.html"
        self.assertTrue(gloss.exists())
        html = gloss.read_text()
        for term in ("Probability", "Dixon", "Expected goals", "Over / Under",
                     "Edge", "Brier score", "Closing-line value", "Look-ahead"):
            self.assertIn(term, html, term)
        self.assertIn("dl class=\"glossary\"", html)
        # linked from the shared nav (check a deep page)
        deep = next(p for p in self.pages if p.parent.name == "matches")
        self.assertIn("glossary.html", deep.read_text())

    def test_analytics_is_consent_gated(self):
        """GA must never auto-load: no static gtag.js <script> tag, consent
        defaults to denied, and the consent banner ships on every page."""
        html = (self.docs / "index.html").read_text()
        self.assertNotIn(
            '<script async src="https://www.googletagmanager.com/gtag/js',
            html, "GA is auto-loading without consent")
        self.assertIn("analytics_storage: 'denied'", html)
        self.assertIn('id="cookie-consent"', html)
        # the banner is in the shared page template -> on a deep page too
        deep = next(p for p in self.pages if p.parent.name == "matches")
        self.assertIn('id="cookie-consent"', deep.read_text())

    def test_report_page_graded_mode(self):
        """Inject synthetic results and verify every report section renders."""
        import copy
        saved = copy.deepcopy(b.PRED)
        try:
            for p in b.PRED["group_matches"][:6]:
                p["actual_score"] = "2-1"
                p["actual_result"] = "H"
                p["hit"] = p["pred_result"] == "H"
            b.PRED["accuracy"] = {
                "graded_group_matches": 6, "result_hits": 4,
                "result_pct": 0.667, "exact_score_hits": 1,
                "exact_score_pct": 0.167, "brier": 0.55,
                "market_priced_matches": 5,
                "compare": {"blend": {"brier": 0.55, "logloss": 0.9},
                            "model": {"brier": 0.56, "logloss": 0.92},
                            "market": {"brier": 0.54, "logloss": 0.89}},
                "stages": {}, "champion_correct": None}
            b.build_report()
            html = (Path(b.OUT) / "report.html").read_text()
            for needle in ("Who forecasts best", "Matchday by matchday",
                           "Sharpest calls", "Calibration so far"):
                self.assertIn(needle, html)
            # reframed headline leads with model log-loss vs the market,
            # not raw hit-rate (model 0.92 vs market 0.89 -> behind by 0.030)
            self.assertIn("Model log-loss", html)
            self.assertIn("vs the market", html)
            self.assertIn("behind by 0.030", html)
            self.assertIn("log-loss against the market", html)
            self.assertNotIn("{", html.split("<main")[1].split("</main>")[0]
                             .replace("{ ", ""))
        finally:
            b.PRED.clear()
            b.PRED.update(saved)
            b.build_report()

    def test_page_counts(self):
        self.assertEqual(len(list((self.docs / "teams").glob("*.html"))), 48)
        self.assertEqual(len(list((self.docs / "matches").glob("*.html"))),
                         len(b.MATCHES) + len(b.KOS))
        self.assertGreaterEqual(
            len(list((self.docs / "players").glob("*.html"))), 30)
        for p in ("index", "groups", "matches", "futures", "awards",
                  "bracket", "report", "follow-the-model", "method",
                  "method-fa", "archive"):
            self.assertTrue((self.docs / f"{p}.html").exists(), p)
        # bracket.html = the locked picks, un-folded from the report
        self.assertIn("The predicted tournament",
                      (self.docs / "bracket.html").read_text())

    def test_no_template_leaks(self):
        leak = re.compile(r"\{(?:inline_svg|grid_inline|escape\(|m\[|pride|wrap_)")
        for p in self.pages:
            self.assertIsNone(leak.search(p.read_text()),
                              f"template leak in {p.name}")

    def test_internal_links_resolve(self):
        href = re.compile(r'href="([^"#]+?)(?:#[^"]*)?"')
        broken = []
        for p in self.pages:
            base = p.parent
            for link in href.findall(p.read_text()):
                if link.startswith(("http", "mailto", "data:")):
                    continue
                target = (base / link.split("?")[0]).resolve()
                if not target.exists():
                    broken.append(f"{p.relative_to(self.docs)} -> {link}")
        self.assertFalse(broken[:10], f"{len(broken)} broken links")

    def test_method_evolution_chart_both_languages(self):
        for name, heading in (("method.html",
                               "What each iteration did to the numbers"),
                              ("method-fa.html",
                               "هر تکرار، چقدر پیش‌بینی را جابه‌جا کرد")):
            html = (self.docs / name).read_text()
            self.assertIn(heading, html, name)
            sec = html.split(heading)[1]
            self.assertGreaterEqual(sec.count("<polyline"), 3, name)
            self.assertIn("squad-value prior", sec, name)

    def test_method_timeline_both_languages(self):
        for name, heading in (("method.html", "How the method evolved"),
                              ("method-fa.html", "سیر تکامل روش")):
            html = (self.docs / name).read_text()
            self.assertIn(heading, html, name)
            tl = html.split('<ol class="timeline">')[1].split("</ol>")[0]
            self.assertGreaterEqual(tl.count("<li>"), 8, name)

    def test_second_opinion_flag_only_on_match_pages(self):
        """Option 2: match pages carry the disagreement FLAG, never the
        Elo per-team percentages (no number to average in). The Elo
        figures and the failed-blend finding live on the method page."""
        import glob as g
        pages = g.glob(str(self.docs / "matches" / "*.html"))
        with_op = [p for p in pages if "Second opinion" in open(p).read()]
        self.assertGreaterEqual(len(with_op), 30)
        # the flag links to the method page rather than restating Elo odds
        for p in with_op:
            self.assertIn("method.html#second-opinion", open(p).read())
        # the actual Elo numbers belong on the method page, not match pages
        for name, phrase, blend in (("method.html", "A second opinion",
                                     "0.831"),
                                    ("method-fa.html", "نظر دوم", "۰٫۸۳۱")):
            html = (self.docs / name).read_text()
            self.assertIn(phrase, html, name)
            self.assertIn(blend, html, name)   # the rejected blend number
        self.assertIn("current elo ratings",
                      (self.docs / "method.html").read_text().lower())

    def test_method_news_gate_disclosure_both_languages(self):
        """The site's honesty claim must track reality: the AI analyst
        section discloses the reduce-only betting news gate."""
        for name, phrase in (("method.html", "reduce-only by construction"),
                             ("method-fa.html", "فقط کاهنده است")):
            html = (self.docs / name).read_text()
            self.assertIn(phrase, html, name)

    def test_farsi_page_rtl_and_charts(self):
        fa = (self.docs / "method-fa.html").read_text()
        self.assertIn('dir="rtl"', fa)
        self.assertGreaterEqual(fa.count("<svg"), 4)

    def test_og_tags_everywhere(self):
        for p in self.pages[:20]:
            self.assertIn('property="og:title"', p.read_text(), p.name)

    def test_cname_emitted(self):
        """Pages custom domain: build must emit docs/CNAME every rebuild."""
        self.assertEqual((self.docs / "CNAME").read_text().strip(),
                         "wcformbook.com")

    def test_og_image_on_custom_domain(self):
        html = (self.docs / "index.html").read_text()
        self.assertIn('property="og:image" '
                      'content="https://wcformbook.com/', html)


if __name__ == "__main__":
    unittest.main()
