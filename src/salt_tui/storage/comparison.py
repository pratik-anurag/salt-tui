from __future__ import annotations

import json
from typing import Any


def compare_states(previous: list[dict[str, Any]], current: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compare normalized state rows by minion, SLS, state ID, module, and function."""
    def key(row: dict[str, Any]) -> tuple:
        return tuple(row.get(k) for k in ("minion_id", "sls", "state_id", "state_module", "state_function"))
    before = {key(row): row for row in previous}
    after = {key(row): row for row in current}
    changes = []
    for identity in sorted(before.keys() | after.keys()):
        old, new = before.get(identity), after.get(identity)
        if old is None:
            kind = "added"
        elif new is None:
            kind = "removed"
        elif old.get("result") != new.get("result"):
            kind = "recovered" if new.get("result") == 1 else "newly failing" if new.get("result") == 0 else "result changed"
        elif old.get("changes_json") != new.get("changes_json"):
            kind = "changes differ"
        elif old.get("comment") != new.get("comment"):
            kind = "comment differs"
        else:
            kind = "same"
        duration_delta = None
        if old and new and old.get("duration_ms") is not None and new.get("duration_ms") is not None:
            duration_delta = new["duration_ms"] - old["duration_ms"]
        changes.append({"identity": identity, "kind": kind, "previous": old, "current": new,
                        "duration_delta_ms": duration_delta})
    return changes
