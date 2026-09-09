# claude-token-audit

A Claude Code skill that audits your past sessions for wasted tokens, converts each waste pattern into
an estimated dollar amount, and tells you what to change.

Existing tools (`/cost`, `/stats`, `ccusage`) tell you how much you spent. This one tells you why, and what
to do differently.

Using Codex CLI instead? See [codex-token-audit](https://github.com/tatsuo48/codex-token-audit), the same
audit for `~/.codex` rollouts.

## What it detects

| Rule | Pattern | Why it costs |
|---|---|---|
| R1 | Cache rewrite after an idle gap longer than the cache TTL | Whole context re-written at 1.25x–2x instead of read at 0.1x |
| R2 | Large cache rewrite mid-session (model/mode/MCP change, compaction) | Same as above |
| R3 | Oversized tool result pulled into context | Re-read on every following turn |
| R4 | Same file read 3+ times in one session | Duplicated context |
| R5 | Large or repeated `Write` outputs | Output tokens cost ~5x input |
| R6 | Sessions over 150 turns or 150k context | Every turn carries the whole history |

Plus an info section: cost by category and model, output-heavy sessions, subagent share, short sessions on
expensive models.

## Install

As a Claude Code plugin (recommended):

```
/plugin marketplace add tatsuo48/claude-token-audit
/plugin install token-audit@claude-token-audit
```

With the agentskills.io CLI:

```
npx skills add tatsuo48/claude-token-audit
```

Or copy `skills/token-audit/` into `~/.claude/skills/`. No dependencies beyond Python 3.9+.

## Use

Ask Claude Code for a token audit in plain language, for example "where am I wasting tokens?",
"audit the last 90 days", or "token audit for project foo". The skill triggers on those phrases.

To invoke it explicitly:

```
/token-audit:token-audit          # installed as a plugin
/token-audit                      # copied into ~/.claude/skills/
```

You can also run the analyzer directly:

```bash
python3 skills/token-audit/scripts/analyze.py --days 30
python3 skills/token-audit/scripts/analyze.py --all --format json --top 25
python3 skills/token-audit/scripts/analyze.py --project my-repo --threshold r3_min_tokens=4000
```

## Privacy

- Reads only local files under `~/.claude/projects` (or `$CLAUDE_CONFIG_DIR/projects`).
- Prints session titles, project slugs, file paths, tool names, and numbers. Never message bodies,
  tool results, or full shell commands (only the first word of a Bash command).
- No network access.

## Pricing

Prices live in `skills/token-audit/scripts/pricing.json` (USD per 1M tokens, plus cache multipliers).
Unknown model ids are priced as the default model and listed in the report. Edit the file to add models.
All amounts are estimates.

## Development

```bash
python3 -m unittest discover -s tests -t . -v
```

## License

MIT
