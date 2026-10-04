import json
from pathlib import Path
import zipfile

import pytest

from salt_tui.config import Settings
from salt_tui.diagnostics import create_diagnostics_bundle
from salt_tui.export import export_run
from salt_tui.models import RunResult
from salt_tui.storage.database import Database


@pytest.mark.asyncio
async def test_redacted_exports_and_diagnostics(tmp_path: Path):
    settings = Settings(database=tmp_path / "history.db", targets={"prod": {"expression": "password=plain", "type": "glob"}})
    db = Database(settings.database)
    await db.migrate()
    run = RunResult("salt web01 test.ping password=plain", ["salt", "web01", "test.ping", "password=plain"],
                    "web01", "glob", "test.ping")
    run.parsed = {"web01": {"password": "plain", "ok": True}}
    run.stdout = json.dumps(run.parsed)
    run_id = await db.save_run(run)
    for format in ("json", "yaml", "csv", "txt"):
        path = await export_run(db, run_id, tmp_path / f"run.{format}", format)
        assert path.exists()
        assert "plain" not in path.read_text()
    path = await create_diagnostics_bundle(settings, tmp_path / "diagnostics.zip", run_id)
    with zipfile.ZipFile(path) as archive:
        assert {"diagnostics.json", "app-log-tail.txt", "selected-run.json"}.issubset(archive.namelist())
        assert "plain" not in archive.read("selected-run.json").decode()
        assert "plain" not in archive.read("diagnostics.json").decode()
