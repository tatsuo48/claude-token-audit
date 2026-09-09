"""Parse Codex CLI rollout files (`$CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl`).

Each line is `{"timestamp": ..., "type": ..., "payload": {...}}`. Only the record types
needed for accounting are decoded; message bodies are never kept.
"""
import glob
import gzip
import io
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from .model import Session, ToolResult, ToolUse, Turn, Usage

# Flat per-block char estimate for image tool-output content. Real image blocks carry
# base64 payloads that would otherwise be priced at chars/4, wildly overestimating tokens.
IMAGE_RESULT_CHARS = 6400

# Lines are only JSON-decoded when they contain one of these markers (cheap pre-filter for
# multi-hundred-MB rollouts).
_MARKERS = ('"session_meta"', '"turn_context"', '"token_count"', '"token_usage_record"',
            '"compacted"', '"function_call', '"custom_tool_call', '"local_shell_call"',
            '"context_compacted"')

SHELL_TOOLS = {"shell", "shell_command", "exec_command", "container.exec", "local_shell", "bash"}
READ_CMDS = {"cat", "sed", "head", "tail", "less", "more", "bat", "nl"}
WRITE_CMDS = {"cat", "echo", "printf", "tee"}
READ_FILE_TOOLS = {"read_file", "view_file", "open_file"}
PATCH_TOOLS = {"apply_patch"}

_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_NUMERIC_ARG_RE = re.compile(r"^[+\-]?[\d,$]+p?$")
_PATCH_FILE_RE = re.compile(r"^\*\*\* (Add|Update|Delete|Move to) File: (.+?)\s*$", re.M)
_REDIRECT_RE = re.compile(r"(?<![<>&\d])>{1,2}\s*([^\s;&|]+)")
_SPLIT_RE = re.compile(r"\s*(?:\|\||&&|\||;)\s*")


class UnsupportedRollout(Exception):
    """Raised for rollout files this Python cannot decode (e.g. .zst without a decoder)."""


_FRACTION_RE = re.compile(r"(\.\d{1,6})\d*")


def parse_ts(s: str) -> datetime:
    # Python < 3.11 only accepts 3- or 6-digit fractions; normalise to 6.
    s = _FRACTION_RE.sub(lambda m: m.group(1).ljust(7, "0"), s.replace("Z", "+00:00"), 1)
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def default_codex_home() -> str:
    return os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")


def default_session_dirs() -> List[str]:
    home = default_codex_home()
    return [os.path.join(home, "sessions"), os.path.join(home, "archived_sessions")]


def open_rollout(path: str):
    """Open a `.jsonl`, `.jsonl.gz` or `.jsonl.zst` rollout as a text stream."""
    if path.endswith(".zst"):
        try:
            from compression import zstd  # Python 3.14+
            return io.TextIOWrapper(zstd.open(path, "rb"), encoding="utf-8", errors="replace")
        except ImportError:
            pass
        try:
            import zstandard
            fh = open(path, "rb")
            return io.TextIOWrapper(zstandard.ZstdDecompressor().stream_reader(fh),
                                    encoding="utf-8", errors="replace")
        except ImportError:
            raise UnsupportedRollout("%s: .zst needs Python 3.14+ or the 'zstandard' package" % path)
    if path.endswith(".gz"):
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8", errors="replace")
    return open(path, encoding="utf-8", errors="replace")


def _usage(u: dict) -> Usage:
    return Usage(
        input=int(u.get("input_tokens") or 0),
        cached=int(u.get("cached_input_tokens") or 0),
        cache_write=int(u.get("cache_write_input_tokens") or 0),
        output=int(u.get("output_tokens") or 0),
        reasoning=int(u.get("reasoning_output_tokens") or 0),
    )


def _output_chars(output) -> int:
    if isinstance(output, list):
        total = 0
        for block in output:
            if isinstance(block, dict) and block.get("type") in ("input_image", "image"):
                total += IMAGE_RESULT_CHARS
            else:
                total += len(json.dumps(block, ensure_ascii=False))
        return total
    if isinstance(output, str):
        return len(output)
    return len(json.dumps(output, ensure_ascii=False))


def _strip_quotes(tok: str) -> str:
    if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "\"'":
        return tok[1:-1]
    return tok


def _command_string(args: dict) -> Optional[str]:
    cmd = args.get("command")
    if cmd is None:
        cmd = args.get("cmd")
    if isinstance(cmd, list):
        parts = [str(c) for c in cmd]
        if len(parts) >= 3 and parts[0].rsplit("/", 1)[-1] in ("bash", "sh", "zsh") and parts[1] in ("-lc", "-c"):
            return parts[2]
        return " ".join(parts)
    if isinstance(cmd, str):
        return cmd
    return None


def _analyze_shell(cmd: str, use: ToolUse) -> None:
    cmd = cmd.strip()
    if not cmd:
        return
    first = _SPLIT_RE.split(cmd, 1)[0]
    tokens = first.split()
    idx = 0
    while idx < len(tokens) and (_ENV_ASSIGN_RE.match(tokens[idx]) or tokens[idx] == "export"):
        idx += 1
    if idx >= len(tokens):
        return
    head = tokens[idx].rsplit("/", 1)[-1]
    use.command_head = head
    args = [_strip_quotes(t) for t in tokens[idx + 1:]]
    if head in READ_CMDS and ">" not in first and "<<" not in first:
        cands = [a for a in args if a and not a.startswith("-") and not _NUMERIC_ARG_RE.match(a)]
        if cands:
            use.read_path = cands[-1]
            use.file_path = cands[-1]
        return
    if head in WRITE_CMDS:
        targets = []
        if head == "tee":
            targets = [a for a in args if a and not a.startswith("-")]
        m = _REDIRECT_RE.search(cmd)
        if m:
            targets.append(_strip_quotes(m.group(1)))
        if targets:
            use.write_paths = targets[:1]
            use.file_path = targets[0]
            use.content_chars = len(cmd)


def _analyze_patch(patch: str, use: ToolUse) -> None:
    use.content_chars = len(patch)
    paths = _PATCH_FILE_RE.findall(patch)
    if paths:
        use.file_path = paths[0][1].strip()
        use.write_paths = [p.strip() for kind, p in paths if kind == "Add"]


def _parse_args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            d = json.loads(raw)
            return d if isinstance(d, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _tool_use_from_item(p: dict) -> Optional[ToolUse]:
    t = p.get("type")
    if t == "function_call":
        name = p.get("name") or "?"
        ns = p.get("namespace")
        use = ToolUse(id=p.get("call_id") or "", name=("%s.%s" % (ns, name)) if ns else name)
        args = _parse_args(p.get("arguments"))
        if name in SHELL_TOOLS:
            cmd = _command_string(args)
            if cmd:
                _analyze_shell(cmd, use)
        elif name in PATCH_TOOLS:
            patch = args.get("input") or args.get("patch")
            if isinstance(patch, str):
                _analyze_patch(patch, use)
        elif name in READ_FILE_TOOLS:
            fp = args.get("path") or args.get("file_path")
            if isinstance(fp, str):
                use.read_path = fp
                use.file_path = fp
        return use
    if t == "custom_tool_call":
        name = p.get("name") or "?"
        use = ToolUse(id=p.get("call_id") or "", name=name)
        inp = p.get("input")
        if name in PATCH_TOOLS and isinstance(inp, str):
            _analyze_patch(inp, use)
        return use
    if t == "local_shell_call":
        use = ToolUse(id=p.get("call_id") or "", name="local_shell")
        action = p.get("action")
        if isinstance(action, dict):
            cmd = _command_string(action)
            if cmd:
                _analyze_shell(cmd, use)
        return use
    return None


def _source_string(src) -> str:
    """Flatten SessionSource: "cli", "vscode", {"subagent": {"thread_spawn": {...}}} ..."""
    if isinstance(src, str):
        return src
    if isinstance(src, dict) and src:
        key, val = next(iter(src.items()))
        if key == "subagent":
            if isinstance(val, str):
                return "subagent:%s" % val
            if isinstance(val, dict) and val:
                return "subagent:%s" % next(iter(val))
            return "subagent"
        if key == "internal":
            return "internal:%s" % (val if isinstance(val, str) else "?")
        return str(key)
    return ""


def _parent_id(meta: dict) -> Optional[str]:
    pid = meta.get("parent_thread_id")
    if isinstance(pid, str):
        return pid
    src = meta.get("source")
    if isinstance(src, dict):
        sub = src.get("subagent")
        if isinstance(sub, dict):
            spawn = sub.get("thread_spawn")
            if isinstance(spawn, dict) and isinstance(spawn.get("parent_thread_id"), str):
                return spawn["parent_thread_id"]
    return None


def peek_meta(path: str) -> Optional[dict]:
    """Return the first session_meta payload without reading the whole file."""
    with open_rollout(path) as f:
        for _ in range(20):
            line = f.readline()
            if not line:
                break
            if '"session_meta"' not in line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(d, dict) and d.get("type") == "session_meta" and isinstance(d.get("payload"), dict):
                return d["payload"]
    return None


def parse_file(path: str, fallback_id: str, project_filter: Optional[str] = None) -> Optional[Session]:
    """Parse one rollout. Returns None when `project_filter` does not match the session cwd."""
    s = Session(session_id=fallback_id, project="", path=path)
    items: List[Tuple] = []     # ordered ("call"|"output"|"record"|"count"|"mode"|"compact", ...)
    meta_seen = False
    start_ordinal = None
    model = "unknown"
    effort = None
    policy = None
    last_count = None
    with open_rollout(path) as f:
        for line in f:
            if not any(m in line for m in _MARKERS):
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(d, dict):
                continue
            try:
                t = d.get("type")
                p = d.get("payload")
                if not isinstance(p, dict):
                    continue
                if t == "session_meta":
                    # A subagent rollout may also carry its parent's replayed meta. Prefer the
                    # meta whose id matches the filename; otherwise the first one wins.
                    if meta_seen and not (p.get("id") == fallback_id and s.session_id != fallback_id):
                        continue
                    meta_seen = True
                    s.session_id = p.get("id") or s.session_id
                    s.project = str(p.get("cwd") or "")
                    if project_filter and project_filter not in s.project:
                        return None
                    s.source = _source_string(p.get("source"))
                    s.parent_id = _parent_id(p)
                    so = p.get("subagent_history_start_ordinal")
                    start_ordinal = so if isinstance(so, int) else None
                    continue
                ordinal = d.get("ordinal")
                if start_ordinal is not None and isinstance(ordinal, int) and ordinal < start_ordinal:
                    continue  # inherited parent context, not this thread's own usage
                ts = parse_ts(d["timestamp"]) if d.get("timestamp") else None
                if t == "turn_context":
                    model = p.get("model") or model
                    eff = p.get("effort")
                    effort = eff if isinstance(eff, str) else effort
                    new_policy = (json.dumps(p.get("approval_policy"), sort_keys=True),
                                  json.dumps(p.get("sandbox_policy"), sort_keys=True))
                    if policy is not None and new_policy != policy:
                        items.append(("mode",))
                    policy = new_policy
                elif t == "compacted":
                    items.append(("compact",))
                elif t == "token_usage_record":
                    tid = p.get("thread_id")
                    if isinstance(tid, str) and tid != s.session_id:
                        continue
                    u = p.get("usage")
                    if isinstance(u, dict) and ts:
                        items.append(("record", ts, model, effort, _usage(u), p.get("response_id") or ""))
                elif t == "event_msg":
                    et = p.get("type")
                    if et == "token_count":
                        info = p.get("info")
                        if not isinstance(info, dict) or not ts:
                            continue
                        last = info.get("last_token_usage")
                        total = info.get("total_token_usage")
                        if not isinstance(last, dict):
                            continue
                        key = (json.dumps(last, sort_keys=True), json.dumps(total, sort_keys=True))
                        if key == last_count:
                            continue  # re-emitted on rate-limit-only updates
                        last_count = key
                        items.append(("count", ts, model, effort, _usage(last), ""))
                    elif et == "context_compacted":
                        items.append(("compact",))
                elif t == "response_item":
                    pt = p.get("type")
                    if pt in ("function_call_output", "custom_tool_call_output"):
                        items.append(("output", p.get("call_id") or "", ts, _output_chars(p.get("output"))))
                    else:
                        use = _tool_use_from_item(p)
                        if use is not None:
                            items.append(("call", use))
            except (ValueError, AttributeError, TypeError, KeyError):
                continue
    _assemble(s, items)
    if s.is_subagent:
        s.title = "%s (subagent)" % s.display_name()
    return s


def _assemble(s: Session, items: List[Tuple]) -> None:
    kind = "record" if any(it[0] == "record" for it in items) else "count"
    seen_ids = set()
    pending: List[ToolUse] = []
    call_turn = {}
    for it in items:
        tag = it[0]
        if tag == "call":
            pending.append(it[1])
        elif tag == "output":
            _, call_id, ts, chars = it
            idx = call_turn.get(call_id, len(s.turns))
            s.tool_results.append(ToolResult(tool_use_id=call_id, ts=ts, chars=chars, turn_index=idx))
        elif tag == kind:
            _, ts, model, effort, usage, rid = it
            if rid and rid in seen_ids:
                continue
            if rid:
                seen_ids.add(rid)
            turn = Turn(response_id=rid or "r%d" % len(s.turns), ts=ts, model=model, usage=usage,
                        effort=effort, tool_uses=pending)
            for u in pending:
                call_turn[u.id] = len(s.turns)
            pending = []
            s.turns.append(turn)
        elif tag == "mode":
            s.mode_events.append(len(s.turns))
        elif tag == "compact":
            s.compaction_events.append(len(s.turns))
    if pending and s.turns:
        s.turns[-1].tool_uses.extend(pending)
        for u in pending:
            call_turn[u.id] = len(s.turns) - 1
    n = len(s.turns)
    for r in s.tool_results:
        if r.turn_index >= n:
            r.turn_index = n - 1


def _rollout_files(root: str) -> List[str]:
    out = []
    for pattern in ("*/*/*/rollout-*.jsonl*", "rollout-*.jsonl*"):
        out += glob.glob(os.path.join(root, pattern))
    return sorted(p for p in out if p.endswith((".jsonl", ".jsonl.gz", ".jsonl.zst")))


def _session_id_from_name(path: str) -> str:
    name = os.path.basename(path)
    for suf in (".jsonl.zst", ".jsonl.gz", ".jsonl"):
        if name.endswith(suf):
            name = name[:-len(suf)]
            break
    core = name[len("rollout-"):] if name.startswith("rollout-") else name
    ids = core[20:] if len(core) > 20 else core
    return ids.split("_", 1)[0]


def load_sessions(session_dirs, days: int = 30, project_filter: Optional[str] = None,
                  all_time: bool = False, now: Optional[datetime] = None,
                  skipped: Optional[List[str]] = None) -> List[Session]:
    """Load rollouts from `session_dirs`, attach subagent threads to their parents, and
    return top-level sessions sorted by start time. Files this Python cannot decode are
    appended to `skipped`."""
    if isinstance(session_dirs, str):
        session_dirs = [session_dirs]
    now = now or datetime.now(timezone.utc)
    cutoff = None if all_time else now - timedelta(days=days)
    by_id = {}
    order: List[Session] = []
    for root in session_dirs:
        if not os.path.isdir(root):
            continue
        for fp in _rollout_files(root):
            if cutoff is not None:
                try:
                    mtime = datetime.fromtimestamp(os.path.getmtime(fp), tz=timezone.utc)
                except OSError:
                    continue
                if mtime < cutoff:
                    continue
            try:
                s = parse_file(fp, _session_id_from_name(fp), project_filter)
            except UnsupportedRollout:
                if skipped is not None:
                    skipped.append(fp)
                continue
            except OSError:
                continue
            if s is None or not s.turns:
                continue
            if cutoff is not None and s.end < cutoff:
                continue
            if s.session_id in by_id:
                continue  # same thread stored twice (e.g. sessions/ and archived_sessions/)
            by_id[s.session_id] = s
            order.append(s)
    top: List[Session] = []
    for s in order:
        parent = by_id.get(s.parent_id) if s.is_subagent and s.parent_id else None
        if parent is not None and parent is not s:
            parent.subagents.append(s)
        else:
            top.append(s)
    top.sort(key=lambda x: x.start)
    return top
