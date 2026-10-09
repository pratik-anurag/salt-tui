from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from typing import Any, Callable

from salt_tui.config import Settings
from salt_tui.models import CommandSpec, RunResult, now
from salt_tui.salt.client import LogCallback, SubprocessSaltClient
from salt_tui.salt.commands import build_argv, display_argv
from salt_tui.salt.parser import extract_states, summarize_status
from salt_tui.salt.redaction import Redactor


def _value(text: str) -> Any:
    if text in {"True", "true"}: return True
    if text in {"False", "false"}: return False
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


class SaltPythonBackend:
    """Optional LocalClient adapter for master execution; unsupported CLI forms use CLI."""
    def __init__(self, settings: Settings, local_client_factory: Callable[[], Any] | None = None):
        self.settings = settings
        self.redactor = Redactor(settings.redaction_patterns)
        self.fallback = SubprocessSaltClient(settings)
        self._factory = local_client_factory

    def _client(self) -> Any:
        if self._factory:
            return self._factory()
        import salt.client  # type: ignore[import-not-found]
        return salt.client.LocalClient()

    async def run(self, spec: CommandSpec, log: LogCallback | None = None) -> RunResult:
        if spec.executable != "salt" or spec.options or spec.batch:
            return await self.fallback.run(spec, log)
        argv = build_argv(spec, self.settings)
        redactor = self.redactor.with_values(spec.secret_values)
        safe = redactor.argv(argv)
        run = RunResult(display_argv(safe), safe, spec.target, spec.target_type, spec.function)
        run.execution_context = spec.execution_context
        run.action_kind = spec.action_kind
        started = datetime.now(timezone.utc)
        positional: list[str] = []
        kwargs: dict[str, Any] = {}
        for arg in spec.arguments:
            if "=" in arg:
                key, value = arg.split("=", 1)
                kwargs[key] = _value(value)
            else:
                positional.append(arg)
        if spec.test: kwargs["test"] = True
        if spec.saltenv: kwargs["saltenv"] = spec.saltenv
        if spec.pillarenv: kwargs["pillarenv"] = spec.pillarenv
        try:
            client = self._client()
            if spec.async_run:
                jid = await asyncio.to_thread(client.cmd_async, spec.target, spec.function,
                                               arg=positional, tgt_type=spec.target_type, kwarg=kwargs,
                                               start_event=True)
                run.jid = str(jid) if jid else None
                run.parsed = {"jid": run.jid} if run.jid else {}
                run.status = "running" if run.jid else "failed"
            else:
                result = await asyncio.to_thread(client.cmd, spec.target, spec.function,
                                                 arg=positional, tgt_type=spec.target_type, kwarg=kwargs,
                                                 timeout=spec.timeout)
                run.parsed = redactor.value(result)
                run.states = extract_states(run.parsed)
                run.status = summarize_status(0, run.states, run.parsed)
            run.exit_code = 0 if run.status != "failed" else 1
            run.stdout = json.dumps(run.parsed, ensure_ascii=False, default=str)
            if log:
                await log("INFO", run.stdout)
        except Exception as exc:
            run.stderr = redactor.text(f"Salt Python API error: {type(exc).__name__}: {exc}")
            run.exit_code = 1
            run.status = "failed"
            if log:
                await log("ERROR", run.stderr)
        finally:
            run.finished_at = now()
            run.duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        return run

    async def cancel(self) -> bool:
        """A LocalClient call cannot safely cancel a published remote Salt job."""
        return False
