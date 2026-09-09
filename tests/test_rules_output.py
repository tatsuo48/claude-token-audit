import os
import tempfile
import unittest

from tokenaudit.loader import parse_file
from tokenaudit.pricing import Pricing
from tokenaudit.rules import DEFAULT_THRESHOLDS, detect, detect_large_writes, detect_long_session
from tests.fixtures.builder import assistant_line, tool_use, write_session

PRICING = Pricing.load(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "skills", "token-audit", "scripts", "pricing.json"))
TH = dict(DEFAULT_THRESHOLDS)


def ts(i):
    return "2026-09-01T%02d:%02d:00Z" % (10 + i // 60, i % 60)


class LargeWriteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _session(self, lines):
        return parse_file(write_session(self.tmp.name, "p", "s", lines), "s", "p")

    def test_r5_large_write_priced_at_output_rate(self):
        s = self._session([assistant_line(
            "m1", ts(0), model="claude-fable-5",
            content=[tool_use("t1", "Write", file_path="/gen.md", content="w" * 20000)])])
        f = detect_large_writes(s, PRICING, TH)
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].evidence["kind"], "large_write")
        self.assertEqual(f[0].evidence["est_tokens"], 5000)
        self.assertAlmostEqual(f[0].waste_usd, round(5000 * 50e-6, 4))

    def test_r5_repeated_write_same_path(self):
        s = self._session([
            assistant_line("m1", ts(0), content=[tool_use("t1", "Write", file_path="/a.py", content="a" * 4000)]),
            assistant_line("m2", ts(1), content=[tool_use("t2", "Write", file_path="/a.py", content="b" * 8000)]),
        ])
        f = detect_large_writes(s, PRICING, TH)
        kinds = sorted(x.evidence["kind"] for x in f)
        self.assertEqual(kinds, ["repeated_write"])
        rep = f[0]
        self.assertEqual(rep.evidence["writes"], 2)
        self.assertEqual(rep.evidence["extra_est_tokens"], 2000)
        self.assertAlmostEqual(rep.waste_usd, round(2000 * 25e-6, 4))

    def test_small_single_write_not_flagged(self):
        s = self._session([assistant_line(
            "m1", ts(0), content=[tool_use("t1", "Write", file_path="/s", content="s" * 100)])])
        self.assertEqual(detect_large_writes(s, PRICING, TH), [])

    def test_r5_large_write_without_file_path_omits_key(self):
        s = self._session([assistant_line(
            "m1", ts(0), content=[tool_use("t1", "Write", content="w" * 20000)])])
        f = detect_large_writes(s, PRICING, TH)
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].evidence["kind"], "large_write")
        self.assertNotIn("file_path", f[0].evidence)
        self.assertEqual(f[0].evidence["est_tokens"], 5000)


class LongSessionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _session(self, lines):
        return parse_file(write_session(self.tmp.name, "p", "s", lines), "s", "p")

    def test_r6_by_context_size(self):
        s = self._session([
            assistant_line("m1", ts(0), usage={"read": 100000}),
            assistant_line("m2", ts(1), usage={"read": 160000}),
            assistant_line("m3", ts(2), usage={"read": 200000}),
        ])
        f = detect_long_session(s, PRICING, TH)
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].evidence["turns"], 3)
        self.assertEqual(f[0].evidence["final_context_tokens"], 200000)
        self.assertAlmostEqual(f[0].waste_usd, round((10000 + 50000) * 0.1 * 5e-6, 4))

    def test_r6_by_turn_count(self):
        lines = [assistant_line("m%d" % i, ts(i), usage={"read": 1000}) for i in range(150)]
        f = detect_long_session(self._session(lines), PRICING, TH)
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].evidence["turns"], 150)
        self.assertEqual(f[0].waste_usd, 0.0)

    def test_short_small_session_not_flagged(self):
        s = self._session([assistant_line("m1", ts(0), usage={"read": 1000})])
        self.assertEqual(detect_long_session(s, PRICING, TH), [])

    def test_detect_runs_all_rules(self):
        lines = [assistant_line("m%d" % i, ts(i), usage={"read": 160000},
                                content=[tool_use("t%d" % i, "Write", file_path="/o", content="o" * 20000)])
                 for i in range(2)]
        rules = sorted(set(f.rule for f in detect(self._session(lines), PRICING)))
        self.assertEqual(rules, ["R5", "R6"])


if __name__ == "__main__":
    unittest.main()
