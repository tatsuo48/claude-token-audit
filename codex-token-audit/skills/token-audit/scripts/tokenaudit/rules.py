from dataclasses import dataclass, field
from typing import List, Optional

from .model import Session
from .pricing import Pricing

DEFAULT_THRESHOLDS = {
    "r1_min_missed_tokens": 20000,
    "r2_min_missed_tokens": 50000,
    "cache_ttl_min": 10,
    "r3_min_tokens": 8000,
    "r4_min_reads": 3,
    "r5_min_tokens": 4000,
    "r5_min_writes": 2,
    "r6_min_turns": 150,
    "r6_max_context": 150000,
    "chars_per_token": 4,
}


@dataclass
class Finding:
    rule: str
    session_id: str
    session_name: str
    project: str
    ts: str
    waste_usd: float
    evidence: dict = field(default_factory=dict)


def est_tokens(chars: int, th: dict) -> int:
    return int(chars / th["chars_per_token"])


def _finding(s: Session, rule: str, ts, waste: float, evidence: dict) -> Finding:
    return Finding(rule=rule, session_id=s.session_id, session_name=s.display_name(),
                   project=s.project, ts=ts.isoformat() if ts else "",
                   waste_usd=round(waste, 4), evidence=evidence)


def detect(session: Session, pricing: Pricing, thresholds: Optional[dict] = None) -> List[Finding]:
    th = dict(DEFAULT_THRESHOLDS)
    th.update(thresholds or {})
    out: List[Finding] = []
    out += detect_cache_misses(session, pricing, th)
    out += detect_large_results(session, pricing, th)
    out += detect_repeated_reads(session, pricing, th)
    out += detect_large_writes(session, pricing, th)
    out += detect_long_session(session, pricing, th)
    return out


def detect_cache_misses(s: Session, pricing: Pricing, th: dict) -> List[Finding]:
    """R1/R2: a prompt prefix that was in context on the previous response but was not
    served from the cache on this one, so it was billed at the full input price."""
    out: List[Finding] = []
    prev = None
    for i, t in enumerate(s.turns):
        u = t.usage
        if prev is not None:
            expected = min(prev.usage.input, u.input)
            missed = expected - u.cached
            if missed > 0:
                r = pricing.rates(t.model)
                gap_min = (t.ts - prev.ts).total_seconds() / 60.0
                waste = missed * (1.0 - r["read"]) * r["input"]
                if gap_min > th["cache_ttl_min"] and missed >= th["r1_min_missed_tokens"]:
                    out.append(_finding(s, "R1", t.ts, waste, {
                        "gap_min": int(round(gap_min)), "ttl_min": th["cache_ttl_min"],
                        "missed_tokens": missed, "context_tokens": u.context, "model": t.model}))
                elif missed >= th["r2_min_missed_tokens"]:
                    causes = []
                    if prev.model != t.model:
                        causes.append("model_change:%s->%s" % (prev.model, t.model))
                    if i in s.mode_events:
                        causes.append("mode_change")
                    if i in s.compaction_events:
                        causes.append("compaction")
                    out.append(_finding(s, "R2", t.ts, waste, {
                        "missed_tokens": missed, "gap_min": int(round(gap_min)),
                        "context_tokens": u.context, "causes": causes, "model": t.model}))
        prev = t
    return out


def detect_large_results(s: Session, pricing: Pricing, th: dict) -> List[Finding]:
    out: List[Finding] = []
    uses = {u.id: u for t in s.turns for u in t.tool_uses}
    n = len(s.turns)
    for res in s.tool_results:
        tokens = est_tokens(res.chars, th)
        if tokens < th["r3_min_tokens"] or not (0 <= res.turn_index < n):
            continue
        turn = s.turns[res.turn_index]
        r = pricing.rates(turn.model)
        remaining = n - 1 - res.turn_index
        # entered once at the input price, then re-read on every later response at the cached rate
        waste = tokens * (1.0 + remaining * r["read"]) * r["input"]
        use = uses.get(res.tool_use_id)
        ev = {"tool": use.name if use else "?", "est_tokens": tokens, "remaining_turns": remaining}
        if use and use.file_path:
            ev["file_path"] = use.file_path
        if use and use.command_head:
            ev["command"] = use.command_head
        out.append(_finding(s, "R3", res.ts or turn.ts, waste, ev))
    return out


def detect_repeated_reads(s: Session, pricing: Pricing, th: dict) -> List[Finding]:
    reads = {}
    for i, t in enumerate(s.turns):
        for u in t.tool_uses:
            if u.read_path:
                reads.setdefault(u.read_path, []).append((i, u.id))
    sizes = {r.tool_use_id: r.chars for r in s.tool_results}
    out: List[Finding] = []
    for path, lst in reads.items():
        if len(lst) < th["r4_min_reads"]:
            continue
        extra = sum(est_tokens(sizes.get(uid, 0), th) for _, uid in lst[1:])
        last_turn = s.turns[lst[-1][0]]
        r = pricing.rates(last_turn.model)
        waste = extra * r["input"]
        out.append(_finding(s, "R4", last_turn.ts, waste, {
            "file_path": path, "reads": len(lst), "extra_est_tokens": extra}))
    return out


def detect_large_writes(s: Session, pricing: Pricing, th: dict) -> List[Finding]:
    out: List[Finding] = []
    per_path = {}
    for i, t in enumerate(s.turns):
        r = pricing.rates(t.model)
        for u in t.tool_uses:
            if u.content_chars <= 0:
                continue
            tokens = est_tokens(u.content_chars, th)
            for p in u.write_paths:
                per_path.setdefault(p, []).append((i, tokens))
            if tokens >= th["r5_min_tokens"]:
                ev = {"kind": "large_write", "tool": u.name}
                if u.file_path:
                    ev["file_path"] = u.file_path
                ev["est_tokens"] = tokens
                out.append(_finding(s, "R5", t.ts, tokens * r["output"], ev))
    for path, lst in per_path.items():
        if len(lst) < th["r5_min_writes"]:
            continue
        extra = sum(tok for _, tok in lst[1:])
        last_turn = s.turns[lst[-1][0]]
        r = pricing.rates(last_turn.model)
        out.append(_finding(s, "R5", last_turn.ts, extra * r["output"], {
            "kind": "repeated_write", "file_path": path, "writes": len(lst),
            "extra_est_tokens": extra}))
    return out


def detect_long_session(s: Session, pricing: Pricing, th: dict) -> List[Finding]:
    n = len(s.turns)
    if n == 0:
        return []
    final_ctx = s.turns[-1].usage.context
    if n < th["r6_min_turns"] and final_ctx < th["r6_max_context"]:
        return []
    waste = 0.0
    for t in s.turns:
        over = t.usage.context - th["r6_max_context"]
        if over > 0:
            r = pricing.rates(t.model)
            waste += over * r["read"] * r["input"]
    return [_finding(s, "R6", s.turns[-1].ts, waste, {
        "turns": n, "final_context_tokens": final_ctx})]
