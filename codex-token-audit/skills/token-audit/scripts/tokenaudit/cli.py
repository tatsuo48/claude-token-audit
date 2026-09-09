import argparse
import json
import os
import sys
from datetime import datetime
from typing import List, Optional

from .loader import default_session_dirs, load_sessions
from .pricing import Pricing
from .report import build_report, render_markdown
from .rules import DEFAULT_THRESHOLDS, detect

DEFAULT_PRICING = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pricing.json")


def parse_thresholds(items: List[str]) -> dict:
    out = {}
    for item in items:
        if "=" not in item:
            sys.exit("--threshold expects NAME=VALUE, got: %s" % item)
        k, v = item.split("=", 1)
        if k not in DEFAULT_THRESHOLDS:
            sys.exit("unknown threshold %r. Known: %s" % (k, ", ".join(sorted(DEFAULT_THRESHOLDS))))
        try:
            out[k] = int(v)
        except ValueError:
            try:
                out[k] = float(v)
            except ValueError:
                sys.exit("--threshold value for %s must be a number, got: %r" % (k, v))
        if out[k] <= 0:
            sys.exit("--threshold value for %s must be > 0" % k)
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="analyze.py",
        description="Summarize Codex CLI rollouts and rank token waste by estimated cost. "
                    "Reads only local files; prints no message bodies.")
    p.add_argument("--days", type=int, default=30, help="look back N days (default 30)")
    p.add_argument("--all", action="store_true", help="ignore --days and scan every session")
    p.add_argument("--project", help="only sessions whose cwd contains this substring")
    p.add_argument("--top", type=int, default=10, help="number of findings to print (default 10)")
    p.add_argument("--format", choices=["md", "json"], default="md")
    p.add_argument("--sessions-dir", action="append", default=None, metavar="DIR",
                   help="rollout directory (repeatable). Default: <CODEX_HOME or ~/.codex>/sessions "
                        "and archived_sessions")
    p.add_argument("--pricing", default=None, help="override pricing.json path")
    p.add_argument("--threshold", action="append", default=[], metavar="NAME=VALUE",
                   help="override a detection threshold; repeatable. Known: %s" % ", ".join(sorted(DEFAULT_THRESHOLDS)))
    return p


def run(argv: Optional[List[str]] = None, now: Optional[datetime] = None) -> str:
    args = build_parser().parse_args(argv)
    thresholds = parse_thresholds(args.threshold)
    pricing = Pricing.load(args.pricing or DEFAULT_PRICING)
    skipped: List[str] = []
    sessions = load_sessions(args.sessions_dir or default_session_dirs(), days=args.days,
                             project_filter=args.project, all_time=args.all, now=now, skipped=skipped)
    findings = []
    for s in sessions:
        findings += detect(s, pricing, thresholds)
        for a in s.subagents:
            findings += detect(a, pricing, thresholds)
    findings.sort(key=lambda f: -f.waste_usd)
    report = build_report(sessions, findings, pricing, top=args.top,
                          days=None if args.all else args.days, skipped_files=len(skipped))
    if args.format == "json":
        return json.dumps(report, ensure_ascii=False, indent=2)
    return render_markdown(report)


def main(argv: Optional[List[str]] = None) -> None:
    sys.stdout.write(run(argv))
