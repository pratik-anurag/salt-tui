from __future__ import annotations

import json
from typing import Any
from salt_tui.models import StateResult


def parse_output(stdout: str) -> Any:
    """Keep unparseable CLI output as text; never discard original output."""
    try:
        return json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return stdout


def _state_key(key: str, item: dict[str, Any]) -> bool:
    return isinstance(item, dict) and "result" in item and ("__id__" in item or "_|-" in key)


def extract_states(parsed: Any) -> list[StateResult]:
    if not isinstance(parsed, dict):
        return []
    states: list[StateResult] = []
    for minion, result in parsed.items():
        if not isinstance(result, dict):
            continue
        # salt-call --out=json can wrap its result under "local".
        for key, item in result.items():
            if not _state_key(str(key), item):
                continue
            parts = str(key).split("_|-")
            module = item.get("state") or (parts[0] if len(parts) == 4 else "")
            state_id = item.get("__id__") or (parts[1] if len(parts) == 4 else str(key))
            name = item.get("name") or (parts[2] if len(parts) == 4 else state_id)
            function = item.get("fun") or (parts[3] if len(parts) == 4 else "")
            states.append(StateResult(str(minion), str(state_id), str(module), str(function),
                str(name), str(item.get("__sls__", "")), item.get("result"), item.get("changes", {}),
                str(item.get("comment", "")), _float(item.get("duration")), item.get("start_time"), item))
    return states


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (ValueError, TypeError):
        return None


def summarize_status(exit_code: int | None, states: list[StateResult], parsed: Any) -> str:
    if exit_code is None:
        return "cancelled"
    if exit_code != 0 or any(s.result is False for s in states):
        return "failed"
    if isinstance(parsed, dict) and any(v is False for v in parsed.values()):
        return "failed"
    return "success"
