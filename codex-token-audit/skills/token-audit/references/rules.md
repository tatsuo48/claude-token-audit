# Finding rules and advice

Each finding printed by `analyze.py` carries a rule id and an evidence map.
Map the rule id to the advice below. Quote the evidence numbers; do not invent others.
All `waste` amounts are estimates of "what this would have cost less if avoided".

Background on OpenAI prompt caching, which R1 and R2 depend on: prefixes of 1024+ tokens are cached
automatically and served at 10% of the input price (`cached_input_tokens`). An in-memory cache entry
lives for about 5 to 10 minutes of inactivity (up to an hour); models with extended retention keep
the prefix for around 30 minutes and up to 24 hours. GPT-5.6 and later bill cache writes at 1.25x the
input price (`cache_write_input_tokens`). A miss is not a surcharge: the prefix is simply billed at the
full input price again.

## R1 — Cache miss after an idle gap

**What happened:** The gap between two responses (`gap_min`) exceeded the assumed cache lifetime
(`ttl_min`, default 10 minutes), and `missed_tokens` of the prompt that had been in context on the
previous response came back uncached, so they were billed at the full input price instead of 10%.
`gap_min` is measured between consecutive responses, so it includes the previous turn's generation
time and any tool execution; a gap only slightly above `ttl_min` may be a slow command rather than
idle time.

**Advice:**
- Before stepping away for longer than the cache lifetime, finish the task or write a short handoff
  note; resume in a fresh thread with only the context you need instead of continuing a large one.
- If you must resume a long thread, expect the resume itself to cost roughly the Waste amount shown.
  Decide whether the history is worth it or a summary would do.
- Run `/compact` before a planned break so the re-sent context is smaller.

## R2 — Cache miss mid-session

**What happened:** `missed_tokens` of the prompt were not served from the cache even though the
previous response was recent. Something changed the prompt prefix. `causes` lists what the analyzer
could see: `model_change:<from>-><to>`, `mode_change` (approval or sandbox policy changed),
`compaction` (the thread was compacted just before this response). Other invisible causes: MCP servers
or plugins enabled/disabled, a change to AGENTS.md or the instructions, or the cache being evicted
under load.

**Advice:**
- Switch model, reasoning effort, approval mode, or MCP configuration at the start of a thread, not in
  the middle.
- If `context_tokens` is large and `compaction` is listed, the thread was near its window: see R6.
- Keep the set of enabled MCP servers and plugins stable for the life of a thread.

## R3 — Oversized tool output pulled into context

**What happened:** A single tool output (`tool`, `est_tokens`) entered the context and was then re-sent
on every one of the following `remaining_turns` responses at the cached rate. Waste grows with thread
length. `command` shows the first word of the shell command only; `file_path` is the last path
argument when the command was a file read.

**Advice by tool:**
- `shell` / `exec_command` (`cat`, `sed`, `git diff`, test runners, build logs): pipe through `head`,
  `tail`, `grep`, or `rg`, or redirect to a file and read selectively. Avoid `cat` on logs and build
  output; ask Codex to run tests with quiet flags.
- `read_file`: read a line range instead of the whole file.
- MCP tools: prefer filtered queries, or ask for summaries instead of raw dumps.
- Any tool: delegate exploratory reading to a subagent so the bulk never enters the main thread.
- If the same large output was needed once, consider `/compact` right after using it.

## R4 — Same file read repeatedly

**What happened:** `file_path` was read `reads` times in one thread (via `cat`, `sed -n`, `head`,
`tail`, or `read_file`); the repeats added about `extra_est_tokens` tokens of duplicated context.

**Advice:**
- Read once, then rely on the copy already in context; after a patch, re-read only the changed range
  with `sed -n 'A,Bp'`.
- If the file is a reference you keep coming back to, put the relevant summary in AGENTS.md.

## R5 — Large or repeated file writes

**What happened:** Output tokens cost 5 to 8x input. `kind=large_write`: one `apply_patch` or shell
heredoc of `est_tokens` tokens touching `file_path`. `kind=repeated_write`: `file_path` was created in
full `writes` times (`*** Add File` or a shell redirect); the repeats generated `extra_est_tokens`
extra output tokens. A single write that is both large and a repeat appears in both kinds and is
counted twice in the rule totals.

**Advice:**
- Generate large or repetitive files with a script or template run through the shell instead of
  emitting the content as model output.
- Use `*** Update File` hunks for partial changes; never recreate a whole file to change a few lines.
- For data files (fixtures, JSON, CSV), produce them programmatically.

## R6 — Thread too long

**What happened:** The thread reached `turns` responses and a final prompt of `final_context_tokens`
tokens. Every response re-sends the whole context; the waste is the cached-rate cost of carrying the
part above the threshold.

**Advice:**
- Split work by task: start a new thread at natural boundaries (`/new`), or use `codex resume` only
  when the old context is really needed.
- Use subagents for research-heavy phases so their reading does not accumulate in the main thread.
- Run `/compact` proactively at milestones instead of waiting for automatic compaction.

## Info section (not findings)

- **Cost by category:** `output` dominating means R5 habits and reasoning effort matter most; `input`
  (uncached) dominating means cache misses (R1/R2); `cache_read` dominating means threads are long
  (R6) or carry big outputs (R3).
- **Reasoning:** the share of output tokens spent on reasoning. If it is high on routine tasks, lower
  the effort (`/model` → reasoning effort, or `model_reasoning_effort` in `config.toml`).
- **Sessions by reasoning effort:** many `xhigh`/`high` threads on small tasks suggest a lower default.
- **Short sessions on expensive models:** many short threads on gpt-5.5 / gpt-5.6-sol tier models
  suggest routing quick tasks to gpt-5.6-terra or a mini model.
- **Subagents:** a high share is not waste by itself; check whether the parent threads also show R3.
- **Unknown models:** the price table needs an entry in `scripts/pricing.json`.
- **Skipped compressed rollouts:** Codex compresses old rollouts to `.jsonl.zst`; reading them needs
  Python 3.14+ or the `zstandard` package.
