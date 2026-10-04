from __future__ import annotations

import re

TIMESTAMP = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b")
PID = re.compile(r"\b(pid|process)\s*[=: ]\s*\d+\b", re.IGNORECASE)
TEMP = re.compile(r"/tmp/[^\s,'\"]+")
JID = re.compile(r"\b\d{18,20}\b")
SPACE = re.compile(r"\s+")


def failure_signature(error: str) -> str:
    """Normalize volatile identifiers without merging arbitrary numeric error codes."""
    value = TIMESTAMP.sub("<timestamp>", error)
    value = PID.sub(lambda m: f"{m.group(1).lower()} <pid>", value)
    value = TEMP.sub("/tmp/<path>", value)
    value = JID.sub("<jid>", value)
    return SPACE.sub(" ", value).strip().lower()[:500]
