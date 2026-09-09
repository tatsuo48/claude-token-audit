from collections import Counter, defaultdict
from dataclasses import asdict
from typing import List, Optional

from .model import Session
from .pricing import Cost, Pricing
from .rules import Finding

SHORT_SESSION_TURNS = 10
TOP_OUTPUT_SESSIONS = 5


def _cost_dict(c: Cost) -> dict:
    return {"input": c.input, "cache_write": c.cache_write, "cache_read": c.cache_read,
            "output": c.output, "total": c.total}


def build_report(sessions: List[Session], findings: List[Finding], pricing: Pricing,
                 top: int = 10, days: Optional[int] = 30, skipped_files: int = 0) -> dict:
    totals = Cost()
    by_model = defaultdict(Cost)
    model_sessions = defaultdict(set)
    sub_cost = Cost()
    sub_count = 0
    output_rows = []
    short_expensive = 0
    starts, ends = [], []
    out_tokens_all = 0
    reasoning_all = 0
    effort_sessions = Counter()

    for s in sessions:
        sc = Cost()
        out_tokens = 0
        efforts = set()
        for t in s.turns:
            c = pricing.cost(t.model, t.usage)
            sc = sc + c
            by_model[t.model] = by_model[t.model] + c
            model_sessions[t.model].add(s.session_id)
            out_tokens += t.usage.output
            reasoning_all += t.usage.reasoning
            if t.effort:
                efforts.add(t.effort)
        for a in s.subagents:
            sub_count += 1
            for t in a.turns:
                c = pricing.cost(t.model, t.usage)
                sc = sc + c
                sub_cost = sub_cost + c
                by_model[t.model] = by_model[t.model] + c
                model_sessions[t.model].add(s.session_id)
                reasoning_all += t.usage.reasoning
                out_tokens_all += t.usage.output
        totals = totals + sc
        out_tokens_all += out_tokens
        for e in efforts:
            effort_sessions[e] += 1
        output_rows.append({"name": s.display_name(), "project": s.project,
                            "usd_output": sc.output, "output_tokens": out_tokens})
        if s.turns and len(s.turns) <= SHORT_SESSION_TURNS and pricing.is_expensive(s.turns[0].model):
            short_expensive += 1
        if s.start:
            starts.append(s.start)
            ends.append(s.end)

    grand = totals.total or 1.0
    models = sorted(
        ({"model": m, "usd": c.total, "share": c.total / grand, "sessions": len(model_sessions[m])}
         for m, c in by_model.items()),
        key=lambda x: -x["usd"])
    by_rule = Counter()
    rule_usd = defaultdict(float)
    for f in findings:
        by_rule[f.rule] += 1
        rule_usd[f.rule] += f.waste_usd
    expensive = sorted(m for m in pricing.models if pricing.is_expensive(m))
    output_rows.sort(key=lambda x: -x["usd_output"])

    return {
        "period": {"days": days,
                   "from": min(starts).isoformat() if starts else "",
                   "to": max(ends).isoformat() if ends else ""},
        "sessions": len(sessions),
        "totals": _cost_dict(totals),
        "by_model": models,
        "findings": [asdict(f) for f in findings[:max(top, 0)]],
        "by_rule": [{"rule": r, "count": by_rule[r], "usd": rule_usd[r]} for r in sorted(by_rule)],
        "info": {
            "top_output_sessions": output_rows[:TOP_OUTPUT_SESSIONS],
            "subagents": {"count": sub_count, "usd": sub_cost.total, "share": sub_cost.total / grand},
            "reasoning": {"tokens": reasoning_all, "output_tokens": out_tokens_all,
                          "share": (reasoning_all / out_tokens_all) if out_tokens_all else 0.0},
            "effort_sessions": dict(sorted(effort_sessions.items())),
            "expensive_models": expensive,
            "short_sessions_on_expensive_models": short_expensive,
            "unknown_models": sorted(pricing.unknown_models),
            "skipped_files": skipped_files,
        },
    }


def _usd(x: float) -> str:
    return "$%.2f" % x


def _cell(v) -> str:
    return str(v).replace("|", "\\|").replace("\n", " ")


def _short(project: str, n: int = 40) -> str:
    return project if len(project) <= n else "…" + project[-n:]


def render_markdown(r: dict) -> str:
    L = ["# codex token-audit report", ""]
    p = r["period"]
    span = "last %d days" % p["days"] if p["days"] else "all time"
    L.append("- Period: %s to %s (%s)" % (p["from"][:10] or "-", p["to"][:10] or "-", span))
    L.append("- Sessions: %d (subagent threads: %d)" % (r["sessions"], r["info"]["subagents"]["count"]))
    L.append("- Total estimated cost: %s" % _usd(r["totals"]["total"]))
    L.append("- All amounts are estimates from rollout usage fields and the bundled price table.")

    tot = r["totals"]["total"] or 1.0
    L += ["", "## Cost by category", "", "| Category | USD | Share |", "|---|---|---|"]
    for k in ("input", "cache_write", "cache_read", "output"):
        L.append("| %s | %s | %.0f%% |" % (k, _usd(r["totals"][k]), 100.0 * r["totals"][k] / tot))

    L += ["", "## Cost by model", "", "| Model | USD | Share | Sessions |", "|---|---|---|---|"]
    for m in r["by_model"]:
        L.append("| %s | %s | %.0f%% | %d |" % (_cell(m["model"]), _usd(m["usd"]), 100.0 * m["share"], m["sessions"]))

    L += ["", "## Top findings (estimated waste)", "",
          "| # | Rule | Waste | Session | Project | When | Evidence |",
          "|---|---|---|---|---|---|---|"]
    for i, f in enumerate(r["findings"], 1):
        ev = ", ".join("%s=%s" % (k, v) for k, v in f["evidence"].items())
        L.append("| %d | %s | %s | %s | %s | %s | %s |" % (
            i, f["rule"], _usd(f["waste_usd"]), _cell(f["session_name"]),
            _cell(_short(f["project"])), f["ts"][:16], _cell(ev)))

    L += ["", "## Findings by rule", "", "| Rule | Count | Total waste |", "|---|---|---|"]
    for b in r["by_rule"]:
        L.append("| %s | %d | %s |" % (b["rule"], b["count"], _usd(b["usd"])))

    info = r["info"]
    L += ["", "## Info", ""]
    tops = "; ".join("%s (%s, %d tokens)" % (_cell(x["name"]), _usd(x["usd_output"]), x["output_tokens"])
                     for x in info["top_output_sessions"]) or "-"
    L.append("- Output-heavy sessions: " + tops)
    L.append("- Reasoning: %d of %d output tokens (%.0f%%)" % (
        info["reasoning"]["tokens"], info["reasoning"]["output_tokens"], 100.0 * info["reasoning"]["share"]))
    if info["effort_sessions"]:
        L.append("- Sessions by reasoning effort: " + ", ".join(
            "%s=%d" % (_cell(k), v) for k, v in info["effort_sessions"].items()))
    L.append("- Subagents: %d threads, %s (%.0f%% of total)" % (
        info["subagents"]["count"], _usd(info["subagents"]["usd"]), 100.0 * info["subagents"]["share"]))
    L.append("- Short sessions (<= %d responses) on expensive models (%s): %d" % (
        SHORT_SESSION_TURNS, ", ".join(info["expensive_models"]), info["short_sessions_on_expensive_models"]))
    if info["unknown_models"]:
        L.append("- Unknown models priced as default: " + ", ".join(_cell(m) for m in info["unknown_models"]))
    if info["skipped_files"]:
        L.append("- Skipped %d compressed rollout(s) (.zst): install Python 3.14+ or `pip install zstandard`" % info["skipped_files"])
    return "\n".join(L) + "\n"
