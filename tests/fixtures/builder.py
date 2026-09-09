"""Helpers that build synthetic Claude Code transcript lines."""
import json
import os


def assistant_line(msg_id, ts, model="claude-opus-5", usage=None, content=None):
    """usage keys: input, c5, c1, read, output (all optional)."""
    u = usage or {}
    return json.dumps({
        "type": "assistant",
        "timestamp": ts,
        "uuid": "uuid-" + msg_id,
        "message": {
            "id": msg_id,
            "model": model,
            "role": "assistant",
            "content": content or [{"type": "text", "text": "ok"}],
            "usage": {
                "input_tokens": u.get("input", 0),
                "output_tokens": u.get("output", 0),
                "cache_creation_input_tokens": u.get("c5", 0) + u.get("c1", 0),
                "cache_read_input_tokens": u.get("read", 0),
                "cache_creation": {
                    "ephemeral_5m_input_tokens": u.get("c5", 0),
                    "ephemeral_1h_input_tokens": u.get("c1", 0),
                },
            },
        },
    })


def synthetic_line(ts):
    return json.dumps({
        "type": "assistant", "timestamp": ts, "uuid": "syn",
        "message": {"id": "syn", "model": "<synthetic>", "role": "assistant",
                    "content": [], "usage": {"input_tokens": 0, "output_tokens": 0,
                                             "cache_creation_input_tokens": 0,
                                             "cache_read_input_tokens": 0}},
    })


def tool_use(tool_id, name, **inp):
    return {"type": "tool_use", "id": tool_id, "name": name, "input": inp}


def tool_result_line(tool_use_id, ts, content):
    return json.dumps({
        "type": "user", "timestamp": ts, "uuid": "u-" + tool_use_id,
        "message": {"role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": tool_use_id,
                                 "content": content}]},
    })


def title_line(title):
    return json.dumps({"type": "custom-title", "customTitle": title})


def mode_line():
    return json.dumps({"type": "mode", "mode": "normal"})


def write_session(root, project, session_id, lines, subagents=None):
    """Write lines to <root>/<project>/<session_id>.jsonl.

    subagents: dict agent_id -> list of lines, written under
    <root>/<project>/<session_id>/subagents/<agent_id>.jsonl
    """
    pdir = os.path.join(root, project)
    os.makedirs(pdir, exist_ok=True)
    path = os.path.join(pdir, session_id + ".jsonl")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    for agent_id, alines in (subagents or {}).items():
        adir = os.path.join(pdir, session_id, "subagents")
        os.makedirs(adir, exist_ok=True)
        with open(os.path.join(adir, agent_id + ".jsonl"), "w", encoding="utf-8") as f:
            f.write("\n".join(alines) + "\n")
    return path
