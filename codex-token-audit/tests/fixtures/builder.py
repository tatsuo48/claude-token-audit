"""Helpers that build synthetic Codex CLI rollout lines."""
import json
import os

DEFAULT_CWD = "/home/me/proj"
_ZERO = {"input_tokens": 0, "cached_input_tokens": 0, "cache_write_input_tokens": 0,
         "output_tokens": 0, "reasoning_output_tokens": 0, "total_tokens": 0}


def _line(ts, typ, payload, ordinal=None):
    d = {"timestamp": ts, "type": typ, "payload": payload}
    if ordinal is not None:
        d["ordinal"] = ordinal
    return json.dumps(d)


def _usage(u):
    """u keys: input, cached, write, output, reasoning (all optional)."""
    u = u or {}
    d = dict(_ZERO)
    d["input_tokens"] = u.get("input", 0)
    d["cached_input_tokens"] = u.get("cached", 0)
    d["cache_write_input_tokens"] = u.get("write", 0)
    d["output_tokens"] = u.get("output", 0)
    d["reasoning_output_tokens"] = u.get("reasoning", 0)
    d["total_tokens"] = d["input_tokens"] + d["output_tokens"]
    return d


def meta_line(session_id, ts, cwd=DEFAULT_CWD, source="cli", parent_id=None,
              start_ordinal=None, ordinal=None):
    p = {"id": session_id, "session_id": session_id, "timestamp": ts, "cwd": cwd,
         "originator": "codex_cli_rs", "cli_version": "0.140.0", "source": source}
    if parent_id:
        p["parent_thread_id"] = parent_id
        p["source"] = {"subagent": {"thread_spawn": {"parent_thread_id": parent_id, "depth": 1}}}
    if start_ordinal is not None:
        p["subagent_history_start_ordinal"] = start_ordinal
    return _line(ts, "session_meta", p, ordinal)


def turn_context_line(ts, model="gpt-5.5", effort=None, approval="on-request",
                      sandbox=None, ordinal=None):
    p = {"cwd": DEFAULT_CWD, "approval_policy": approval,
         "sandbox_policy": sandbox or {"type": "workspace-write"},
         "model": model, "summary": "auto"}
    if effort:
        p["effort"] = effort
    return _line(ts, "turn_context", p, ordinal)


class Rollout:
    """Accumulates cumulative totals so consecutive token_count lines differ."""

    def __init__(self):
        self.total = dict(_ZERO)

    def token_count_line(self, ts, usage=None, ordinal=None):
        last = _usage(usage)
        for k in self.total:
            self.total[k] += last[k]
        p = {"type": "token_count",
             "info": {"total_token_usage": dict(self.total), "last_token_usage": last,
                      "model_context_window": 272000},
             "rate_limits": None}
        return _line(ts, "event_msg", p, ordinal)


def token_count_line(ts, usage=None, total=None, ordinal=None):
    """Stand-alone token_count with an explicit cumulative total (defaults to the usage)."""
    last = _usage(usage)
    p = {"type": "token_count",
         "info": {"total_token_usage": _usage(total) if total else last, "last_token_usage": last,
                  "model_context_window": 272000},
         "rate_limits": None}
    return _line(ts, "event_msg", p, ordinal)


def usage_record_line(ts, thread_id, response_id, usage=None, ordinal=None):
    u = _usage(usage)
    p = {"thread_id": thread_id, "turn_id": "turn-1", "session_id": thread_id,
         "root_turn_id": "turn-1", "response_id": response_id,
         "usage": u, "turn_token_usage": u, "thread_token_usage": u}
    return _line(ts, "token_usage_record", p, ordinal)


def function_call_line(ts, call_id, name, arguments, ordinal=None):
    p = {"type": "function_call", "name": name, "call_id": call_id,
         "arguments": json.dumps(arguments) if not isinstance(arguments, str) else arguments}
    return _line(ts, "response_item", p, ordinal)


def shell_line(ts, call_id, command, ordinal=None):
    return function_call_line(ts, call_id, "shell", {"command": ["bash", "-lc", command]}, ordinal)


def custom_tool_call_line(ts, call_id, name, input_text, ordinal=None):
    p = {"type": "custom_tool_call", "name": name, "call_id": call_id, "input": input_text,
         "status": "completed"}
    return _line(ts, "response_item", p, ordinal)


def output_line(ts, call_id, output, custom=False, ordinal=None):
    p = {"type": "custom_tool_call_output" if custom else "function_call_output",
         "call_id": call_id, "output": output}
    return _line(ts, "response_item", p, ordinal)


def compacted_line(ts, ordinal=None):
    return _line(ts, "compacted", {"message": "summary omitted"}, ordinal)


def user_message_line(ts, text):
    return _line(ts, "event_msg", {"type": "user_message", "message": text})


def rollout_path(root, session_id, ts="2026-09-01T10:00:00Z", archived=False):
    y, m, d = ts[:4], ts[5:7], ts[8:10]
    stamp = ts[:19].replace(":", "-")
    sub = "archived_sessions" if archived else "sessions"
    return os.path.join(root, sub, y, m, d, "rollout-%s-%s.jsonl" % (stamp, session_id))


def write_rollout(root, session_id, lines, ts="2026-09-01T10:00:00Z", archived=False):
    path = rollout_path(root, session_id, ts, archived)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def sessions_dir(root):
    return os.path.join(root, "sessions")
