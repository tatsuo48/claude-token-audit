# Rollout format notes

Codex CLI writes one JSONL file per thread at
`$CODEX_HOME/sessions/YYYY/MM/DD/rollout-<YYYY-MM-DDTHH-MM-SS>-<thread-id>.jsonl`
(archived threads under `archived_sessions/`; old files may be compressed to `.jsonl.zst`).
Every line is `{"timestamp": "...", "type": "...", "payload": {...}}` with an optional `ordinal`.

The analyzer decodes only these record types:

| `type` | Used for |
|---|---|
| `session_meta` | thread id, `cwd` (the project), `source` (cli / vscode / subagent…), `parent_thread_id`, `subagent_history_start_ordinal` |
| `turn_context` | current `model`, `effort`, `approval_policy` + `sandbox_policy` (a change marks a mode event) |
| `token_usage_record` | one record per model response with `response_id` and `usage` (preferred when present) |
| `event_msg` / `token_count` | `info.last_token_usage` per response (fallback; duplicates re-emitted on rate-limit updates are dropped) |
| `compacted`, `event_msg` / `context_compacted` | compaction events |
| `response_item` / `function_call`, `custom_tool_call`, `local_shell_call` | tool calls: command head, read paths, patch paths and sizes |
| `response_item` / `function_call_output`, `custom_tool_call_output` | tool output size in characters |

Usage fields: `input_tokens` is the whole prompt; `cached_input_tokens` and
`cache_write_input_tokens` are the parts of it served from / written to the prompt cache;
`output_tokens` includes `reasoning_output_tokens`.

Subagent threads (`source.subagent.thread_spawn`) start with a replay of the parent's context.
Records whose `ordinal` is below `subagent_history_start_ordinal` are skipped, and
`token_usage_record` lines for a different `thread_id` are ignored, so the parent's usage is not
counted twice. Subagent costs are attached to the parent thread when it is in the scanned period.

Tool calls are attached to the next usage record (the response that emitted them); tool outputs
are attributed to the response that issued the call.
