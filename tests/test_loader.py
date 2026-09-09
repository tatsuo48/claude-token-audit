import json
import os
import tempfile
import unittest
from datetime import datetime, timezone

from tokenaudit.loader import IMAGE_RESULT_CHARS, load_sessions, parse_file
from tests.fixtures.builder import (assistant_line, mode_line, synthetic_line,
                                    title_line, tool_result_line, tool_use,
                                    write_session)

T0 = "2026-09-01T10:00:00Z"
T1 = "2026-09-01T10:01:00Z"
T2 = "2026-09-01T10:02:00Z"


class ParseFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_dedup_by_message_id_keeps_last_usage_and_merges_tool_uses(self):
        lines = [
            assistant_line("m1", T0, usage={"output": 10},
                           content=[{"type": "thinking", "thinking": ""}]),
            assistant_line("m1", T0, usage={"output": 10},
                           content=[tool_use("t1", "Read", file_path="/a.py")]),
            assistant_line("m1", T0, usage={"output": 12},
                           content=[tool_use("t2", "Bash", command="ls -la /tmp")]),
        ]
        path = write_session(self.root, "proj", "s1", lines)
        s = parse_file(path, "s1", "proj")
        self.assertEqual(len(s.turns), 1)
        self.assertEqual(s.turns[0].usage.output, 12)
        names = [u.name for u in s.turns[0].tool_uses]
        self.assertEqual(names, ["Read", "Bash"])
        self.assertEqual(s.turns[0].tool_uses[0].file_path, "/a.py")
        self.assertEqual(s.turns[0].tool_uses[1].command_head, "ls")

    def test_synthetic_and_title_and_mode_events(self):
        lines = [
            title_line("My session"),
            synthetic_line(T0),
            assistant_line("m1", T0),
            mode_line(),
            assistant_line("m2", T1),
        ]
        s = parse_file(write_session(self.root, "proj", "s1", lines), "s1", "proj")
        self.assertEqual(len(s.turns), 2)
        self.assertEqual(s.title, "My session")
        self.assertEqual(s.display_name(), "My session")
        self.assertEqual(s.mode_events, [1])

    def test_tool_result_size_and_turn_index(self):
        lines = [
            assistant_line("m1", T0, content=[tool_use("t1", "Read", file_path="/a.py")]),
            tool_result_line("t1", T1, "x" * 100),
            assistant_line("m2", T2),
        ]
        s = parse_file(write_session(self.root, "proj", "s1", lines), "s1", "proj")
        self.assertEqual(len(s.tool_results), 1)
        r = s.tool_results[0]
        self.assertEqual(r.tool_use_id, "t1")
        self.assertEqual(r.chars, 102)  # json.dumps adds quotes
        self.assertEqual(r.turn_index, 0)

    def test_write_content_chars_and_cache_breakdown(self):
        lines = [assistant_line(
            "m1", T0, usage={"c5": 100, "c1": 200, "read": 300, "input": 4},
            content=[tool_use("t1", "Write", file_path="/b.md", content="y" * 50)])]
        s = parse_file(write_session(self.root, "proj", "s1", lines), "s1", "proj")
        t = s.turns[0]
        self.assertEqual(t.tool_uses[0].content_chars, 50)
        self.assertEqual((t.usage.cache_5m, t.usage.cache_1h, t.usage.cache_read), (100, 200, 300))
        self.assertEqual(t.usage.context, 604)

    def test_malformed_lines_are_skipped(self):
        path = write_session(self.root, "proj", "s1", ["{not json", assistant_line("m1", T0)])
        s = parse_file(path, "s1", "proj")
        self.assertEqual(len(s.turns), 1)

    def test_tool_result_image_block_uses_flat_constant(self):
        text_block = {"type": "text", "text": "small"}
        image_block = {"type": "image", "source": {"type": "base64", "data": "x" * 100000}}
        lines = [
            assistant_line("m1", T0, content=[tool_use("t1", "Read", file_path="/a.png")]),
            tool_result_line("t1", T1, [image_block, text_block]),
            assistant_line("m2", T2),
        ]
        s = parse_file(write_session(self.root, "proj", "s1", lines), "s1", "proj")
        r = s.tool_results[0]
        expected = IMAGE_RESULT_CHARS + len(json.dumps(text_block, ensure_ascii=False))
        self.assertEqual(r.chars, expected)

    def test_malformed_records_are_skipped_and_naive_timestamp_gets_utc(self):
        lines = [
            json.dumps([1, 2, 3]),  # top-level JSON is a list, not a dict
            json.dumps({"type": "user", "timestamp": T0,
                       "message": {"role": "user", "content": "just a string"}}),
            json.dumps({"type": "assistant", "timestamp": T0, "message": "oops"}),
            assistant_line("bad_ts", "not-a-date", usage={"output": 1}),
            assistant_line("naive", "2026-09-01T10:00:00", usage={"output": 5}),
            assistant_line("final", T2, usage={"output": 7}),
        ]
        s = parse_file(write_session(self.root, "proj", "s1", lines), "s1", "proj")
        ids = [t.msg_id for t in s.turns]
        self.assertEqual(ids, ["naive", "final"])
        naive_turn = s.turns[0]
        self.assertIsNotNone(naive_turn.ts.tzinfo)
        self.assertEqual(naive_turn.ts.utcoffset().total_seconds(), 0)

    def test_bash_command_head_skips_env_assignments(self):
        lines = [
            assistant_line("m1", T0, content=[
                tool_use("t1", "Bash", command="GITHUB_TOKEN=ghp_secret gh pr list"),
                tool_use("t2", "Bash", command="FOO=bar"),
                tool_use("t3", "Bash", command="export FOO=bar gh pr list"),
            ]),
        ]
        s = parse_file(write_session(self.root, "proj", "s1", lines), "s1", "proj")
        uses = s.turns[0].tool_uses
        self.assertEqual(uses[0].command_head, "gh")
        self.assertIsNone(uses[1].command_head)
        self.assertEqual(uses[2].command_head, "gh")
        dump = json.dumps([u.__dict__ for u in uses])
        self.assertNotIn("ghp_secret", dump)

    def test_corrupt_duplicate_line_does_not_leak_usage(self):
        lines = [
            assistant_line("m1", T0, usage={"input": 10, "output": 1}),
            assistant_line("m1", "not-a-date", usage={"input": 999, "output": 999}),
        ]
        s = parse_file(write_session(self.root, "proj", "s1", lines), "s1", "proj")
        self.assertEqual(len(s.turns), 1)
        self.assertEqual((s.turns[0].usage.input, s.turns[0].usage.output), (10, 1))
        self.assertEqual(s.turns[0].ts.isoformat(), "2026-09-01T10:00:00+00:00")


class LoadSessionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        write_session(self.root, "proj-a", "old", [assistant_line("m1", "2026-07-01T00:00:00Z")])
        write_session(self.root, "proj-a", "new", [assistant_line("m1", "2026-09-01T00:00:00Z")],
                      subagents={"agent-1": [assistant_line("a1", "2026-09-01T00:00:30Z")]})
        write_session(self.root, "proj-b", "other", [assistant_line("m1", "2026-09-02T00:00:00Z")])
        write_session(self.root, "proj-b", "empty", [title_line("nothing")])
        self.now = datetime(2026, 9, 9, tzinfo=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def test_period_filter_and_subagents(self):
        sessions = load_sessions(self.root, days=30, now=self.now)
        ids = sorted(s.session_id for s in sessions)
        self.assertEqual(ids, ["new", "other"])
        new = [s for s in sessions if s.session_id == "new"][0]
        self.assertEqual(len(new.subagents), 1)
        self.assertEqual(new.subagents[0].session_id, "agent-1")
        self.assertEqual(new.subagents[0].title, "new (subagent)")

    def test_all_time_and_project_filter(self):
        self.assertEqual(len(load_sessions(self.root, all_time=True, now=self.now)), 3)
        only_b = load_sessions(self.root, all_time=True, project_filter="proj-b", now=self.now)
        self.assertEqual([s.session_id for s in only_b], ["other"])


if __name__ == "__main__":
    unittest.main()
