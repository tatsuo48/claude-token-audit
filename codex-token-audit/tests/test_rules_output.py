import os
import tempfile
import unittest

from tokenaudit.loader import parse_file
from tokenaudit.pricing import Pricing
from tokenaudit.rules import DEFAULT_THRESHOLDS, detect, detect_large_writes, detect_long_session
from tests.fixtures.builder import (Rollout, custom_tool_call_line, meta_line, shell_line,
                                    turn_context_line, write_rollout)

PRICING = Pricing.load(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "skills", "token-audit", "scripts", "pricing.json"))
SID = "019a0000-0000-7000-8000-000000000001"
TH = dict(DEFAULT_THRESHOLDS)


def ts(i):
    return "2026-09-01T%02d:%02d:00Z" % (10 + i // 60, i % 60)


def patch(kind, path, size):
    return "*** Begin Patch\n*** %s File: %s\n+%s\n*** End Patch" % (kind, path, "w" * size)


class LargeWriteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _session(self, lines):
        return parse_file(write_rollout(self.tmp.name, SID, lines), SID)

    def test_r5_large_patch_priced_at_output_rate(self):
        p = patch("Add", "gen.md", 20000)
        s = self._session([
            meta_line(SID, ts(0)), turn_context_line(ts(0), model="gpt-5.6-sol"),
            custom_tool_call_line(ts(0), "c1", "apply_patch", p),
            Rollout().token_count_line(ts(0), {"input": 10, "output": 6000}),
        ])
        f = detect_large_writes(s, PRICING, TH)
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].evidence["kind"], "large_write")
        self.assertEqual(f[0].evidence["file_path"], "gen.md")
        self.assertEqual(f[0].evidence["est_tokens"], len(p) // 4)
        self.assertAlmostEqual(f[0].waste_usd, round((len(p) // 4) * 20e-6, 4))

    def test_r5_repeated_full_write_same_path(self):
        r = Rollout()
        s = self._session([
            meta_line(SID, ts(0)), turn_context_line(ts(0), model="gpt-5.5"),
            custom_tool_call_line(ts(0), "c1", "apply_patch", patch("Add", "a.py", 4000)),
            r.token_count_line(ts(0), {"input": 10}),
            shell_line(ts(1), "c2", "cat > a.py <<'EOF'\n" + "b" * 8000 + "\nEOF"),
            r.token_count_line(ts(1), {"input": 20}),
        ])
        f = detect_large_writes(s, PRICING, TH)
        kinds = sorted(x.evidence["kind"] for x in f)
        self.assertEqual(kinds, ["repeated_write"])
        rep = f[0]
        self.assertEqual(rep.evidence["file_path"], "a.py")
        self.assertEqual(rep.evidence["writes"], 2)
        expected_extra = len("cat > a.py <<'EOF'\n" + "b" * 8000 + "\nEOF") // 4
        self.assertEqual(rep.evidence["extra_est_tokens"], expected_extra)
        self.assertAlmostEqual(rep.waste_usd, round(expected_extra * 30e-6, 4))

    def test_update_patches_are_not_repeated_writes(self):
        r = Rollout()
        lines = [meta_line(SID, ts(0)), turn_context_line(ts(0))]
        for i in range(3):
            lines.append(custom_tool_call_line(ts(i), "c%d" % i, "apply_patch", patch("Update", "a.py", 100)))
            lines.append(r.token_count_line(ts(i), {"input": 10}))
        self.assertEqual(detect_large_writes(self._session(lines), PRICING, TH), [])

    def test_small_single_write_not_flagged(self):
        s = self._session([
            meta_line(SID, ts(0)), turn_context_line(ts(0)),
            custom_tool_call_line(ts(0), "c1", "apply_patch", patch("Add", "s", 100)),
            Rollout().token_count_line(ts(0), {"input": 10}),
        ])
        self.assertEqual(detect_large_writes(s, PRICING, TH), [])


class LongSessionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _session(self, lines):
        return parse_file(write_rollout(self.tmp.name, SID, lines), SID)

    def test_r6_by_context_size(self):
        r = Rollout()
        s = self._session([
            meta_line(SID, ts(0)), turn_context_line(ts(0), model="gpt-5.5"),
            r.token_count_line(ts(0), {"input": 100000, "cached": 90000}),
            r.token_count_line(ts(1), {"input": 160000, "cached": 100000}),
            r.token_count_line(ts(2), {"input": 200000, "cached": 160000}),
        ])
        f = detect_long_session(s, PRICING, TH)
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].evidence["turns"], 3)
        self.assertEqual(f[0].evidence["final_context_tokens"], 200000)
        self.assertAlmostEqual(f[0].waste_usd, round((10000 + 50000) * 0.1 * 5e-6, 4))

    def test_r6_by_turn_count(self):
        r = Rollout()
        lines = [meta_line(SID, ts(0)), turn_context_line(ts(0))]
        lines += [r.token_count_line(ts(i), {"input": 1000, "cached": 900}) for i in range(150)]
        f = detect_long_session(self._session(lines), PRICING, TH)
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].evidence["turns"], 150)
        self.assertEqual(f[0].waste_usd, 0.0)

    def test_short_small_session_not_flagged(self):
        s = self._session([meta_line(SID, ts(0)), turn_context_line(ts(0)),
                           Rollout().token_count_line(ts(0), {"input": 1000})])
        self.assertEqual(detect_long_session(s, PRICING, TH), [])

    def test_detect_runs_all_rules(self):
        r = Rollout()
        lines = [meta_line(SID, ts(0)), turn_context_line(ts(0))]
        for i in range(2):
            lines.append(custom_tool_call_line(ts(i), "c%d" % i, "apply_patch", patch("Add", "o", 20000)))
            lines.append(r.token_count_line(ts(i), {"input": 160000, "cached": 150000}))
        rules = sorted(set(f.rule for f in detect(self._session(lines), PRICING)))
        self.assertEqual(rules, ["R5", "R6"])


if __name__ == "__main__":
    unittest.main()
