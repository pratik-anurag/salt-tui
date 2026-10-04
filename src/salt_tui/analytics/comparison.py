from __future__ import annotations

import json
from typing import Any


def detailed_compare(previous: list[dict[str, Any]], current: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def identity(row: dict[str, Any]) -> tuple:
        return tuple(row.get(key) for key in ("minion_id", "sls", "state_id", "state_module", "state_function", "name"))
    before = {identity(row): row for row in previous}
    after = {identity(row): row for row in current}
    output = []
    for key in sorted(before.keys() | after.keys()):
        old, new = before.get(key), after.get(key)
        categories: list[str] = []
        if old is None:
            categories.append("NEW")
        elif new is None:
            categories.append("REMOVED")
        else:
            if old.get("result") != new.get("result"):
                categories.append("RECOVERED" if new.get("result") == 1 else "REGRESSED" if new.get("result") == 0 else "CHANGED")
            if old.get("changes_json") != new.get("changes_json") or old.get("comment") != new.get("comment"):
                categories.append("CHANGED")
            old_ms, new_ms = old.get("duration_ms"), new.get("duration_ms")
            if old_ms is not None and new_ms is not None and abs(new_ms - old_ms) >= 100:
                if new_ms >= old_ms * 1.5:
                    categories.append("SLOWER")
                elif old_ms >= new_ms * 1.5:
                    categories.append("FASTER")
        if not categories:
            continue
        output.append({"identity": key, "categories": tuple(dict.fromkeys(categories)),
                       "previous": old, "current": new})
    return output
