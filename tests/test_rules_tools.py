import os
import tempfile
import unittest

from tokenaudit.loader import parse_file
from tokenaudit.pricing import Pricing
from tokenaudit.rules import DEFAULT_THRESHOLDS, detect, detect_large_results, detect_repeated_reads
from tests.fixtures.builder import assistant_line, tool_result_line, tool_use, write_session

PRICING = Pricing.load(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "skills", "token-audit", "scripts", "pricing.json"))
TH = dict(DEFAULT_THRESHOLDS)


def ts(minute):
    return "2026-09-01T10:%02d:00Z" % minute


class LargeResultTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _session(self, lines):
        return parse_file(write_session(self.tmp.name, "p", "s", lines), "s", "p")

    def test_r3_large_read_result_counts_remaining_turns(self):
        big = "x" * 40000  # ~10k tokens
        s = self._session([
            assistant_line("m1", ts(0), usage={"c5": 100},
                           content=[tool_use("t1", "Read", file_path="/big.log")]),
            tool_result_line("t1", ts(1), big),
            assistant_line("m2", ts(2)),
            assistant_line("m3", ts(3)),
        ])
        f = detect_large_results(s, PRICING, TH)
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].rule, "R3")
        self.assertEqual(f[0].evidence["tool"], "Read")
        self.assertEqual(f[0].evidence["file_path"], "/big.log")
        self.assertEqual(f[0].evidence["remaining_turns"], 2)
        tokens = f[0].evidence["est_tokens"]
        self.assertEqual(tokens, (40000 + 2) // 4)
        # opus-5, 5m TTL: tokens * (2 * 0.1 + 1.25) * 5e-6
        self.assertAlmostEqual(f[0].waste_usd, round(tokens * (2 * 0.1 + 1.25) * 5e-6, 4))

    def test_r3_bash_reports_command_head_only(self):
        s = self._session([
            assistant_line("m1", ts(0), content=[tool_use("t1", "Bash", command="cat /etc/hosts secret")]),
            tool_result_line("t1", ts(1), "y" * 40000),
        ])
        f = detect_large_results(s, PRICING, TH)
        self.assertEqual(f[0].evidence["command"], "cat")
        self.assertNotIn("secret", str(f[0].evidence))

    def test_small_result_not_flagged(self):
        s = self._session([
            assistant_line("m1", ts(0), content=[tool_use("t1", "Read", file_path="/a")]),
            tool_result_line("t1", ts(1), "z" * 1000),
        ])
        self.assertEqual(detect_large_results(s, PRICING, TH), [])


class RepeatedReadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _session(self, lines):
        return parse_file(write_session(self.tmp.name, "p", "s", lines), "s", "p")

    def test_r4_three_reads_of_same_file(self):
        lines = []
        for i in range(3):
            lines.append(assistant_line("m%d" % i, ts(i), usage={"c1": 10},
                                        content=[tool_use("t%d" % i, "Read", file_path="/same.py")]))
            lines.append(tool_result_line("t%d" % i, ts(i), "r" * 400))
        s = self._session(lines)
        f = detect_repeated_reads(s, PRICING, TH)
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].rule, "R4")
        self.assertEqual(f[0].evidence["file_path"], "/same.py")
        self.assertEqual(f[0].evidence["reads"], 3)
        extra = 2 * ((400 + 2) // 4)
        self.assertEqual(f[0].evidence["extra_est_tokens"], extra)
        # session used 1h TTL -> w1 = 2.0
        self.assertAlmostEqual(f[0].waste_usd, round(extra * 2.0 * 5e-6, 4))

    def test_two_reads_not_flagged(self):
        s = self._session([
            assistant_line("m1", ts(0), content=[tool_use("t1", "Read", file_path="/x")]),
            assistant_line("m2", ts(1), content=[tool_use("t2", "Read", file_path="/x")]),
        ])
        self.assertEqual(detect_repeated_reads(s, PRICING, TH), [])

    def test_detect_includes_r3_and_r4(self):
        lines = [assistant_line("m0", ts(0), content=[tool_use("t0", "Read", file_path="/f")]),
                 tool_result_line("t0", ts(0), "q" * 40000)]
        for i in range(1, 3):
            lines.append(assistant_line("m%d" % i, ts(i), content=[tool_use("t%d" % i, "Read", file_path="/f")]))
            lines.append(tool_result_line("t%d" % i, ts(i), "q" * 40))
        rules = sorted(f.rule for f in detect(self._session(lines), PRICING))
        self.assertEqual(rules, ["R3", "R4"])


if __name__ == "__main__":
    unittest.main()
