from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import shlex

from salt_tui.models import SaltEvent


@dataclass(frozen=True)
class EventFilter:
    text: str = ""
    tag: str = ""
    prefix: str = ""
    minion: str = ""
    jid: str = ""
    function: str = ""
    category: str = ""
    since: str = ""
    until: str = ""

    def matches(self, event: SaltEvent) -> bool:
        if self.tag and self.tag.lower() not in event.tag.lower(): return False
        if self.prefix and not event.tag.lower().startswith(self.prefix.lower()): return False
        if self.minion and self.minion.lower() not in (event.minion_id or "").lower(): return False
        if self.jid and self.jid != (event.jid or ""): return False
        if self.function and self.function.lower() not in (event.function or "").lower(): return False
        if self.category and self.category.lower() != event.category.lower(): return False
        if self.since and event.timestamp < self.since: return False
        if self.until and event.timestamp > self.until: return False
        if self.text:
            haystack = " ".join((event.tag, event.category, event.summary, event.minion_id or "", event.jid or "", event.function or "", str(event.payload))).lower()
            if self.text.lower() not in haystack: return False
        return True


def parse_event_filter(query: str) -> EventFilter:
    fields: dict[str, str] = {}
    free: list[str] = []
    aliases = {"type": "category", "fun": "function"}
    valid = set(EventFilter.__dataclass_fields__)
    for token in shlex.split(query):
        if ":" in token:
            key, value = token.split(":", 1)
            key = aliases.get(key.lower(), key.lower())
            if key in valid and key != "text":
                if key in {"since", "until"}:
                    value = _normalize_time(value)
                fields[key] = value
                continue
        free.append(token)
    fields["text"] = " ".join(free)
    return EventFilter(**fields)


def _normalize_time(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Invalid time: {value}; use ISO 8601") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()
