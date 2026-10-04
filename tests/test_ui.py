from pathlib import Path
import asyncio

import pytest

from salt_tui.app import SaltTUI
from salt_tui.config import Settings
from salt_tui.models import RunResult
from salt_tui.salt.events import normalize_event
from salt_tui.storage.database import Database
from salt_tui.sls.graph import StateGraph
import json
from textual.widgets import Button, DataTable, LoadingIndicator, Static


@pytest.mark.asyncio
async def test_screen_navigation(tmp_path: Path):
    app = SaltTUI(Settings(database=tmp_path / "history.db"))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        for name in ("command", "minions", "states", "matrix", "sls", "graph", "history", "logs", "failures", "intelligence", "performance", "runner", "orchestration", "targets", "settings", "events", "live", "dashboard"):
            app.action_show(name)
            await pilot.pause()
            assert app.screen is app.get_screen(name)


@pytest.mark.asyncio
async def test_sls_keyboard_selection_opens_source(tmp_path: Path):
    root = tmp_path / "states"
    root.mkdir()
    (root / "example.sls").write_text("example:\n  test.nop: []\n")
    (root / "second.sls").write_text("second:\n  test.nop: []\n")
    app = SaltTUI(Settings(database=tmp_path / "history.db", file_roots={"base": [root]}), initial="sls")
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        table = app.screen.query_one("#files", DataTable)
        assert app.focused is table
        assert table.cursor_type == "row"
        assert app.current_sls == "example"
        await pilot.press("down")
        await pilot.pause()
        assert app.current_sls == "second"


@pytest.mark.asyncio
async def test_sls_dependencies_and_escape_back(tmp_path: Path):
    root = tmp_path / "states"
    root.mkdir()
    (root / "example.sls").write_text("example:\n  test.nop: []\n")
    app = SaltTUI(Settings(database=tmp_path / "history.db", file_roots={"base": [root]}))
    low = json.loads((Path(__file__).parent / "fixtures/show_low_sls.json").read_text())

    async def compiled(spec):
        assert spec.function == "state.show_low_sls"
        return RunResult("", [], "local", "glob", spec.function, parsed=low, status="success", exit_code=0)

    app.execute = compiled
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        assert app.focused.id == "files"
        assert "Dashboard › SLS Explorer" in str(app.screen.query_one(".breadcrumb", Static).render())
        await pilot.press("shift+tab", "enter")
        await pilot.pause()
        assert app.screen is app.get_screen("graph")
        assert "Dependencies" in str(app.screen.query_one(".breadcrumb", Static).render())
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is app.get_screen("sls")
        assert app.focused.id == "files"
        assert "Dependencies ready" in str(app.screen.query_one("#sls-status", Static).render())


@pytest.mark.asyncio
async def test_sls_compilation_error_is_visible(tmp_path: Path):
    root = tmp_path / "states"
    root.mkdir()
    (root / "example.sls").write_text("example:\n  test.nop: []\n")
    app = SaltTUI(Settings(database=tmp_path / "history.db", file_roots={"base": [root]}), initial="sls")

    async def failed(spec):
        return RunResult("", [], "local", "glob", spec.function, stderr="SLS 'example' failed to render", status="failed", exit_code=1)

    app.execute = failed
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("shift+tab", "shift+tab", "shift+tab", "enter")
        await pilot.pause()
        assert "Compilation failed" in str(app.screen.query_one("#source-title", Static).render())
        assert "failed to render" in str(app.screen.query_one("#source", Static).render())
        assert not app.screen.query_one("#high", Button).disabled


@pytest.mark.asyncio
async def test_sls_shows_progress_while_salt_compiles(tmp_path: Path):
    root = tmp_path / "states"
    root.mkdir()
    (root / "example.sls").write_text("example:\n  test.nop: []\n")
    app = SaltTUI(Settings(database=tmp_path / "history.db", file_roots={"base": [root]}), initial="sls")
    release = asyncio.Event()

    async def compiled(spec):
        await release.wait()
        return RunResult("", [], "local", "glob", spec.function, parsed={"example": {}}, status="success", exit_code=0)

    app.execute = compiled
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        task = asyncio.create_task(app.screen.compiled("high"))
        await pilot.pause()
        assert app.screen.query_one("#sls-loading", LoadingIndicator).display
        assert app.screen.query_one("#high", Button).disabled
        release.set()
        await task
        assert not app.screen.query_one("#sls-loading", LoadingIndicator).display


@pytest.mark.asyncio
async def test_live_minion_progress_updates_without_rebuilding(tmp_path: Path):
    settings = Settings(database=tmp_path / "history.db")
    db = Database(settings.database)
    await db.migrate()
    jid = "20261004150112345678"
    run = RunResult("salt 'web-*' state.highstate --async", ["salt", "web-*", "state.highstate", "--async"],
                    "web-*", "glob", "state.highstate")
    run.jid = jid
    run.parsed = {"jid": jid, "minions": ["web01"]}
    run_id = await db.save_run(run)
    app = SaltTUI(settings, initial="live")
    app.current_run_id = run_id
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        table = app.screen.query_one("#minion_progress")
        assert table.row_count == 1
        event = normalize_event({"tag": f"salt/job/{jid}/ret/web01", "data": {"jid": jid, "id": "web01", "retcode": 0, "return": {}}})
        await app.db.save_events([event])
        await app.screen.refresh_data()
        assert table.row_count == 1
        assert app.screen._rows["web01"]["status"] == "success"


@pytest.mark.asyncio
async def test_graph_screen_renders_compiled_state(tmp_path: Path):
    settings = Settings(database=tmp_path / "history.db")
    app = SaltTUI(settings, initial="graph")
    fixture = Path(__file__).parent / "fixtures/show_low_sls.json"
    app.current_graph = StateGraph.from_low(json.loads(fixture.read_text()))
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        table = app.screen.query_one("#graph_nodes")
        assert table.row_count == 3
        table.move_cursor(row=1)
        await pilot.pause()
        assert app.screen._selected() in app.current_graph.nodes
