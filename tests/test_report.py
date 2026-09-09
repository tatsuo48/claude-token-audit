import json
import os
import tempfile
import unittest

from tokenaudit.loader import load_sessions
from tokenaudit.pricing import Pricing
from tokenaudit.report import build_report, render_markdown
from tokenaudit.rules import Finding, detect
from tests.fixtures.builder import assistant_line, title_line, tool_use, write_session

PRICING_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "skills", "token-audit", "scripts", "pricing.json")


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pricing = Pricing.load(PRICING_PATH)
        write_session(self.tmp.name, "proj-a", "s1", [
            title_line("Alpha | work"),
            assistant_line("m1", "2026-09-01T10:00:00Z", model="claude-opus-5",
                           usage={"input": 100, "c1": 30000, "output": 1000}),
            assistant_line("m2", "2026-09-01T13:00:00Z", model="claude-opus-5",
                           usage={"c1": 77503, "read": 100, "output": 500}),
        ], subagents={"agent-1": [assistant_line("a1", "2026-09-01T10:00:30Z",
                                                  model="claude-sonnet-5",
                                                  usage={"input": 1000, "output": 200})]})
        write_session(self.tmp.name, "proj-b", "s2", [
            assistant_line("m1", "2026-09-02T10:00:00Z", model="claude-weird-1",
                           usage={"input": 10, "output": 10}),
        ])
        self.sessions = load_sessions(self.tmp.name, all_time=True)
        self.findings = sorted(
            (f for s in self.sessions for f in detect(s, self.pricing)),
            key=lambda f: -f.waste_usd)
        self.report = build_report(self.sessions, self.findings, self.pricing, top=10, days=None)

    def tearDown(self):
        self.tmp.cleanup()

    def test_totals_and_models(self):
        r = self.report
        self.assertEqual(r["sessions"], 2)
        self.assertEqual(r["period"]["days"], None)
        self.assertEqual(r["period"]["from"][:10], "2026-09-01")
        self.assertEqual(r["period"]["to"][:10], "2026-09-02")
        expected_total = (
            100 * 5e-6 + 30000 * 2.0 * 5e-6 + 1000 * 25e-6
            + 77503 * 2.0 * 5e-6 + 100 * 0.1 * 5e-6 + 500 * 25e-6
            + 1000 * 2e-6 + 200 * 10e-6
            + 10 * 5e-6 + 10 * 25e-6)
        self.assertAlmostEqual(r["totals"]["total"], expected_total, places=6)
        models = [m["model"] for m in r["by_model"]]
        self.assertEqual(models[0], "claude-opus-5")
        self.assertIn("claude-sonnet-5", models)
        self.assertAlmostEqual(sum(m["share"] for m in r["by_model"]), 1.0, places=6)

    def test_findings_and_rule_summary(self):
        r = self.report
        self.assertEqual(r["findings"][0]["rule"], "R1")
        self.assertEqual(r["findings"][0]["session_name"], "Alpha | work")
        rules = {b["rule"]: b for b in r["by_rule"]}
        self.assertEqual(rules["R1"]["count"], 1)

    def test_info_section(self):
        info = self.report["info"]
        self.assertEqual(info["subagents"]["count"], 1)
        self.assertAlmostEqual(info["subagents"]["usd"], 1000 * 2e-6 + 200 * 10e-6)
        self.assertAlmostEqual(info["subagents"]["share"], info["subagents"]["usd"] / self.report["totals"]["total"])
        self.assertEqual(info["top_output_sessions"][0]["name"], "Alpha | work")
        self.assertIn("claude-opus-5", info["expensive_models"])
        self.assertEqual(info["short_sessions_on_expensive_models"], 2)  # s1 (opus) and s2 (unknown->opus)
        self.assertEqual(info["unknown_models"], ["claude-weird-1"])

    def test_json_serializable_and_top_limit(self):
        json.dumps(self.report)
        small = build_report(self.sessions, self.findings, self.pricing, top=0, days=7)
        self.assertEqual(small["findings"], [])
        self.assertEqual(small["period"]["days"], 7)

    def test_markdown_sections_and_pipe_escaping(self):
        md = render_markdown(self.report)
        for heading in ("# token-audit report", "## Cost by category", "## Cost by model",
                        "## Top findings", "## Findings by rule", "## Info"):
            self.assertIn(heading, md)
        self.assertIn("Alpha \\| work", md)
        self.assertIn("| R1 |", md)
        self.assertIn("claude-weird-1", md)
        self.assertIn("% of total)", md)
        self.assertLess(len(md.encode("utf-8")), 8192)

    def test_empty_input(self):
        r = build_report([], [], self.pricing, top=10, days=30)
        self.assertEqual(r["sessions"], 0)
        self.assertEqual(r["totals"]["total"], 0.0)
        self.assertEqual(r["info"]["subagents"]["share"], 0.0)
        md = render_markdown(r)
        self.assertIn("Sessions: 0", md)


if __name__ == "__main__":
    unittest.main()
