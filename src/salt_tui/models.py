from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class CommandSpec:
    executable: str = "salt"
    function: str = "test.version"
    target: str = "*"
    arguments: list[str] = field(default_factory=list)
    options: list[str] = field(default_factory=list)
    target_type: str = "glob"
    saltenv: str | None = None
    pillarenv: str | None = None
    test: bool = False
    timeout: int | None = None
    batch: str | None = None
    async_run: bool = False


@dataclass
class StateResult:
    minion: str
    state_id: str
    module: str
    function: str
    name: str
    sls: str
    result: bool | None
    changes: Any
    comment: str
    duration_ms: float | None
    started_at: str | None
    raw: dict[str, Any]


@dataclass
class RunResult:
    command: str
    argv: list[str]
    target: str
    target_type: str
    function: str
    started_at: str = field(default_factory=now)
    uuid: str = field(default_factory=lambda: str(uuid4()))
    finished_at: str | None = None
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    parsed: Any = None
    states: list[StateResult] = field(default_factory=list)
    status: str = "running"
    duration_ms: int = 0
    id: int | None = None
    jid: str | None = None
    parent_run_id: int | None = None

    @property
    def failed(self) -> int:
        return sum(state.result is False for state in self.states)

    @property
    def changed(self) -> int:
        return sum(bool(state.changes) for state in self.states)


@dataclass
class SaltEvent:
    timestamp: str
    tag: str
    category: str
    payload: dict[str, Any]
    summary: str
    jid: str | None = None
    minion_id: str | None = None
    function: str | None = None
    target: str | None = None
    kind: str | None = None
    id: int | None = None
