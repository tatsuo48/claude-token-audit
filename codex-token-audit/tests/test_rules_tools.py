import os
import tempfile
import unittest

from tokenaudit.loader import parse_file
from tokenaudit.pricing import Pricing
from tokenaudit.rules import DEFAULT_THRESHOLDS, detect, detect_large_results, detect_repeated_reads
from tests.fixtures.builder import (Rollout, function_call_line, meta_line, output_line, shell_line,
                                    turn_context_line, write_rollout)

PRICING = Pricing.load(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "skills", "token-audit", "scripts", "pricing.json"))
SID = "019a0000-0000-7000-8000-000000000001"
TH = dict(DEFAULT_THRESHOLDS)


def ts(minute):
    return "2026-09-01T10:%02d:00Z" % minute


class LargeResultTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _session(self, lines):
        return parse_file(write_rollout(self.tmp.name, SID, lines), SID)

    def test_r3_large_shell_output_counts_remaining_turns(self):
        r = Rollout()
        s = self._session([
            meta_line(SID, ts(0)), turn_context_line(ts(0), model="gpt-5.5"),
            shell_line(ts(0), "c1", "cat build.log secret-arg"),
            r.token_count_line(ts(0), {"input": 100}),
            output_line(ts(1), "c1", "x" * 40000),
            r.token_count_line(ts(2), {"input": 10100, "cached": 100}),
            r.token_count_line(ts(3), {"input": 10200, "cached": 10100}),
        ])
        f = detect_large_results(s, PRICING, TH)
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].rule, "R3")
        ev = f[0].evidence
        self.assertEqual((ev["tool"], ev["command"], ev["file_path"], ev["remaining_turns"]),
                         ("shell", "cat", "secret-arg", 2))
        self.assertEqual(ev["est_tokens"], 10000)
        self.assertAlmostEqual(f[0].waste_usd, round(10000 * (1 + 2 * 0.1) * 5e-6, 4))
        self.assertNotIn("build.log secret", str(ev))

    def test_small_result_not_flagged(self):
        r = Rollout()
        s = self._session([
            meta_line(SID, ts(0)), turn_context_line(ts(0)),
            shell_line(ts(0), "c1", "cat a"), r.token_count_line(ts(0), {"input": 100}),
            output_line(ts(1), "c1", "z" * 1000),
        ])
        self.assertEqual(detect_large_results(s, PRICING, TH), [])


class RepeatedReadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _session(self, lines):
        return parse_file(write_rollout(self.tmp.name, SID, lines), SID)

    def test_r4_three_reads_of_same_file(self):
        r = Rollout()
        lines = [meta_line(SID, ts(0)), turn_context_line(ts(0), model="gpt-5.5")]
        for i in range(3):
            cmd = ["cat src/same.py", "sed -n '1,40p' src/same.py", "cat src/same.py"][i]
            lines.append(shell_line(ts(i), "c%d" % i, cmd))
            lines.append(r.token_count_line(ts(i), {"input": 100 * (i + 1)}))
            lines.append(output_line(ts(i), "c%d" % i, "r" * 400))
        f = detect_repeated_reads(self._session(lines), PRICING, TH)
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].rule, "R4")
        self.assertEqual(f[0].evidence["file_path"], "src/same.py")
        self.assertEqual(f[0].evidence["reads"], 3)
        self.assertEqual(f[0].evidence["extra_est_tokens"], 200)
        self.assertAlmostEqual(f[0].waste_usd, round(200 * 5e-6, 4))

    def test_read_file_tool_counts_as_read(self):
        r = Rollout()
        lines = [meta_line(SID, ts(0)), turn_context_line(ts(0))]
        for i in range(3):
            lines.append(function_call_line(ts(i), "c%d" % i, "read_file", {"file_path": "/x.py"}))
            lines.append(r.token_count_line(ts(i), {"input": 10}))
        f = detect_repeated_reads(self._session(lines), PRICING, TH)
        self.assertEqual([x.evidence["file_path"] for x in f], ["/x.py"])

    def test_two_reads_not_flagged(self):
        r = Rollout()
        s = self._session([
            meta_line(SID, ts(0)), turn_context_line(ts(0)),
            shell_line(ts(0), "c1", "cat /x"), r.token_count_line(ts(0), {"input": 1}),
            shell_line(ts(1), "c2", "cat /x"), r.token_count_line(ts(1), {"input": 2}),
        ])
        self.assertEqual(detect_repeated_reads(s, PRICING, TH), [])

    def test_detect_includes_r3_and_r4(self):
        r = Rollout()
        lines = [meta_line(SID, ts(0)), turn_context_line(ts(0)),
                 shell_line(ts(0), "c0", "cat /f"), r.token_count_line(ts(0), {"input": 1}),
                 output_line(ts(0), "c0", "q" * 40000)]
        for i in range(1, 3):
            lines.append(shell_line(ts(i), "c%d" % i, "cat /f"))
            lines.append(r.token_count_line(ts(i), {"input": 1 + i}))
            lines.append(output_line(ts(i), "c%d" % i, "q" * 40))
        rules = sorted(f.rule for f in detect(self._session(lines), PRICING))
        self.assertEqual(rules, ["R3", "R4"])


if __name__ == "__main__":
    unittest.main()
