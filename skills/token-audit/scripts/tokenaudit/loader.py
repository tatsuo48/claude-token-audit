import glob
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from .model import Session, ToolResult, ToolUse, Turn, Usage

# Flat per-block char estimate for image tool-result content. Real image blocks carry
# base64 payloads that would otherwise be priced at chars/4, wildly overestimating tokens.
IMAGE_RESULT_CHARS = 6400

_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def parse_ts(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _result_chars(content) -> int:
    if isinstance(content, list):
        total = 0
        for block in content:
            if isinstance(block, dict) and block.get("type") == "image":
                total += IMAGE_RESULT_CHARS
            else:
                total += len(json.dumps(block, ensure_ascii=False))
        return total
    return len(json.dumps(content, ensure_ascii=False))


def default_projects_dir() -> str:
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    return os.path.join(base, "projects")


def _usage(u: dict) -> Usage:
    breakdown = u.get("cache_creation") or {}
    total = u.get("cache_creation_input_tokens") or 0
    c5 = breakdown.get("ephemeral_5m_input_tokens")
    c1 = breakdown.get("ephemeral_1h_input_tokens")
    if c5 is None and c1 is None:
        c5, c1 = total, 0  # no breakdown: assume the cheaper 5m TTL
    return Usage(
        input=u.get("input_tokens") or 0,
        cache_5m=c5 or 0,
        cache_1h=c1 or 0,
        cache_read=u.get("cache_read_input_tokens") or 0,
        output=u.get("output_tokens") or 0,
    )


def _tool_use(b: dict) -> ToolUse:
    inp = b.get("input")
    if not isinstance(inp, dict):
        inp = {}
    content = inp.get("content")
    cmd = inp.get("command")
    head = None
    if isinstance(cmd, str) and cmd.strip():
        tokens = cmd.strip().split()
        idx = 0
        while idx < len(tokens) and (_ENV_ASSIGN_RE.match(tokens[idx]) or tokens[idx] == "export"):
            idx += 1
        head = tokens[idx] if idx < len(tokens) else None
    fp = inp.get("file_path")
    return ToolUse(
        id=b.get("id", ""),
        name=b.get("name", "?"),
        file_path=fp if isinstance(fp, str) else None,
        content_chars=len(content) if isinstance(content, str) else 0,
        command_head=head,
    )


def parse_file(path: str, session_id: str, project: str) -> Session:
    s = Session(session_id=session_id, project=project, path=path)
    by_id = {}
    order: List[str] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(d, dict):
                continue
            try:
                t = d.get("type")
                if t == "assistant":
                    m = d.get("message")
                    if not isinstance(m, dict):
                        continue
                    usage = m.get("usage")
                    if not isinstance(usage, dict):
                        continue
                    if m.get("model") == "<synthetic>" or not usage or not d.get("timestamp"):
                        continue
                    mid = m.get("id") or d.get("uuid")
                    uses = [_tool_use(b) for b in (m.get("content") or [])
                            if isinstance(b, dict) and b.get("type") == "tool_use"]
                    existing = by_id.get(mid)
                    if existing is None:
                        by_id[mid] = Turn(msg_id=mid, ts=parse_ts(d["timestamp"]),
                                          model=m.get("model") or "unknown",
                                          usage=_usage(usage), tool_uses=uses)
                        order.append(mid)
                    else:
                        new_usage = _usage(usage)
                        new_ts = parse_ts(d["timestamp"])
                        existing.usage = new_usage
                        existing.ts = new_ts
                        known = {u.id for u in existing.tool_uses}
                        existing.tool_uses.extend(u for u in uses if u.id not in known)
                elif t == "user":
                    m = d.get("message")
                    if not isinstance(m, dict):
                        continue
                    content = m.get("content")
                    if isinstance(content, list):
                        ts = parse_ts(d["timestamp"]) if d.get("timestamp") else None
                        for b in content:
                            if isinstance(b, dict) and b.get("type") == "tool_result":
                                s.tool_results.append(ToolResult(
                                    tool_use_id=b.get("tool_use_id", ""), ts=ts,
                                    chars=_result_chars(b.get("content")),
                                    turn_index=len(order) - 1))
                elif t == "custom-title":
                    s.title = d.get("customTitle") or s.title
                elif t in ("mode", "permission-mode"):
                    s.mode_events.append(len(order))
            except (ValueError, AttributeError, TypeError, KeyError):
                continue
    s.turns = [by_id[i] for i in order]
    return s


def load_sessions(projects_dir: str, days: int = 30, project_filter: Optional[str] = None,
                  all_time: bool = False, now: Optional[datetime] = None) -> List[Session]:
    now = now or datetime.now(timezone.utc)
    cutoff = None if all_time else now - timedelta(days=days)
    sessions: List[Session] = []
    for proj_dir in sorted(glob.glob(os.path.join(projects_dir, "*"))):
        if not os.path.isdir(proj_dir):
            continue
        project = os.path.basename(proj_dir)
        if project_filter and project_filter not in project:
            continue
        for fp in sorted(glob.glob(os.path.join(proj_dir, "*.jsonl"))):
            sid = os.path.splitext(os.path.basename(fp))[0]
            s = parse_file(fp, sid, project)
            if not s.turns:
                continue
            if cutoff and s.end < cutoff:
                continue
            for ap in sorted(glob.glob(os.path.join(proj_dir, sid, "subagents", "*.jsonl"))):
                aid = os.path.splitext(os.path.basename(ap))[0]
                sub = parse_file(ap, aid, project)
                if sub.turns:
                    sub.title = "%s (subagent)" % s.display_name()
                    s.subagents.append(sub)
            sessions.append(s)
    return sessions
