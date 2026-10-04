import json
from pathlib import Path

import pytest

from salt_tui.config import Settings
from salt_tui.models import CommandSpec, RunResult
from salt_tui.salt.commands import build_argv, parse_line, is_mutating
from salt_tui.salt.parser import parse_output, extract_states, summarize_status
from salt_tui.sls.dependencies import dependencies
from salt_tui.storage.database import Database

FIXTURES = Path(__file__).parent / "fixtures"


def test_command_construction():
    spec = CommandSpec(function="state.apply", target="web-*", arguments=["nginx"], target_type="grain", test=True, saltenv="prod")
    assert build_argv(spec, Settings()) == ["salt", "-G", "web-*", "state.apply", "nginx", "test=True", "saltenv=prod", "--out=json", "--static"]
    assert parse_line("salt '*' grains.items", Settings()).target == "*"
    assert is_mutating(spec)


def test_state_parsing_and_failure():
    raw = (FIXTURES / "failed_highstate.json").read_text()
    parsed = parse_output(raw)
    states = extract_states(parsed)
    assert len(states) == 2
    assert states[0].sls == "nginx.init"
    assert states[1].result is False
    assert summarize_status(0, states, parsed) == "failed"
    assert parse_output("not json") == "not json"


def test_compiled_dependencies():
    low = json.loads((FIXTURES / "show_low_sls.json").read_text())
    edges = dependencies(low)
    assert [(e.source, e.destination, e.kind) for e in edges] == [
        ("nginx-package", "nginx-config", "require"),
        ("nginx-config", "nginx-service", "watch"),
    ]
    assert all(e.resolved for e in edges)


@pytest.mark.asyncio
async def test_database_roundtrip(tmp_path):
    db = Database(tmp_path / "history.db")
    await db.migrate()
    parsed = json.loads((FIXTURES / "failed_highstate.json").read_text())
    run = RunResult("salt '*' state.highstate --out=json", ["salt", "*", "state.highstate", "--out=json"], "*", "glob", "state.highstate")
    run.parsed = parsed
    run.states = extract_states(parsed)
    run.exit_code = 2
    run.status = "failed"
    run_id = await db.save_run(run, log_lines=[("INFO", "run started")])
    assert len(await db.states(run_id)) == 2
    assert (await db.failures())[0]["state_id"] == "nginx-service"
    assert (await db.runs())[0]["failed"] == 1
    assert (await db.logs())[0]["message"] == "run started"


@pytest.mark.asyncio
async def test_mocked_subprocess_integration(tmp_path, monkeypatch):
    from salt_tui.salt import client
    class FakeStream:
        def __init__(self, lines): self.lines = list(lines)
        async def readline(self): return self.lines.pop(0) if self.lines else b""
    class FakeProcess:
        returncode = 0
        stdout = FakeStream([(FIXTURES / "failed_highstate.json").read_bytes() + b"\n"])
        stderr = FakeStream([])
        async def wait(self): return 0
    async def fake_create(*args, **kwargs): return FakeProcess()
    monkeypatch.setattr(client.asyncio, "create_subprocess_exec", fake_create)
    settings = Settings(database=tmp_path / "history.db")
    result = await client.SubprocessSaltClient(settings).run(CommandSpec(function="state.highstate"))
    assert result.failed == 1
    db = Database(settings.database); await db.migrate()
    await db.save_run(result)
    assert len(await db.failures()) == 1
