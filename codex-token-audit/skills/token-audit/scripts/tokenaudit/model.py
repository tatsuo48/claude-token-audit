from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


@dataclass
class Usage:
    """Token usage of one model response, as reported by the Responses API.

    `input` is the whole prompt. `cached` and `cache_write` are the parts of that prompt
    served from / written to the prompt cache (subsets of `input`). `output` includes
    `reasoning`.
    """
    input: int = 0
    cached: int = 0
    cache_write: int = 0
    output: int = 0
    reasoning: int = 0

    @property
    def uncached(self) -> int:
        return max(self.input - self.cached - self.cache_write, 0)

    @property
    def context(self) -> int:
        """Prompt size seen by this response."""
        return self.input


@dataclass
class ToolUse:
    id: str
    name: str
    file_path: Optional[str] = None          # first file path the call touches, if any
    content_chars: int = 0                   # size of written content (apply_patch / heredoc)
    command_head: Optional[str] = None       # first word of a shell command only
    read_path: Optional[str] = None          # set when the call reads one file (cat, sed, read_file ...)
    write_paths: List[str] = field(default_factory=list)  # files written in full (Add File, redirect)


@dataclass
class ToolResult:
    tool_use_id: str
    ts: Optional[datetime]
    chars: int                    # json.dumps(output) length (images use a flat constant)
    turn_index: int               # index of the Turn that issued the call


@dataclass
class Turn:
    """One model response (one Responses API call)."""
    response_id: str
    ts: datetime
    model: str
    usage: Usage
    effort: Optional[str] = None
    tool_uses: List[ToolUse] = field(default_factory=list)


@dataclass
class Session:
    session_id: str
    project: str                  # cwd recorded in session_meta
    path: str
    title: str = ""
    source: str = ""              # cli, vscode, exec, mcp, subagent:<kind> ...
    parent_id: Optional[str] = None
    turns: List[Turn] = field(default_factory=list)
    tool_results: List[ToolResult] = field(default_factory=list)
    mode_events: List[int] = field(default_factory=list)        # turn index the event precedes
    compaction_events: List[int] = field(default_factory=list)  # turn index the event precedes
    subagents: List["Session"] = field(default_factory=list)

    @property
    def is_subagent(self) -> bool:
        return self.source.startswith("subagent")

    @property
    def start(self) -> Optional[datetime]:
        return self.turns[0].ts if self.turns else None

    @property
    def end(self) -> Optional[datetime]:
        return self.turns[-1].ts if self.turns else None

    def display_name(self) -> str:
        if self.title:
            return self.title
        base = self.project.rstrip("/").rsplit("/", 1)[-1] or self.project
        return "%s %s" % (base, self.session_id[:8]) if base else self.session_id[:8]
