import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone

from tokenaudit.cli import parse_thresholds, run
from tests.fixtures.builder import assistant_line, write_session

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANALYZE = os.path.join(ROOT, "skills", "token-audit", "scripts", "analyze.py")


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        write_session(self.tmp.name, "proj", "s1", [
            assistant_line("m1", "2026-09-01T10:00:00Z", usage={"c1": 30000}),
            assistant_line("m2", "2026-09-01T13:00:00Z", usage={"c1": 77503}),
        ])
        self.now = datetime(2026, 9, 9, tzinfo=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def test_parse_thresholds(self):
        self.assertEqual(parse_thresholds(["r1_min_cache_write=5000", "chars_per_token=3.5"]),
                         {"r1_min_cache_write": 5000, "chars_per_token": 3.5})
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
        out = run(["--projects-dir", self.tmp.name], now=self.now)
        self.assertTrue(out.startswith("# token-audit report"))
        self.assertIn("| R1 |", out)
        self.assertIn("last 30 days", out)

    def test_run_json_and_filters(self):
        out = run(["--projects-dir", self.tmp.name, "--format", "json", "--all", "--top", "1"], now=self.now)
        r = json.loads(out)
        self.assertEqual(r["sessions"], 1)
        self.assertEqual(r["period"]["days"], None)
        self.assertEqual(len(r["findings"]), 1)
        none = run(["--projects-dir", self.tmp.name, "--format", "json", "--project", "zzz"], now=self.now)
        self.assertEqual(json.loads(none)["sessions"], 0)

    def test_threshold_flag_changes_detection(self):
        out = run(["--projects-dir", self.tmp.name, "--format", "json",
                   "--threshold", "r1_min_cache_write=100000",
                   "--threshold", "r2_min_cache_write=100000"], now=self.now)
        self.assertEqual(json.loads(out)["findings"], [])

    def test_entrypoint_script_runs(self):
        res = subprocess.run([sys.executable, ANALYZE, "--projects-dir", self.tmp.name, "--all"],
                             capture_output=True, text=True, check=True)
        self.assertIn("# token-audit report", res.stdout)


if __name__ == "__main__":
    unittest.main()
