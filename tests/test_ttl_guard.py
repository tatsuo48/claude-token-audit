import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone

from tests.fixtures.builder import assistant_line, synthetic_line

SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "plugins", "ttl-guard", "scripts", "ttl_guard.py")


def ago(minutes):
    t = datetime.fromtimestamp(time.time() - minutes * 60, tz=timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def sidechain(line):
    e = json.loads(line)
    e["isSidechain"] = True
    return json.dumps(e)


class TtlGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.transcript = os.path.join(self.tmp.name, "s.jsonl")
        self.env = dict(os.environ, CLAUDE_CONFIG_DIR=os.path.join(self.tmp.name, "cfg"))

    def tearDown(self):
        self.tmp.cleanup()

    def _transcript(self, lines):
        with open(self.transcript, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def _run(self, prompt="hello"):
        payload = json.dumps({"session_id": "s", "transcript_path": self.transcript,
                              "prompt": prompt})
        p = subprocess.run([sys.executable, SCRIPT], input=payload, env=self.env,
                           capture_output=True, text=True)
        return p.returncode, p.stderr

    def test_within_ttl_passes(self):
        self._transcript([assistant_line("m1", ago(30), usage={"c1": 1000})])
        self.assertEqual(self._run()[0], 0)

    def test_expired_1h_blocks_once_then_resend_passes(self):
        self._transcript([assistant_line("m1", ago(61), usage={"c1": 1000})])
        code, err = self._run()
        self.assertEqual(code, 2)
        self.assertIn("60 minutes", err)
        self.assertIn("/clear", err)
        self.assertIn("/compact", err)
        self.assertNotIn("promptCacheTtl", err)
        self.assertEqual(self._run()[0], 0)

    def test_5m_ttl_suggests_1h(self):
        self._transcript([assistant_line("m1", ago(6), usage={"c5": 1000})])
        code, err = self._run()
        self.assertEqual(code, 2)
        self.assertIn("promptCacheTtl", err)

    def test_ttl_comes_from_latest_write_not_latest_line(self):
        # latest response is a pure cache hit; TTL comes from the earlier 1h write
        self._transcript([
            assistant_line("m1", ago(59), usage={"c1": 1000}),
            assistant_line("m2", ago(10), usage={"read": 1000}),
        ])
        self.assertEqual(self._run()[0], 0)

    def test_slash_commands_are_never_blocked(self):
        self._transcript([assistant_line("m1", ago(61), usage={"c1": 1000})])
        self.assertEqual(self._run("/compact keep the plan")[0], 0)

    def test_sidechain_lines_are_ignored(self):
        self._transcript([
            assistant_line("m1", ago(61), usage={"c1": 1000}),
            sidechain(assistant_line("a1", ago(1), usage={"c5": 1000})),
        ])
        code, err = self._run()
        self.assertEqual(code, 2)
        self.assertIn("60 minutes", err)

    def test_synthetic_lines_are_ignored(self):
        self._transcript([
            assistant_line("m1", ago(61), usage={"c1": 1000}),
            synthetic_line(ago(1)),
        ])
        self.assertEqual(self._run()[0], 2)

    def test_no_cache_write_does_nothing(self):
        self._transcript([assistant_line("m1", ago(120), usage={"read": 0})])
        self.assertEqual(self._run()[0], 0)

    def test_missing_transcript_does_nothing(self):
        self.assertEqual(self._run()[0], 0)

    def test_near_expiry_still_passes(self):
        # cache is still alive at 58/60 minutes: sending refreshes it, so do not block
        self._transcript([assistant_line("m1", ago(58), usage={"c1": 1000})])
        self.assertEqual(self._run()[0], 0)

    def test_ack_does_not_expire_with_time(self):
        self._transcript([assistant_line("m1", ago(61), usage={"c1": 1000})])
        self.assertEqual(self._run()[0], 2)
        ack = os.path.join(self.env["CLAUDE_CONFIG_DIR"], "ttl-guard", "s.ack")
        old = time.time() - 60 * 60
        os.utime(ack, (old, old))
        self.assertEqual(self._run()[0], 0)

    def test_new_idle_gap_asks_again(self):
        self._transcript([assistant_line("m1", ago(130), usage={"c1": 1000})])
        self.assertEqual(self._run()[0], 2)
        self.assertEqual(self._run()[0], 0)
        self._transcript([
            assistant_line("m1", ago(130), usage={"c1": 1000}),
            assistant_line("m2", ago(65), usage={"c1": 1000}),
        ])
        self.assertEqual(self._run()[0], 2)

    def test_expiry_counts_from_start_of_last_response(self):
        # one response written over several lines; the cache was refreshed at its first line
        self._transcript([
            assistant_line("m1", ago(60.5), usage={"c1": 1000}),
            assistant_line("m1", ago(59.5), usage={"c1": 1000}),
        ])
        self.assertEqual(self._run()[0], 2)

    def test_old_state_files_are_cleaned_up(self):
        state = os.path.join(self.env["CLAUDE_CONFIG_DIR"], "ttl-guard")
        os.makedirs(state)
        old_file = os.path.join(state, "old.ack")
        open(old_file, "w").close()
        old = time.time() - 91 * 86400
        os.utime(old_file, (old, old))
        self._transcript([assistant_line("m1", ago(1), usage={"c1": 1000})])
        self._run()
        self.assertFalse(os.path.exists(old_file))


if __name__ == "__main__":
    unittest.main()
