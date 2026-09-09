import json
import os
import tempfile
import unittest
from datetime import datetime, timezone

from tokenaudit.loader import (IMAGE_RESULT_CHARS, UnsupportedRollout, load_sessions, open_rollout,
                               parse_file)
from tests.fixtures.builder import (Rollout, compacted_line, custom_tool_call_line,
                                    function_call_line, meta_line, output_line, rollout_path,
                                    sessions_dir, shell_line, token_count_line, turn_context_line,
                                    usage_record_line, user_message_line, write_rollout)

SID = "019a0000-0000-7000-8000-000000000001"
T = ["2026-09-01T10:%02d:00Z" % i for i in range(10)]


class ParseFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _parse(self, lines, sid=SID):
        return parse_file(write_rollout(self.root, sid, lines), sid)

    def test_meta_turn_context_and_token_counts(self):
        r = Rollout()
        s = self._parse([
            meta_line(SID, T[0], cwd="/home/me/work/app"),
            turn_context_line(T[0], model="gpt-5.5", effort="high"),
            user_message_line(T[0], "secret prompt body"),
            r.token_count_line(T[1], {"input": 1000, "cached": 0, "output": 50, "reasoning": 20}),
            r.token_count_line(T[2], {"input": 1500, "cached": 1000, "write": 200, "output": 10}),
        ])
        self.assertEqual(s.session_id, SID)
        self.assertEqual(s.project, "/home/me/work/app")
        self.assertEqual(s.source, "cli")
        self.assertFalse(s.is_subagent)
        self.assertEqual(s.display_name(), "app 019a0000")
        self.assertEqual(len(s.turns), 2)
        t0, t1 = s.turns
        self.assertEqual((t0.model, t0.effort), ("gpt-5.5", "high"))
        self.assertEqual((t0.usage.input, t0.usage.output, t0.usage.reasoning), (1000, 50, 20))
        self.assertEqual((t1.usage.cached, t1.usage.cache_write, t1.usage.uncached), (1000, 200, 300))
        self.assertEqual(t1.usage.context, 1500)
        self.assertEqual(t1.ts.isoformat(), "2026-09-01T10:02:00+00:00")
        self.assertNotIn("secret", json.dumps([t.__dict__ for t in s.turns], default=str))

    def test_duplicate_token_count_is_skipped(self):
        dup = token_count_line(T[1], {"input": 100, "output": 5}, total={"input": 100, "output": 5})
        s = self._parse([meta_line(SID, T[0]), turn_context_line(T[0]), dup, dup,
                         token_count_line(T[2], {"input": 200}, total={"input": 300, "output": 5})])
        self.assertEqual([t.usage.input for t in s.turns], [100, 200])

    def test_usage_records_win_over_token_counts(self):
        r = Rollout()
        s = self._parse([
            meta_line(SID, T[0]), turn_context_line(T[0], model="gpt-5.6-terra"),
            usage_record_line(T[1], SID, "resp_1", {"input": 500, "output": 5}),
            r.token_count_line(T[1], {"input": 500, "output": 5}),
            usage_record_line(T[1], SID, "resp_1", {"input": 500, "output": 5}),  # duplicate
            usage_record_line(T[2], "other-thread", "resp_x", {"input": 99999}),   # not ours
            usage_record_line(T[3], SID, "resp_2", {"input": 700, "cached": 500, "output": 5}),
        ])
        self.assertEqual([(t.response_id, t.usage.input) for t in s.turns],
                         [("resp_1", 500), ("resp_2", 700)])
        self.assertEqual(s.turns[0].model, "gpt-5.6-terra")

    def test_tool_calls_attach_to_the_response_that_issued_them(self):
        r = Rollout()
        s = self._parse([
            meta_line(SID, T[0]), turn_context_line(T[0]),
            shell_line(T[0], "c1", "cat src/a.py"),
            r.token_count_line(T[1], {"input": 100}),
            output_line(T[1], "c1", "x" * 100),
            function_call_line(T[2], "c2", "read_file", {"path": "/abs/b.py"}),
            r.token_count_line(T[2], {"input": 200}),
            output_line(T[2], "c2", [{"type": "input_text", "text": "small"},
                                     {"type": "input_image", "image_url": "data:" + "z" * 100000}]),
            r.token_count_line(T[3], {"input": 300}),
        ])
        self.assertEqual(len(s.turns), 3)
        self.assertEqual([u.name for u in s.turns[0].tool_uses], ["shell"])
        self.assertEqual(s.turns[0].tool_uses[0].command_head, "cat")
        self.assertEqual(s.turns[0].tool_uses[0].read_path, "src/a.py")
        self.assertEqual(s.turns[1].tool_uses[0].read_path, "/abs/b.py")
        self.assertEqual([(x.tool_use_id, x.turn_index) for x in s.tool_results], [("c1", 0), ("c2", 1)])
        self.assertEqual(s.tool_results[0].chars, 100)
        expected = IMAGE_RESULT_CHARS + len(json.dumps({"type": "input_text", "text": "small"}))
        self.assertEqual(s.tool_results[1].chars, expected)

    def test_shell_command_parsing_keeps_only_head_and_paths(self):
        r = Rollout()
        s = self._parse([
            meta_line(SID, T[0]), turn_context_line(T[0]),
            shell_line(T[0], "c1", "GITHUB_TOKEN=ghp_secret gh pr list"),
            shell_line(T[0], "c2", "sed -n '10,80p' lib/util.py | head -20"),
            shell_line(T[0], "c3", "cat > out/gen.txt <<'EOF'\n" + "q" * 400 + "\nEOF"),
            shell_line(T[0], "c4", "rg -n foo src"),
            shell_line(T[0], "c5", "FOO=bar"),
            function_call_line(T[0], "c6", "exec_command", {"cmd": "tail -n +20 /var/log/app.log"}),
            r.token_count_line(T[1], {"input": 100}),
        ])
        uses = {u.id: u for u in s.turns[0].tool_uses}
        self.assertEqual(uses["c1"].command_head, "gh")
        self.assertEqual(uses["c2"].read_path, "lib/util.py")
        self.assertEqual(uses["c3"].command_head, "cat")
        self.assertEqual(uses["c3"].write_paths, ["out/gen.txt"])
        self.assertGreater(uses["c3"].content_chars, 400)
        self.assertIsNone(uses["c4"].read_path)
        self.assertIsNone(uses["c5"].command_head)
        self.assertEqual(uses["c6"].read_path, "/var/log/app.log")
        dump = json.dumps([u.__dict__ for u in s.turns[0].tool_uses])
        self.assertNotIn("ghp_secret", dump)
        self.assertNotIn("qqqq", dump)

    def test_apply_patch_paths_and_sizes(self):
        patch = ("*** Begin Patch\n*** Add File: new/file.py\n+print(1)\n"
                 "*** Update File: old/file.py\n@@\n-a\n+b\n*** End Patch")
        r = Rollout()
        s = self._parse([
            meta_line(SID, T[0]), turn_context_line(T[0]),
            custom_tool_call_line(T[0], "c1", "apply_patch", patch),
            function_call_line(T[0], "c2", "apply_patch", {"input": patch}),
            r.token_count_line(T[1], {"input": 100}),
        ])
        for u in s.turns[0].tool_uses:
            self.assertEqual(u.name, "apply_patch")
            self.assertEqual(u.file_path, "new/file.py")
            self.assertEqual(u.write_paths, ["new/file.py"])
            self.assertEqual(u.content_chars, len(patch))

    def test_mode_and_compaction_events(self):
        r = Rollout()
        s = self._parse([
            meta_line(SID, T[0]),
            turn_context_line(T[0], approval="on-request"),
            r.token_count_line(T[1], {"input": 100}),
            turn_context_line(T[2], approval="never"),
            r.token_count_line(T[2], {"input": 100}),
            compacted_line(T[3]),
            r.token_count_line(T[4], {"input": 50}),
        ])
        self.assertEqual(s.mode_events, [1])
        self.assertEqual(s.compaction_events, [2])

    def test_subagent_meta_and_inherited_ordinals(self):
        parent = "019a0000-0000-7000-8000-00000000aaaa"
        r = Rollout()
        s = self._parse([
            meta_line(SID, T[0], parent_id=parent, start_ordinal=5, ordinal=0),
            turn_context_line(T[0], ordinal=1),
            r.token_count_line(T[0], {"input": 90000}, ordinal=2),   # replayed parent usage
            r.token_count_line(T[0], {"input": 90000}, ordinal=3),
            meta_line(parent, T[0], ordinal=4),                      # replayed parent meta
            turn_context_line(T[1], ordinal=5),
            r.token_count_line(T[1], {"input": 10}, ordinal=6),
        ])
        self.assertEqual(s.session_id, SID)
        self.assertEqual(s.parent_id, parent)
        self.assertEqual(s.source, "subagent:thread_spawn")
        self.assertTrue(s.is_subagent)
        self.assertEqual([t.usage.input for t in s.turns], [10])
        self.assertTrue(s.title.endswith("(subagent)"))

    def test_malformed_lines_are_skipped(self):
        r = Rollout()
        s = self._parse([
            "{not json",
            json.dumps([1, 2]),
            json.dumps({"type": "event_msg", "payload": "token_count"}),
            json.dumps({"type": "event_msg", "timestamp": "bad", "payload": {"type": "token_count", "info": {"last_token_usage": {"input_tokens": 1}}}}),
            json.dumps({"timestamp": T[0], "type": "session_meta", "payload": {"id": SID, "cwd": "/p"}}),
            r.token_count_line(T[1], {"input": 5}),
        ])
        self.assertEqual(len(s.turns), 1)
        self.assertEqual(s.project, "/p")

    def test_project_filter_returns_none(self):
        path = write_rollout(self.root, SID, [meta_line(SID, T[0], cwd="/a/b")])
        self.assertIsNone(parse_file(path, SID, project_filter="zzz"))
        self.assertIsNotNone(parse_file(path, SID, project_filter="a/b"))

    def test_zst_without_decoder_raises(self):
        path = os.path.join(self.root, "rollout-2026-09-01T10-00-00-x.jsonl.zst")
        with open(path, "wb") as f:
            f.write(b"\x28\xb5\x2f\xfd")
        try:
            import zstandard  # noqa: F401
            have = True
        except ImportError:
            try:
                from compression import zstd  # noqa: F401
                have = True
            except ImportError:
                have = False
        if have:
            self.skipTest("zstd decoder available")
        with self.assertRaises(UnsupportedRollout):
            open_rollout(path)


class LoadSessionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.now = datetime(2026, 9, 9, tzinfo=timezone.utc)
        self.parent = "019a0000-0000-7000-8000-00000000aaaa"
        self.child = "019a0000-0000-7000-8000-00000000bbbb"
        self.old = "019a0000-0000-7000-8000-00000000cccc"
        self.other = "019a0000-0000-7000-8000-00000000dddd"
        self.arch = "019a0000-0000-7000-8000-00000000eeee"
        write_rollout(self.root, self.parent, [
            meta_line(self.parent, "2026-09-01T00:00:00Z", cwd="/w/proj-a"), turn_context_line("2026-09-01T00:00:00Z"),
            Rollout().token_count_line("2026-09-01T00:00:01Z", {"input": 10})], ts="2026-09-01T00:00:00Z")
        write_rollout(self.root, self.child, [
            meta_line(self.child, "2026-09-01T00:00:30Z", cwd="/w/proj-a", parent_id=self.parent),
            turn_context_line("2026-09-01T00:00:30Z"),
            Rollout().token_count_line("2026-09-01T00:00:31Z", {"input": 20})], ts="2026-09-01T00:00:30Z")
        p_old = write_rollout(self.root, self.old, [
            meta_line(self.old, "2026-07-01T00:00:00Z", cwd="/w/proj-a"), turn_context_line("2026-07-01T00:00:00Z"),
            Rollout().token_count_line("2026-07-01T00:00:01Z", {"input": 10})], ts="2026-07-01T00:00:00Z")
        os.utime(p_old, (1750000000, 1750000000))  # June 2026 mtime: pruned before parsing
        write_rollout(self.root, self.other, [
            meta_line(self.other, "2026-09-02T00:00:00Z", cwd="/w/proj-b"), turn_context_line("2026-09-02T00:00:00Z"),
            Rollout().token_count_line("2026-09-02T00:00:01Z", {"input": 10})], ts="2026-09-02T00:00:00Z")
        write_rollout(self.root, "019a0000-0000-7000-8000-00000000ffff", [
            meta_line("019a0000-0000-7000-8000-00000000ffff", "2026-09-02T00:00:00Z")], ts="2026-09-02T00:00:00Z")
        write_rollout(self.root, self.arch, [
            meta_line(self.arch, "2026-09-03T00:00:00Z", cwd="/w/proj-c"), turn_context_line("2026-09-03T00:00:00Z"),
            Rollout().token_count_line("2026-09-03T00:00:01Z", {"input": 10})], ts="2026-09-03T00:00:00Z", archived=True)
        self.dirs = [sessions_dir(self.root), os.path.join(self.root, "archived_sessions")]

    def tearDown(self):
        self.tmp.cleanup()

    def test_period_filter_and_subagents(self):
        sessions = load_sessions(self.dirs, days=30, now=self.now)
        ids = sorted(s.session_id for s in sessions)
        self.assertEqual(ids, sorted([self.parent, self.other, self.arch]))
        parent = [s for s in sessions if s.session_id == self.parent][0]
        self.assertEqual([a.session_id for a in parent.subagents], [self.child])
        self.assertEqual(parent.subagents[0].title, "proj-a 019a0000 (subagent)")

    def test_all_time_and_project_filter(self):
        self.assertEqual(len(load_sessions(self.dirs, all_time=True, now=self.now)), 4)
        only_b = load_sessions(self.dirs, all_time=True, project_filter="proj-b", now=self.now)
        self.assertEqual([s.session_id for s in only_b], [self.other])

    def test_single_dir_string_and_skipped_list(self):
        skipped = []
        sessions = load_sessions(sessions_dir(self.root), all_time=True, now=self.now, skipped=skipped)
        self.assertEqual(len(sessions), 3)
        self.assertEqual(skipped, [])


if __name__ == "__main__":
    unittest.main()
