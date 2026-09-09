import os
import tempfile
import unittest

from tokenaudit.loader import parse_file
from tokenaudit.pricing import Pricing
from tokenaudit.rules import DEFAULT_THRESHOLDS, detect, detect_cache_misses
from tests.fixtures.builder import Rollout, compacted_line, meta_line, turn_context_line, write_rollout

PRICING = Pricing.load(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "skills", "token-audit", "scripts", "pricing.json"))
SID = "019a0000-0000-7000-8000-000000000001"
TH = dict(DEFAULT_THRESHOLDS)


class CacheMissRulesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _session(self, lines):
        return parse_file(write_rollout(self.tmp.name, SID, lines), SID)

    def test_r1_after_idle_gap(self):
        r = Rollout()
        s = self._session([
            meta_line(SID, "2026-09-01T10:00:00Z"), turn_context_line("2026-09-01T10:00:00Z", model="gpt-5.5"),
            r.token_count_line("2026-09-01T10:00:00Z", {"input": 80000, "cached": 0}),
            r.token_count_line("2026-09-01T13:00:00Z", {"input": 81000, "cached": 0}),
        ])
        f = detect_cache_misses(s, PRICING, TH)
        self.assertEqual([x.rule for x in f], ["R1"])
        ev = f[0].evidence
        self.assertEqual((ev["gap_min"], ev["ttl_min"], ev["missed_tokens"], ev["context_tokens"]),
                         (180, 10, 80000, 81000))
        self.assertAlmostEqual(f[0].waste_usd, round(80000 * 0.9 * 5e-6, 4))

    def test_cached_prefix_is_not_a_miss(self):
        r = Rollout()
        s = self._session([
            meta_line(SID, "2026-09-01T10:00:00Z"), turn_context_line("2026-09-01T10:00:00Z"),
            r.token_count_line("2026-09-01T10:00:00Z", {"input": 80000}),
            r.token_count_line("2026-09-01T13:00:00Z", {"input": 82000, "cached": 79872}),
        ])
        self.assertEqual(detect_cache_misses(s, PRICING, TH), [])

    def test_r2_mid_session_miss_with_causes(self):
        r = Rollout()
        s = self._session([
            meta_line(SID, "2026-09-01T10:00:00Z"),
            turn_context_line("2026-09-01T10:00:00Z", model="gpt-5.5", approval="on-request"),
            r.token_count_line("2026-09-01T10:00:00Z", {"input": 120000, "cached": 100000}),
            turn_context_line("2026-09-01T10:01:00Z", model="gpt-5.6-terra", approval="never"),
            compacted_line("2026-09-01T10:01:30Z"),
            r.token_count_line("2026-09-01T10:02:00Z", {"input": 130000, "cached": 0}),
        ])
        f = detect_cache_misses(s, PRICING, TH)
        self.assertEqual([x.rule for x in f], ["R2"])
        ev = f[0].evidence
        self.assertEqual(ev["missed_tokens"], 120000)
        self.assertIn("mode_change", ev["causes"])
        self.assertIn("compaction", ev["causes"])
        self.assertIn("model_change:gpt-5.5->gpt-5.6-terra", ev["causes"])
        # priced at the model of the missing turn (terra: $2/M)
        self.assertAlmostEqual(f[0].waste_usd, round(120000 * 0.9 * 2e-6, 4))

    def test_small_miss_below_threshold_ignored(self):
        r = Rollout()
        s = self._session([
            meta_line(SID, "2026-09-01T10:00:00Z"), turn_context_line("2026-09-01T10:00:00Z"),
            r.token_count_line("2026-09-01T10:00:00Z", {"input": 30000}),
            r.token_count_line("2026-09-01T10:01:00Z", {"input": 31000, "cached": 0}),
        ])
        self.assertEqual(detect_cache_misses(s, PRICING, TH), [])

    def test_first_turn_never_flagged(self):
        s = self._session([meta_line(SID, "2026-09-01T10:00:00Z"), turn_context_line("2026-09-01T10:00:00Z"),
                           Rollout().token_count_line("2026-09-01T10:00:00Z", {"input": 500000})])
        self.assertEqual(detect_cache_misses(s, PRICING, TH), [])

    def test_detect_aggregates_and_threshold_override(self):
        r = Rollout()
        s = self._session([
            meta_line(SID, "2026-09-01T10:00:00Z"), turn_context_line("2026-09-01T10:00:00Z"),
            r.token_count_line("2026-09-01T10:00:00Z", {"input": 10000}),
            r.token_count_line("2026-09-01T13:00:00Z", {"input": 10000, "cached": 0}),
        ])
        self.assertEqual(detect(s, PRICING), [])
        f = detect(s, PRICING, {"r1_min_missed_tokens": 5000})
        self.assertEqual([x.rule for x in f], ["R1"])
        self.assertEqual(f[0].project, "/home/me/proj")
        f2 = detect(s, PRICING, {"r1_min_missed_tokens": 5000, "cache_ttl_min": 600, "r2_min_missed_tokens": 5000})
        self.assertEqual([x.rule for x in f2], ["R2"])


if __name__ == "__main__":
    unittest.main()
