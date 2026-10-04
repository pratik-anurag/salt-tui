from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
import shlex


@dataclass(frozen=True)
class SearchTerm:
    field: str
    value: str


@dataclass(frozen=True)
class HistoryQuery:
    terms: tuple[SearchTerm, ...]
    text: tuple[str, ...]


def parse_history_query(query: str, now: datetime | None = None) -> HistoryQuery:
    now = now or datetime.now(timezone.utc)
    fields = {"minion", "sls", "state", "result", "since", "command", "target", "jid"}
    terms: list[SearchTerm] = []
    free: list[str] = []
    for token in shlex.split(query):
        if ":" not in token:
            free.append(token)
            continue
        field, value = token.split(":", 1)
        if field not in fields:
            free.append(token)
            continue
        if not value:
            raise ValueError(f"Missing value for {field}:")
        if field == "since":
            relative = re.fullmatch(r"(\d+)([dhm])", value)
            if relative:
                number = int(relative.group(1))
                delta = {"d": timedelta(days=number), "h": timedelta(hours=number), "m": timedelta(minutes=number)}[relative.group(2)]
                value = (now - delta).isoformat()
            else:
                try:
                    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError as exc:
                    raise ValueError("since: expects 7d, 12h, 30m, or ISO 8601") from exc
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                value = parsed.astimezone(timezone.utc).isoformat()
        if field == "result" and value not in {"failed", "success", "partial", "timed_out", "running"}:
            raise ValueError("result: expects failed, success, partial, timed_out, or running")
        terms.append(SearchTerm(field, value))
    return HistoryQuery(tuple(terms), tuple(free))
