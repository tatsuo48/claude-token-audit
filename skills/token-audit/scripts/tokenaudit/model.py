from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


@dataclass
class Usage:
    input: int = 0
    cache_5m: int = 0
    cache_1h: int = 0
    cache_read: int = 0
    output: int = 0

    @property
    def cache_write(self) -> int:
        return self.cache_5m + self.cache_1h

    @property
    def context(self) -> int:
        """Approximate prompt size seen by this turn."""
        return self.input + self.cache_read + self.cache_write


@dataclass
class ToolUse:
    id: str
    name: str
    file_path: Optional[str] = None
    content_chars: int = 0        # Write tool content length in chars (Edit is not counted)
    command_head: Optional[str] = None  # first word of a Bash command only


@dataclass
class ToolResult:
    tool_use_id: str
    ts: Optional[datetime]
    chars: int                    # json.dumps(content) length
    turn_index: int               # index of the Turn that issued the call


@dataclass
class Turn:
    """One unique assistant message (deduplicated by message.id)."""
    msg_id: str
    ts: datetime
    model: str
    usage: Usage
    tool_uses: List[ToolUse] = field(default_factory=list)


@dataclass
class Session:
    session_id: str
    project: str
    path: str
    title: str = ""
    turns: List[Turn] = field(default_factory=list)
    tool_results: List[ToolResult] = field(default_factory=list)
    mode_events: List[int] = field(default_factory=list)  # turn index the event precedes
    subagents: List["Session"] = field(default_factory=list)

    @property
    def start(self) -> Optional[datetime]:
        return self.turns[0].ts if self.turns else None

    @property
    def end(self) -> Optional[datetime]:
        return self.turns[-1].ts if self.turns else None

    def display_name(self) -> str:
        return self.title or self.session_id[:8]
