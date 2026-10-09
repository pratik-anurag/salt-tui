import json
from pathlib import Path

import pytest
from textual.widgets import Button, DataTable, Input, Static

from salt_tui.app import SaltTUI
from salt_tui.config import Settings
from salt_tui.models import RunResult, StateResult
from salt_tui.salt.parser import extract_states
from salt_tui.sls.explorer import available_states, local_tree_rows
from salt_tui.ui.screens import format_state_changes


def recorded_state(minion: str, sls: str, *, failed: bool = False, changed: bool = False) -> StateResult:
    return StateResult(minion, sls, "test", "nop", sls, sls, not failed,
                       {"updated": True} if changed else {}, "", 20.0, None, {})


@pytest.mark.asyncio
async def test_sls_history_uses_exact_name_and_counts_only_selected_sls(tmp_path: Path):
    from salt_tui.storage.database import Database

    db = Database(tmp_path / "history.db")
    await db.migrate()
    for index in range(3):
        run = RunResult("salt '*' state.highstate", ["salt", "*", "state.highstate"],
                        "*", "glob", "state.highstate", status="failed" if index == 1 else "success",
                        duration_ms=1500 + index)
        run.states = [recorded_state("web-01", "web", failed=index == 1, changed=index == 2),
                      recorded_state("web-02", "web", changed=index == 2),
                      recorded_state("web-01", "web.extra", failed=True)]
        await db.save_run(run, saltenv="base")
    rows = await db.sls_history("web", limit=2)
    assert len(rows) == 2
    assert [row["id"] for row in rows] == [3, 2]
    assert [(row["failed"], row["changed"], row["minions"]) for row in rows] == [(0, 2, 2), (1, 0, 2)]
    assert await db.sls_history("missing") == []

    failed_attempt = RunResult("salt web-01 state.apply web", ["salt", "web-01", "state.apply", "web"],
                               "web-01", "glob", "state.apply", status="failed")
    attempt_id = await db.save_run(failed_attempt, saltenv="base")
    assert (await db.sls_history("web", limit=1))[0]["id"] == attempt_id
    assert (await db.sls_history("web", limit=1))[0]["failed"] == 0

    other_attempt = RunResult("salt web-01 state.apply web.extra", ["salt", "web-01", "state.apply", "web.extra"],
                              "web-01", "glob", "state.apply", status="failed")
    await db.save_run(other_attempt, saltenv="base")
    assert (await db.sls_history("web", limit=1))[0]["id"] == attempt_id


@pytest.mark.asyncio
async def test_sls_explorer_shows_recent_run_and_opens_tracker(tmp_path: Path):
    root = tmp_path / "states"
    root.mkdir()
    (root / "web.sls").write_text("web:\n  test.nop: []\n")
    (root / "other.sls").write_text("other:\n  test.nop: []\n")
    app = SaltTUI(Settings(database=tmp_path / "history.db", file_roots={"base": [root]}), initial="sls")
    async with app.run_test(size=(140, 40)) as pilot:
        files = app.screen.query_one("#files", DataTable)
        files.move_cursor(row=2)
        await pilot.pause()
        run = RunResult("salt web-01 state.apply web", ["salt", "web-01", "state.apply", "web"],
                        "web-01", "glob", "state.apply", status="failed", duration_ms=2300)
        run.states = [recorded_state("web-01", "web", failed=True)]
        run_id = await app.db.save_run(run, saltenv="base")
        await app.screen.refresh_selected_history()
        await pilot.pause()
        table = app.screen.query_one("#sls-history", DataTable)
        assert table.row_count == 1
        assert "1/0" in str(table.get_row_at(0))
        files.move_cursor(row=1)
        await pilot.pause()
        assert table.row_count == 0
        files.move_cursor(row=2)
        await pilot.pause()
        assert table.row_count == 1
        table.focus()
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen is app.get_screen("live")
        assert app.current_run_id == run_id


def test_local_tree_and_salt_inventory(tmp_path: Path):
    root = tmp_path / "states"
    nested = root / "web" / "init.sls"
    nested.parent.mkdir(parents=True)
    nested.write_text("web:\n  test.nop: []\n")
    rows = local_tree_rows([("web/init.sls", nested)])
    assert [row[2] for row in rows] == [None, None, "web"]
    assert "web/" in rows[1][1]
    assert "init.sls" in rows[2][1]
    assert available_states({"minion-a": ["web", "top", "web"], "minion-b": ["web"]}) == {"top": 1, "web": 2}
    assert format_state_changes({"diff": "--- old\n+++ new\n"}) == "--- old\n+++ new\n"


@pytest.mark.asyncio
async def test_salt_reported_states_are_separate_from_local_files(tmp_path: Path):
    app = SaltTUI(Settings(database=tmp_path / "history.db", file_roots={"base": []}), initial="sls")

    async def execute(spec, *args, **kwargs):
        assert spec.function == "cp.list_states"
        assert (spec.executable, spec.target, spec.target_type) == ("salt", "G@roles:web", "compound")
        return RunResult("", [], "local", "glob", spec.function, parsed={"local": ["web", "top"]},
                         status="success", exit_code=0)

    app.execute = execute
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        assert app.screen.query_one("#roots_help", Button).display
        app.screen.query_one("#state_target", Input).value = "G@roles:web"
        app.screen.query_one("#state_target_type", Input).value = "compound"
        await pilot.click("#salt_mode")
        await pilot.pause()
        assert app.screen.source_mode == "salt"
        assert app.screen.query_one("#files", DataTable).row_count == 2
        assert app.current_sls in {"top", "web"}
        assert "Salt target: G@roles:web" in str(app.screen.query_one("#source-origin", Static).render())


@pytest.mark.asyncio
async def test_dry_run_review_apply_and_tracker(tmp_path: Path):
    root = tmp_path / "states"
    root.mkdir()
    (root / "web.sls").write_text("web:\n  test.nop: []\n")
    app = SaltTUI(Settings(database=tmp_path / "history.db", file_roots={"base": [root]}), initial="sls")
    calls = []

    async def execute(spec, *args, parent_run_id=None, **kwargs):
        calls.append((spec.test, parent_run_id))
        result = None if spec.test else True
        parsed = {"local": {"file_|-web_|-/tmp/web_|-managed": {
            "__id__": "web", "__sls__": "web", "name": "/tmp/web", "result": result,
            "changes": {"new": "web"}, "comment": "Would create" if spec.test else "Created"}}}
        run = RunResult("salt-call state.apply web", ["salt-call", "state.apply", "web"], "local", "glob", spec.function,
                        parsed=parsed, states=extract_states(parsed), status="success", exit_code=0)
        run.parent_run_id = parent_run_id
        run.id = await app.db.save_run(run, saltenv=spec.saltenv, test_mode=spec.test)
        return run

    app.execute = execute
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await app.screen.run_state(test=True)
        await pilot.pause()
        assert app.screen is app.get_screen("review")
        assert "1 planned changes" in str(app.screen.query_one("#review_summary", Static).render())
        test_id = app.review_run_id
        await pilot.click("#review_apply")
        await pilot.pause()
        assert type(app.screen).__name__ == "ConfirmScreen"
        assert f"Dry run #{test_id}" in str(app.screen.query_one("#confirm_text", Static).render())
        await pilot.click("#yes")
        await pilot.pause()
        assert app.screen is app.get_screen("live")
        assert calls == [(True, None), (False, test_id)]
        assert "1 complete" in str(app.screen.query_one("#run_summary", Static).render())
        await pilot.click("#tracker_results")
        await pilot.pause()
        assert app.screen is app.get_screen("states")


@pytest.mark.asyncio
async def test_tracker_exposes_failed_state_source_and_logs(tmp_path: Path):
    root = tmp_path / "states" / "nginx"
    root.mkdir(parents=True)
    (root / "service.sls").write_text("nginx-service:\n  test.nop: []\n")
    (root / "config.sls").write_text("nginx-config:\n  test.nop: []\n")
    app = SaltTUI(Settings(database=tmp_path / "history.db", file_roots={"base": [root.parent]}), initial="live")
    parsed = json.loads((Path(__file__).parent / "fixtures/failed_highstate.json").read_text())
    parsed["web-01"]["file_|-nginx-config_|-/tmp/config_|-managed"] = {
        "__id__": "nginx-config", "__sls__": "nginx.config", "name": "/tmp/config",
        "result": False, "changes": {}, "comment": "Config failed"}
    run = RunResult("salt '*' state.highstate", ["salt", "*", "state.highstate"], "*", "glob", "state.highstate",
                    parsed=parsed, states=extract_states(parsed), status="failed", exit_code=2)
    async with app.run_test(size=(140, 40)) as pilot:
        run.id = await app.db.save_run(run, log_lines=[("ERROR", "service failed")])
        app.current_run_id = run.id
        await app.screen.refresh_data()
        await pilot.pause()
        table = app.screen.query_one("#minion_progress", DataTable)
        assert table.row_count == 2
        table.move_cursor(row=0)
        await pilot.pause()
        assert "Service failed to start" in str(app.screen.query_one("#live_detail", Static).render())
        assert not app.screen.query_one("#tracker_source", Button).disabled
        await pilot.click("#tracker_logs")
        for _ in range(20):
            await pilot.pause(0.05)
            if "service failed" in str(app.screen.query_one("#live_detail", Static).render()):
                break
        assert "service failed" in str(app.screen.query_one("#live_detail", Static).render())
        await pilot.pause(1.05)
        assert "service failed" in str(app.screen.query_one("#live_detail", Static).render())
        await pilot.click("#tracker_next_failure")
        await pilot.pause()
        assert app.screen._selected_failure == ("nginx.config", "nginx-config")
        await pilot.press("o")
        await pilot.pause()
        assert app.screen is app.get_screen("sls")
        assert app.current_sls == "nginx.config"


@pytest.mark.asyncio
async def test_tracker_marks_non_returning_minion(tmp_path: Path):
    app = SaltTUI(Settings(database=tmp_path / "history.db"), initial="live")
    run = RunResult("salt '*' state.apply web --async", ["salt", "*", "state.apply", "web", "--async"],
                    "*", "glob", "state.apply", parsed={"minions": ["web01", "web02"]}, status="running")
    run.jid = "20261005150112345678"
    async with app.run_test(size=(140, 40)) as pilot:
        run.id = await app.db.save_run(run)
        app.current_run_id = run.id
        await app.db.query("SELECT 1")
        import sqlite3
        with sqlite3.connect(app.db.path) as con:
            con.execute("UPDATE runs SET started_at='2020-01-01T00:00:00+00:00' WHERE id=?", (run.id,))
            con.execute("UPDATE run_minion_progress SET last_event_at='2020-01-01T00:00:00+00:00' WHERE run_id=?", (run.id,))
        await app.db.mark_live_timeouts(1)
        await app.screen.refresh_data()
        await pilot.pause()
        assert "2 no return" in str(app.screen.query_one("#run_summary", Static).render())
        await pilot.press("f")
        await pilot.pause()
        assert app.screen.query_one("#minion_progress", DataTable).cursor_row in {0, 1}
