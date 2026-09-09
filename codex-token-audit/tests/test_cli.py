import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone

from tokenaudit.cli import parse_thresholds, run
from tests.fixtures.builder import Rollout, meta_line, sessions_dir, turn_context_line, write_rollout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANALYZE = os.path.join(ROOT, "skills", "token-audit", "scripts", "analyze.py")
SID = "019a0000-0000-7000-8000-000000000001"


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        r = Rollout()
        write_rollout(self.tmp.name, SID, [
            meta_line(SID, "2026-09-01T10:00:00Z"),
            turn_context_line("2026-09-01T10:00:00Z", model="gpt-5.5"),
            r.token_count_line("2026-09-01T10:00:00Z", {"input": 30000}),
            r.token_count_line("2026-09-01T13:00:00Z", {"input": 77503, "cached": 0}),
        ])
        self.dir = sessions_dir(self.tmp.name)
        self.now = datetime(2026, 9, 9, tzinfo=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def test_parse_thresholds(self):
        self.assertEqual(parse_thresholds(["r1_min_missed_tokens=5000", "chars_per_token=3.5"]),
                         {"r1_min_missed_tokens": 5000, "chars_per_token": 3.5})
        with self.assertRaises(SystemExit):
            parse_thresholds(["nonsense"])
        with self.assertRaises(SystemExit):
            parse_thresholds(["unknown_key=1"])
        self.assertEqual(parse_thresholds(["r3_min_tokens=1e3"]), {"r3_min_tokens": 1000.0})
        with self.assertRaises(SystemExit):
            parse_thresholds(["r3_min_tokens=abc"])
        with self.assertRaises(SystemExit):
            parse_thresholds(["chars_per_token=0"])

    def test_run_markdown_default(self):
        out = run(["--sessions-dir", self.dir], now=self.now)
        self.assertTrue(out.startswith("# codex token-audit report"))
        self.assertIn("| R1 |", out)
        self.assertIn("last 30 days", out)

    def test_run_json_and_filters(self):
        out = run(["--sessions-dir", self.dir, "--format", "json", "--all", "--top", "1"], now=self.now)
        r = json.loads(out)
        self.assertEqual(r["sessions"], 1)
        self.assertEqual(r["period"]["days"], None)
        self.assertEqual(len(r["findings"]), 1)
        none = run(["--sessions-dir", self.dir, "--format", "json", "--project", "zzz"], now=self.now)
        self.assertEqual(json.loads(none)["sessions"], 0)

    def test_threshold_flag_changes_detection(self):
        out = run(["--sessions-dir", self.dir, "--format", "json",
                   "--threshold", "r1_min_missed_tokens=100000",
                   "--threshold", "r2_min_missed_tokens=100000"], now=self.now)
        self.assertEqual(json.loads(out)["findings"], [])

    def test_default_dirs_from_codex_home(self):
        env = dict(os.environ, CODEX_HOME=self.tmp.name)
        res = subprocess.run([sys.executable, ANALYZE, "--all", "--format", "json"],
                             capture_output=True, text=True, check=True, env=env)
        self.assertEqual(json.loads(res.stdout)["sessions"], 1)

    def test_entrypoint_script_runs(self):
        res = subprocess.run([sys.executable, ANALYZE, "--sessions-dir", self.dir, "--all"],
                             capture_output=True, text=True, check=True)
        self.assertIn("# codex token-audit report", res.stdout)


if __name__ == "__main__":
    unittest.main()
