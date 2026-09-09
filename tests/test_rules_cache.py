import os
import tempfile
import unittest

from tokenaudit.loader import parse_file
from tokenaudit.pricing import Pricing
from tokenaudit.rules import DEFAULT_THRESHOLDS, detect, detect_cache_rewrites
from tests.fixtures.builder import assistant_line, mode_line, write_session

PRICING = Pricing.load(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "skills", "token-audit", "scripts", "pricing.json"))


class CacheRewriteRulesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _session(self, lines):
        return parse_file(write_session(self.tmp.name, "p", "s", lines), "s", "p")

    def test_r1_after_ttl_gap_with_1h_ttl(self):
        s = self._session([
            assistant_line("m1", "2026-09-01T10:00:00Z", usage={"c1": 30000}),
            assistant_line("m2", "2026-09-01T13:00:00Z", usage={"c1": 77503, "read": 100}),
        ])
        f = detect_cache_rewrites(s, PRICING, dict(DEFAULT_THRESHOLDS))
        self.assertEqual([x.rule for x in f], ["R1"])
        self.assertEqual(f[0].evidence["gap_min"], 180)
        self.assertEqual(f[0].evidence["ttl_min"], 60)
        self.assertEqual(f[0].evidence["cache_write_tokens"], 77503)
        # opus-5: 77503 * (2.0 - 0.1) * 5e-6
        self.assertAlmostEqual(f[0].waste_usd, round(77503 * 1.9 * 5e-6, 4))

    def test_r1_uses_5m_ttl_when_no_1h_write(self):
        s = self._session([
            assistant_line("m1", "2026-09-01T10:00:00Z", usage={"c5": 30000}),
            assistant_line("m2", "2026-09-01T10:06:00Z", usage={"c5": 25000}),
        ])
        f = detect_cache_rewrites(s, PRICING, dict(DEFAULT_THRESHOLDS))
        self.assertEqual([x.rule for x in f], ["R1"])
        self.assertEqual(f[0].evidence["ttl_min"], 5)

    def test_gap_within_ttl_is_not_r1(self):
        s = self._session([
            assistant_line("m1", "2026-09-01T10:00:00Z", usage={"c1": 30000}),
            assistant_line("m2", "2026-09-01T10:30:00Z", usage={"c1": 25000}),
        ])
        self.assertEqual(detect_cache_rewrites(s, PRICING, dict(DEFAULT_THRESHOLDS)), [])

    def test_r2_mid_session_rewrite_with_causes(self):
        s = self._session([
            assistant_line("m1", "2026-09-01T10:00:00Z", model="claude-opus-5", usage={"c1": 30000}),
            mode_line(),
            assistant_line("m2", "2026-09-01T10:01:00Z", model="claude-sonnet-5",
                           usage={"c1": 231369, "read": 10}),
        ])
        f = detect_cache_rewrites(s, PRICING, dict(DEFAULT_THRESHOLDS))
        self.assertEqual([x.rule for x in f], ["R2"])
        self.assertIn("mode_change", f[0].evidence["causes"])
        self.assertIn("model_change:claude-opus-5->claude-sonnet-5", f[0].evidence["causes"])
        self.assertEqual(f[0].evidence["cache_write_tokens"], 231369)

    def test_first_turn_never_flagged(self):
        s = self._session([assistant_line("m1", "2026-09-01T10:00:00Z", usage={"c1": 500000})])
        self.assertEqual(detect_cache_rewrites(s, PRICING, dict(DEFAULT_THRESHOLDS)), [])

    def test_detect_aggregates_and_threshold_override(self):
        s = self._session([
            assistant_line("m1", "2026-09-01T10:00:00Z", usage={"c1": 30000}),
            assistant_line("m2", "2026-09-01T13:00:00Z", usage={"c1": 10000}),
        ])
        self.assertEqual(detect(s, PRICING), [])
        f = detect(s, PRICING, {"r1_min_cache_write": 5000})
        self.assertEqual([x.rule for x in f], ["R1"])
        self.assertEqual(f[0].session_name, "s")
        self.assertEqual(f[0].project, "p")


if __name__ == "__main__":
    unittest.main()
