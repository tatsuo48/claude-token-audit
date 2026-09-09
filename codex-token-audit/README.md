# codex-token-audit

A Codex CLI skill that audits your past sessions for wasted tokens, converts each waste pattern into
an estimated dollar amount, and tells you what to change.

Existing tools (`/status`, `ccusage codex`) tell you how much you spent. This one tells you why, and
what to do differently. It is the Codex counterpart of
[claude-token-audit](https://github.com/tatsuo48/claude-token-audit).

## What it detects

| Rule | Pattern | Why it costs |
|---|---|---|
| R1 | Prompt-cache miss after an idle gap longer than the cache lifetime | Whole context billed at the full input price instead of 10% |
| R2 | Large cache miss mid-session (model/mode change, compaction, MCP change) | Same as above |
| R3 | Oversized command or tool output pulled into context | Re-sent on every following response |
| R4 | Same file read 3+ times in one thread (`cat`, `sed -n`, `read_file`) | Duplicated context |
| R5 | Large or repeated `apply_patch` / heredoc writes | Output tokens cost 5–8x input |
| R6 | Threads over 150 responses or 150k context | Every response carries the whole history |

Plus an info section: cost by category and model, reasoning share of output, sessions by reasoning
effort, output-heavy sessions, subagent share, short sessions on expensive models.

## Install

As a Codex plugin (Codex CLI 0.122 or later):

```
codex plugin marketplace add tatsuo48/codex-token-audit
codex plugin add token-audit@codex-token-audit
```

With the agentskills.io CLI:

```
npx skills add tatsuo48/codex-token-audit
```

Or copy `skills/token-audit/` into `~/.codex/skills/` (personal) or `.agents/skills/` in a repo.
No dependencies beyond Python 3.9+ (reading `.jsonl.zst` rollouts needs Python 3.14+ or
`pip install zstandard`).

## Use

Ask Codex for a token audit in plain language, for example "where am I wasting tokens?",
"audit the last 90 days", or "token audit for project foo". The skill triggers on those phrases.

To invoke it explicitly, mention the skill:

```
$token-audit
```

You can also run the analyzer directly:

```bash
python3 skills/token-audit/scripts/analyze.py --days 30
python3 skills/token-audit/scripts/analyze.py --all --format json --top 25
python3 skills/token-audit/scripts/analyze.py --project my-repo --threshold r3_min_tokens=4000
```

## Privacy

- Reads only local rollouts under `$CODEX_HOME/sessions` and `$CODEX_HOME/archived_sessions`
  (default `~/.codex`).
- Prints working directories, session ids, file paths, tool names, and numbers. Never message bodies,
  command output, or full shell commands (only the first word of a command).
- No network access.

## Pricing

Prices live in `skills/token-audit/scripts/pricing.json` (USD per 1M tokens for the OpenAI API, plus
cache multipliers). Unknown model ids are priced as the default model and listed in the report. Edit
the file to add models. All amounts are estimates. If you use Codex through a ChatGPT plan you are not
billed per token, but the same waste consumes your plan's rate limits.

## How it reads rollouts

See [docs/rollout-format.md](docs/rollout-format.md) for the record types the analyzer relies on and
how subagent threads and duplicated usage events are handled.

## Development

```bash
python3 -m unittest discover -s tests -t . -v
```

## License

MIT
