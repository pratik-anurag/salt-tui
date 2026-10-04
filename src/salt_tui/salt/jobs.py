from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class JobSummary:
    jid: str
    function: str
    target: str
    target_type: str
    returned: tuple[str, ...]
    running: tuple[str, ...]
    raw: dict[str, Any]

    @property
    def expected_count(self) -> int:
        return len(set(self.returned) | set(self.running))


def parse_jobs(value: Any) -> list[JobSummary]:
    if isinstance(value, dict) and "local" in value and isinstance(value["local"], dict):
        value = value["local"]
    if not isinstance(value, dict):
        return []
    jobs: list[JobSummary] = []
    for jid, raw in value.items():
        if not str(jid).isdigit() or not isinstance(raw, dict):
            continue
        returned = raw.get("Returned") or raw.get("returned") or []
        running = raw.get("Running") or raw.get("running") or []
        if isinstance(returned, dict): returned = list(returned)
        if isinstance(running, dict): running = list(running)
        if not isinstance(returned, list): returned = []
        if not isinstance(running, list): running = []
        jobs.append(JobSummary(str(jid), str(raw.get("Function") or raw.get("fun") or ""),
                               str(raw.get("Target") or raw.get("tgt") or ""),
                               str(raw.get("Target-type") or raw.get("tgt_type") or "glob"),
                               tuple(map(str, returned)), tuple(map(str, running)), raw))
    return jobs
