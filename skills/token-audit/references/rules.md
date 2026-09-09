# Finding rules and advice

Each finding printed by `analyze.py` carries a rule id and an evidence map.
Map the rule id to the advice below. Quote the evidence numbers; do not invent others.
All `waste` amounts are estimates of "what this would have cost less if avoided".

## R1 — Cache rewrite after an idle gap

**What happened:** The gap between two requests (`gap_min`) exceeded the prompt-cache TTL (`ttl_min`),
so the whole context (`cache_write_tokens`) was re-written to cache at 1.25x (5m TTL) or 2x (1h TTL)
the input price instead of being read at 0.1x. `gap_min` is measured between consecutive assistant
timestamps, so it includes the current turn's generation time; a gap only slightly above `ttl_min` may
be a long-running turn rather than idle time.

**Advice:**
- Before stepping away for longer than the TTL, finish the task or write a short handoff note; resume in a
  fresh session with only the context you need instead of continuing a large one.
- If you must resume a long session, expect the resume itself to cost roughly the Waste amount shown for
  the finding. Decide whether the history is worth it or a summary would do.
- Use `/compact` before a planned break so the rewritten context is smaller.

## R2 — Large cache rewrite mid-session

**What happened:** A cache write of `cache_write_tokens` happened within the TTL. Something changed the
prompt prefix. `causes` lists what the analyzer could see: `model_change:<from>-><to>`, `mode_change`
(permission or plan mode toggled). Other invisible causes: MCP servers or plugins enabled/disabled,
`/compact`, a system-prompt change from a hook, or a context-window compaction near the limit
(`context_tokens` close to the model's window).

**Advice:**
- Switch model, permission mode, or MCP configuration at the start of a session, not in the middle.
- If `context_tokens` is large, this was likely an automatic compaction: see R6.
- Keep the set of enabled MCP servers stable for the life of a session.

## R3 — Oversized tool result pulled into context

**What happened:** A single tool result (`tool`, `est_tokens`) entered the context and was then re-read
on every one of the following `remaining_turns` turns at the cache-read rate. Waste grows with session length.

**Advice by tool:**
- `Read`: use `offset`/`limit`, or `grep -n` first and read only the matching region.
- `Bash` (`command` shows the first word only): pipe through `head`, `tail`, `grep`, or redirect to a file and
  read selectively. Avoid `cat` on logs and build outputs.
- `Grep`/`Glob`: narrow the path or pattern, or use `head_limit`/`output_mode: files_with_matches`.
- Any tool: delegate exploratory reading to a subagent (Explore) so the bulk never enters the main context.
- If the same large result was needed once, consider `/clear` or `/compact` right after using it.

## R4 — Same file read repeatedly

**What happened:** `file_path` was read `reads` times in one session; the repeats added about
`extra_est_tokens` tokens of duplicated context.

**Advice:**
- Read once, then rely on the copy already in context; after an edit, re-read only the changed range
  with `offset`/`limit`.
- If the file is a reference you keep coming back to, put the relevant summary in CLAUDE.md or a memory note.

## R5 — Large or repeated Write output

**What happened:** Output tokens cost about 5x input. `kind=large_write`: one `Write` of `est_tokens`
tokens to `file_path`. `kind=repeated_write`: `file_path` was rewritten `writes` times; the repeats generated
`extra_est_tokens` extra output tokens. A single Write that is both large and a repeat appears in both
`large_write` and `repeated_write` and is counted twice in the rule totals.

**Advice:**
- Generate large or repetitive files with a script or template run through Bash instead of emitting the
  content as model output.
- Use `Edit` for partial changes; never rewrite a whole file to change a few lines.
- For data files (fixtures, JSON, CSV), produce them programmatically.

## R6 — Session too long

**What happened:** The session reached `turns` turns and a final context of `final_context_tokens` tokens.
Every turn re-reads the whole context; the waste is the cost of carrying the part above the threshold.

**Advice:**
- Split work by task: `/clear` at natural boundaries, or start a new session per task.
- Use subagents for research-heavy phases so their reading does not accumulate in the main context.
- Run `/compact` proactively at milestones instead of waiting for automatic compaction.

## Info section (not findings)

- **Cost by category:** output and cache_write dominating means R5/R1/R2 habits matter most; cache_read
  dominating means sessions are long (R6) or carry big results (R3).
- **Short sessions on expensive models:** many short sessions on Fable/Opus-tier models suggest routing
  quick tasks to a cheaper model (`/model`) or lower effort.
- **Subagents:** a high share is not waste by itself; check whether the parent sessions also show R3.
- **Unknown models:** the price table needs an entry in `scripts/pricing.json`.
