import json
from pathlib import Path

import pytest

from salt_tui.app import SaltTUI
from salt_tui.config import Settings
from salt_tui.models import CommandSpec, RunResult, StateResult
from salt_tui.salt.redaction import Redactor
from salt_tui.storage.database import Database


def test_redaction_before_storage_and_command_preview():
    redactor = Redactor([r"custom-secret-\w+"])
    assert redactor.text('"password": "plain"') == '"password": "[REDACTED]"'
    assert redactor.argv(['pillar={"password":"plain","role":"web"}'])[0].find("plain") == -1
    assert redactor.text("custom-secret-abc") == "[REDACTED]"


@pytest.mark.asyncio
async def test_saved_targets_and_test_to_real_parent(tmp_path: Path):
    db = Database(tmp_path / "history.db")
    await db.migrate()
    await db.seed_targets({"production_web": {"expression": "G@env:prod and G@role:web", "type": "compound"}})
    assert (await db.list_targets())[0]["name"] == "production_web"
    await db.rename_target("production_web", "web_prod")
    assert (await db.list_targets())[0]["name"] == "web_prod"
    await db.save_target("staging", "G@env:staging", "grain")
    await db.delete_target("web_prod")
    assert [row["name"] for row in await db.list_targets()] == ["staging"]
    test = RunResult("salt web01 state.apply test=True", ["salt", "web01", "state.apply", "test=True"],
                     "web01", "glob", "state.apply")
    test.parsed = {}
    test_id = await db.save_run(test, test_mode=True)
    real = RunResult("salt web01 state.apply", ["salt", "web01", "state.apply"], "web01", "glob", "state.apply")
    real.parsed = {}
    real.parent_run_id = test_id
    real_id = await db.save_run(real)
    assert (await db.run(real_id))["parent_run_id"] == test_id


@pytest.mark.asyncio
async def test_database_redacts_raw_salt_result(tmp_path: Path):
    db = Database(tmp_path / "history.db")
    await db.migrate()
    raw = {"result": False, "comment": "password=plain", "changes": {"api_key": "raw-key"}}
    run = RunResult("salt web01 state.apply pillar={\"password\":\"plain\"}",
                    ["salt", "web01", "state.apply", 'pillar={"password":"plain"}'], "web01", "glob", "state.apply")
    run.parsed = {"web01": {"cmd_|-example_|-example_|-run": raw}}
    run.stdout = json.dumps(run.parsed)
    run.states = [StateResult("web01", "example", "cmd", "run", "example", "example", False,
                              raw["changes"], raw["comment"], 10, None, raw)]
    run_id = await db.save_run(run)
    persisted = await db.run(run_id)
    assert "plain" not in json.dumps(persisted)
    assert "raw-key" not in json.dumps(await db.states(run_id))


@pytest.mark.asyncio
async def test_legacy_history_redaction(tmp_path: Path):
    import sqlite3
    db = Database(tmp_path / "legacy.db")
    await db.migrate()
    with sqlite3.connect(db.path) as con:
        con.execute("""INSERT INTO runs (uuid,started_at,command,argv_json,status,stdout) VALUES (?,?,?,?,?,?)""",
                    ("legacy", "2026-01-01T00:00:00+00:00", "salt password=plain", '["salt","password=plain"]',
                     "success", '{"password":"plain"}'))
    await db.redact_legacy_history(batch_size=1)
    row = (await db.runs())[0]
    assert "plain" not in json.dumps(row)


def test_confirmation_policy():
    settings = Settings(confirmation_mode="large-target", confirm_if_target_count=20)
    app = SaltTUI(settings)
    spec = CommandSpec(function="state.apply", target="web-*")
    assert app.should_confirm(spec, None)
    assert not app.should_confirm(spec, 19)
    assert app.should_confirm(spec, 20)
    settings.confirmation_mode = "production-only"
    assert not app.should_confirm(spec, 20)
    spec.saltenv = "prod"
    assert app.should_confirm(spec, 1)
