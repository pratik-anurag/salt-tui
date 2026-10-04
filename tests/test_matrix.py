from pathlib import Path

import pytest

from salt_tui.app import SaltTUI
from salt_tui.config import Settings
from salt_tui.models import RunResult, StateResult
from salt_tui.storage.database import Database


def state(minion: str, state_id: str, result: bool, changes: dict) -> StateResult:
    return StateResult(minion, state_id, "file", "managed", state_id, "web", result,
                       changes, "", 1200 if state_id == "slow" else 10, None, {})


@pytest.mark.asyncio
async def test_matrix_filters_and_screen(tmp_path: Path):
    settings = Settings(database=tmp_path / "history.db")
    db = Database(settings.database)
    await db.migrate()
    run = RunResult("salt '*' state.highstate", ["salt", "*", "state.highstate"], "*", "glob", "state.highstate")
    run.status = "partial"
    run.parsed = {"web01": {}, "web02": {}}
    run.states = [state("web01", "config", True, {"new": "x"}),
                  state("web02", "config", False, {}),
                  state("web01", "slow", True, {}),
                  state("web02", "slow", True, {})]
    run_id = await db.save_run(run)
    assert await db.matrix_minions(run_id) == ["web01", "web02"]
    assert [r["state_id"] for r in await db.matrix_summary(run_id, filter="failed")] == ["config"]
    assert [r["state_id"] for r in await db.matrix_summary(run_id, filter="divergent")] == ["config"]
    assert [r["state_id"] for r in await db.matrix_summary(run_id, filter="slow")] == ["slow"]
    assert len(await db.matrix_cells(run_id, ["web01"], ["config"])) == 1

    app = SaltTUI(settings, initial="matrix")
    app.current_run_id = run_id
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        table = app.screen.query_one("#matrix_table")
        assert table.row_count == 2
        assert len(table.columns) == 6
        assert "2 of 2" in str(app.screen.query_one("#matrix_status").render())
