from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
import re
import shutil
from typing import Any, Awaitable, Callable, Protocol

from salt_tui.config import Settings
from salt_tui.models import CommandSpec, RunResult, now
from salt_tui.salt.commands import build_argv, display_argv
from salt_tui.salt.parser import parse_output, extract_states, summarize_status
from salt_tui.salt.redaction import Redactor

JID_TEXT = re.compile(r"(?:job ID|jid)\s*[:=]\s*(\d{12,})", re.IGNORECASE)


def extract_jid(value: object, stdout: str) -> str | None:
    if isinstance(value, dict):
        jid = value.get("jid")
        if jid is not None and str(jid).isdigit():
            return str(jid)
        if len(value) == 1:
            return extract_jid(next(iter(value.values())), stdout)
    match = JID_TEXT.search(stdout)
    return match.group(1) if match else None

LogCallback = Callable[[str, str], Awaitable[None]]


class SaltClient(Protocol):
    async def run(self, spec: CommandSpec, log: LogCallback | None = None) -> RunResult: ...
    async def cancel(self) -> bool: ...


@dataclass
class Capabilities:
    executables: dict[str, bool]
    version: str = "unknown"

    @property
    def master_cli(self) -> bool:
        return self.executables.get("salt", False)


async def detect_capabilities(settings: Settings) -> Capabilities:
    names = {name: getattr(settings, name.replace("-", "_")) for name in ("salt", "salt-call", "salt-run", "salt-key", "salt-cp")}
    available = {name: shutil.which(path) is not None for name, path in names.items()}
    version = "unknown"
    executable = next((names[n] for n in ("salt", "salt-call") if available[n]), None)
    if executable:
        try:
            proc = await asyncio.create_subprocess_exec(executable, "--version", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, _ = await asyncio.wait_for(proc.communicate(), 5)
            version = out.decode(errors="replace").strip()
        except (OSError, asyncio.TimeoutError):
            pass
    return Capabilities(available, version)


class SubprocessSaltClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.redactor = Redactor(settings.redaction_patterns)
        self.process: asyncio.subprocess.Process | None = None
        self.parsers: list[Callable[[str, list[str]], Any | None]] = []

    async def run(self, spec: CommandSpec, log: LogCallback | None = None) -> RunResult:
        argv = build_argv(spec, self.settings)
        safe_argv = self.redactor.argv(argv)
        run = RunResult(display_argv(safe_argv), safe_argv, spec.target, spec.target_type, spec.function)
        started = datetime.now(timezone.utc)
        chunks: list[bytes] = []
        errors: list[bytes] = []
        async def consume(stream: asyncio.StreamReader, sink: list[bytes], level: str) -> None:
            while chunk := await stream.readline():
                sink.append(chunk)
                if log:
                    await log(level, self.redactor.text(chunk.decode(errors="replace").rstrip()))
        try:
            self.process = await asyncio.create_subprocess_exec(*argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            await asyncio.wait_for(asyncio.gather(consume(self.process.stdout, chunks, "INFO"), consume(self.process.stderr, errors, "ERROR"), self.process.wait()), spec.timeout or None)
            run.exit_code = self.process.returncode
        except (asyncio.TimeoutError, asyncio.CancelledError):
            if self.process and self.process.returncode is None:
                self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), 2)
                except asyncio.TimeoutError:
                    self.process.kill()
                    await self.process.wait()
            errors.append(b"Command timed out or was cancelled\n")
        except OSError as exc:
            errors.append(str(exc).encode())
            run.exit_code = 127
        finally:
            self.process = None
            run.finished_at = now()
            run.duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
            raw_stdout = b"".join(chunks).decode(errors="replace")
            parsed = parse_output(raw_stdout)
            if isinstance(parsed, str):
                for parser in self.parsers:
                    try:
                        candidate = parser(raw_stdout, argv)
                        if candidate is not None:
                            parsed = candidate
                            break
                    except Exception as exc:
                        logging.getLogger("salt_tui").error("Plugin parser failed (%s)", type(exc).__name__)
                        continue
            run.parsed = self.redactor.value(parsed)
            run.stdout = json.dumps(run.parsed, ensure_ascii=False, default=str) if not isinstance(run.parsed, str) else run.parsed
            run.stderr = self.redactor.text(b"".join(errors).decode(errors="replace"))
            run.states = extract_states(run.parsed)
            run.jid = extract_jid(run.parsed, run.stdout)
            if spec.async_run:
                run.status = "running" if run.exit_code == 0 and run.jid else "failed"
                if run.exit_code == 0 and not run.jid:
                    run.stderr += "\nSalt returned without a recognizable JID; live tracking was not started.\n"
            else:
                run.status = summarize_status(run.exit_code, run.states, run.parsed)
        return run

    async def cancel(self) -> bool:
        if self.process and self.process.returncode is None:
            self.process.terminate()
            return True
        return False
