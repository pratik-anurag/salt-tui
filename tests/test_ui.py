from pathlib import Path

import pytest

from salt_tui.app import SaltTUI
from salt_tui.config import Settings
from salt_tui.models import RunResult
from salt_tui.salt.events import normalize_event
from salt_tui.storage.database import Database
from salt_tui.sls.graph import StateGraph
import json


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
