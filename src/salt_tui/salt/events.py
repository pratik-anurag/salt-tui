from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Callable
import importlib.util
import json
import re
import shutil
from datetime import datetime, timezone
from typing import Any, Protocol

from salt_tui.config import Settings
from salt_tui.models import SaltEvent, now
from salt_tui.salt.redaction import Redactor, redact, redact_text


JOB_TAG = re.compile(r"^salt/job/(?P<jid>\d+)/(?:((?P<kind>new))|(?P<action>start|ret|prog)/(?P<minion>[^/]+))")
RUN_TAG = re.compile(r"^salt/run/(?P<jid>\d+)/(?P<kind>new|ret)")
RUNNER_LINE = re.compile(r"^(?P<tag>\S+)\s+(?P<payload>\{.*\})\s*$")
STATUS_LIMIT = 180


def event_error_summary(error: Exception | str) -> str:
    """Keep subprocess diagnostics useful without rendering tracebacks in the UI."""
    lines = [line.strip() for line in str(error).splitlines() if line.strip()]
    detail = redact_text(lines[-1]) if lines else "no diagnostic output"
    return detail[:STATUS_LIMIT - 1] + "…" if len(detail) > STATUS_LIMIT else detail


async def _stderr_tail(stream: asyncio.StreamReader, limit: int = 8192) -> str:
    """Drain stderr concurrently with stdout, retaining only its diagnostic tail."""
    tail = bytearray()
    while chunk := await stream.read(4096):
        tail.extend(chunk)
        if len(tail) > limit:
            del tail[:-limit]
    return tail.decode(errors="replace")


def normalize_event(raw: dict[str, Any], redactor: Redactor | None = None) -> SaltEvent:
    tag = str(raw.get("tag", ""))
    payload = redactor.value(raw.get("data")) if redactor else redact(raw.get("data"))
    if not isinstance(payload, dict):
        payload = {"value": payload}
    job = JOB_TAG.match(tag)
    runner = RUN_TAG.match(tag)
    if job:
        category = "job"
        kind = job.group("kind") or job.group("action")
        jid = str(payload.get("jid") or job.group("jid"))
        minion = str(payload.get("id") or job.group("minion") or "") or None
    elif runner:
        category = "runner"
        kind = runner.group("kind")
        jid = str(payload.get("jid") or runner.group("jid"))
        minion = None
    else:
        category = "authentication" if tag.startswith("salt/auth") else "key" if tag.startswith("salt/key") else \
                   "presence" if tag.startswith("salt/presence") else "reactor" if tag.startswith("salt/reactor") else \
                   "state" if tag.startswith("salt/state") else "error" if "error" in tag.lower() else "custom"
        kind, jid, minion = None, str(payload["jid"]) if payload.get("jid") else None, str(payload.get("id")) if payload.get("id") else None
    timestamp = _timestamp(payload.get("_stamp"))
    function = payload.get("fun")
    target = payload.get("tgt")
    summary = _summary(category, kind, payload)
    return SaltEvent(timestamp, tag, category, payload, summary, jid, minion,
                     str(function) if function else None, str(target) if target else None, kind)


def _timestamp(value: Any) -> str:
    if not value:
        return now()
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except ValueError:
        return now()


def _summary(category: str, kind: str | None, payload: dict[str, Any]) -> str:
    if category == "job":
        if kind == "new":
            return f"{payload.get('fun', '?')} → {payload.get('tgt', '?')} ({len(payload.get('minions') or [])} expected)"
        if kind == "ret":
            return f"return code {payload.get('retcode', '?')}"
        if kind == "prog":
            return "state progress"
        return f"{kind or 'job'} {payload.get('fun', '')}"
    if category == "runner":
        return f"runner {kind}: {payload.get('fun', '?')}"
    return str(payload.get("message") or payload.get("act") or payload.get("id") or "event")[:180]


def parse_runner_line(line: str) -> SaltEvent | None:
    match = RUNNER_LINE.match(line.strip())
    if not match:
        return None
    try:
        return normalize_event({"tag": match.group("tag"), "data": json.loads(match.group("payload"))})
    except json.JSONDecodeError:
        return None


class EventSource(Protocol):
    async def events(self) -> AsyncIterator[SaltEvent]: ...


class PythonSaltEventSource:
    """Subscribe through Salt's documented master event API when importable."""
    def __init__(self, config_path: str, redactor: Redactor | None = None):
        self.config_path = config_path
        self.redactor = redactor

    async def events(self) -> AsyncIterator[SaltEvent]:
        import salt.config  # type: ignore[import-not-found]
        import salt.utils.event  # type: ignore[import-not-found]
        opts = await asyncio.to_thread(salt.config.client_config, self.config_path)
        bus = await asyncio.to_thread(salt.utils.event.get_event, "master", sock_dir=opts["sock_dir"], opts=opts)
        try:
            while True:
                raw = await asyncio.to_thread(bus.get_event, wait=1, full=True)
                if isinstance(raw, dict) and "tag" in raw:
                    yield normalize_event(raw, self.redactor)
        finally:
            close = getattr(bus, "destroy", None) or getattr(bus, "close", None)
            if close:
                await asyncio.to_thread(close)


class RunnerEventSource:
    """Fallback to documented `salt-run state.event` line output."""
    def __init__(self, executable: str, redactor: Redactor | None = None):
        self.executable = executable
        self.redactor = redactor

    async def events(self) -> AsyncIterator[SaltEvent]:
        proc = await asyncio.create_subprocess_exec(self.executable, "state.event", "pretty=False", "--no-color",
                                                     stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stderr_task = asyncio.create_task(_stderr_tail(proc.stderr))
        try:
            while line := await proc.stdout.readline():
                event = parse_runner_line(line.decode(errors="replace"))
                if event:
                    if self.redactor:
                        event.payload = self.redactor.value(event.payload)
                    yield event
            await proc.wait()
            stderr = await stderr_task
            reason = event_error_summary(self.redactor.text(stderr) if self.redactor else stderr)
            raise RuntimeError(f"Salt event runner exited ({proc.returncode}): {reason}")
        finally:
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), 2)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
            if not stderr_task.done():
                stderr_task.cancel()
            await asyncio.gather(stderr_task, return_exceptions=True)


def choose_event_source(settings: Settings) -> EventSource | None:
    # An event bus is a master feature. A salt-run binary alone does not imply
    # that this host has a usable master configuration (notably on laptops).
    try:
        with settings.master_config.open("rb") as config:
            config.read(1)
    except (OSError, ValueError):
        return None
    redactor = Redactor(settings.redaction_patterns)
    if importlib.util.find_spec("salt") is not None:
        return PythonSaltEventSource(str(settings.master_config), redactor)
    if shutil.which(settings.salt_run):
        return RunnerEventSource(settings.salt_run, redactor)
    return None


class EventMonitor:
    def __init__(self, source: EventSource | None, database: Any, capacity: int = 2000,
                 queue_size: int = 5000, retention: int = 100000):
        self.source = source
        self.database = database
        self.recent: deque[SaltEvent] = deque(maxlen=capacity)
        self.queue: asyncio.Queue[SaltEvent] = asyncio.Queue(maxsize=queue_size)
        self.subscribers: set[Callable[[SaltEvent], None]] = set()
        self.dropped = 0
        self.retention = retention
        self.status = "unavailable" if source is None else "connecting"
        self._reader: asyncio.Task | None = None
        self._writer: asyncio.Task | None = None

    def start(self) -> None:
        if self.source is None or self._reader:
            return
        self._reader = asyncio.create_task(self._read())
        self._writer = asyncio.create_task(self._write())

    async def stop(self) -> None:
        if self._reader:
            self._reader.cancel()
            await asyncio.gather(self._reader, return_exceptions=True)
        if self._writer:
            try:
                await asyncio.wait_for(self.queue.join(), 2)
            except asyncio.TimeoutError:
                pass
            self._writer.cancel()
        await asyncio.gather(*(task for task in (self._reader, self._writer) if task), return_exceptions=True)
        self._reader = self._writer = None

    async def _read(self) -> None:
        delay = 1
        while True:
            try:
                self.status = "listening"
                async for event in self.source.events():  # type: ignore[union-attr]
                    self.status = "connected"
                    delay = 1
                    self.publish(event)
                self.status = "event source ended; reconnecting"
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.status = f"event source error: {event_error_summary(exc)}; retrying"
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

    def publish(self, event: SaltEvent) -> None:
        self.recent.append(event)
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            self.dropped += 1
        for callback in tuple(self.subscribers):
            try:
                callback(event)
            except Exception as exc:
                self.status = f"event display error: {event_error_summary(exc)}"

    async def _write(self) -> None:
        saved_since_prune = 0
        while True:
            first = await self.queue.get()
            batch = [first]
            while len(batch) < 100:
                try:
                    batch.append(self.queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            while True:
                try:
                    await self.database.save_events(batch)
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.status = f"event persistence error: {event_error_summary(exc)}; retrying"
                    await asyncio.sleep(2)
            saved_since_prune += len(batch)
            if saved_since_prune >= 1000:
                try:
                    await self.database.prune_events(self.retention)
                except Exception as exc:
                    self.status = f"event retention error: {event_error_summary(exc)}"
                saved_since_prune = 0
            for _ in batch:
                self.queue.task_done()
