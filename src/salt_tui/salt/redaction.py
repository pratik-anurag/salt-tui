"""Redact common sensitive keys before events enter the UI or database."""
from __future__ import annotations

import re
import json
from typing import Any

SENSITIVE = re.compile(r"password|passwd|secret|token|api[_-]?key|private[_-]?key|access[_-]?key|credential", re.IGNORECASE)


def redact(value: Any, patterns: tuple[re.Pattern, ...] = ()) -> Any:
    if isinstance(value, dict):
        return {str(k): "[REDACTED]" if SENSITIVE.search(str(k)) or any(p.search(str(k)) for p in patterns)
                else redact(v, patterns) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v, patterns) for v in value]
    if isinstance(value, str):
        return redact_text(value, patterns)
    return value


KEY_VALUE = re.compile(r"(?i)([\"']?(?:password|passwd|secret|token|api[_-]?key|private[_-]?key|access[_-]?key|credential)[\"']?\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s,}]+)")


def redact_text(value: str, patterns: tuple[re.Pattern, ...] = ()) -> str:
    def replace(match: re.Match) -> str:
        original = match.group(2)
        marker = f"{original[0]}[REDACTED]{original[0]}" if original.startswith(("'", '"')) else "[REDACTED]"
        return match.group(1) + marker
    result = KEY_VALUE.sub(replace, value)
    for pattern in patterns:
        result = pattern.sub("[REDACTED]", result)
    return result


class Redactor:
    def __init__(self, regexes: list[str] | None = None):
        self.patterns = tuple(re.compile(rule, re.IGNORECASE) for rule in (regexes or []))

    def value(self, value: Any) -> Any:
        return redact(value, self.patterns)

    def text(self, value: str) -> str:
        return redact_text(value, self.patterns)

    def argv(self, argv: list[str]) -> list[str]:
        output = []
        for arg in argv:
            if "=" in arg:
                key, value = arg.split("=", 1)
                if SENSITIVE.search(key) or any(p.search(key) for p in self.patterns):
                    output.append(f"{key}=[REDACTED]")
                    continue
                if key == "pillar":
                    try:
                        output.append(f"pillar={json.dumps(self.value(json.loads(value)), ensure_ascii=False)}")
                        continue
                    except json.JSONDecodeError:
                        pass
            output.append(self.text(arg))
        return output
