import json
from pathlib import Path

import pytest

from salt_tui.models import RunResult
from salt_tui.salt.client import extract_jid
from salt_tui.salt.events import EventMonitor, normalize_event, parse_runner_line
from salt_tui.salt.event_filters import parse_event_filter
from salt_tui.salt.redaction import redact
from salt_tui.storage.database import Database

FIXTURES = Path(__file__).parent / "fixtures"
JID = "20261004150112345678"


def fixture_events():
    return [normalize_event(json.loads(line)) for line in (FIXTURES / "events.jsonl").read_text().splitlines()]


def test_event_normalization_and_redaction():
    event = fixture_events()[0]
    assert (event.category, event.kind, event.jid, event.function, event.target) == ("job", "new", JID, "state.highstate", "web-*")
    assert event.payload["password"] == "[REDACTED]"
    assert redact({"nested": {"api_key": "secret"}})["nested"]["api_key"] == "[REDACTED]"
    line = f'salt/job/{JID}/new {{"jid":"{JID}"}}'
    assert parse_runner_line(line).jid == JID
    assert parse_runner_line("not an event") is None
    assert extract_jid("not json", f"Executed command with job ID: {JID}") == JID
    event_filter = parse_event_filter(f"prefix:salt/job/{JID} minion:web01 type:job since:2026-10-04")
    assert event_filter.matches(fixture_events()[1])
    assert not event_filter.matches(fixture_events()[0])


@pytest.mark.asyncio
async def test_jid_correlation_and_partial_timeout(tmp_path):
    db = Database(tmp_path / "history.db")
    await db.migrate()
    events = fixture_events()
    # Events can arrive before the CLI publishes its local run record.
    await db.save_events(events[:2])
    run = RunResult("salt 'web-*' state.highstate --async", ["salt", "web-*", "state.highstate", "--async"],
                    "web-*", "glob", "state.highstate")
    run.jid = JID
    run.status = "running"
    run.parsed = {"jid": JID}
    run_id = await db.save_run(run)
    await db.replay_events(run_id, JID)
    await db.save_events(events[2:])
    progress = {row["minion_id"]: row for row in await db.progress(run_id)}
    assert progress["web01"]["status"] == "success"
    assert progress["web01"]["completed_states"] == 1
    assert progress["web02"]["status"] == "failed"
    assert progress["web03"]["status"] == "pending"
    assert (await db.run(run_id))["status"] == "running"
    assert len(await db.states(run_id)) == 2
    assert (await db.failures())[0]["minion_id"] == "web02"
    assert len(await db.events(jid=JID)) == 5
    assert len(await db.events_filtered(parse_event_filter("type:job minion:web01"))) == 3
    assert "do-not-store" not in json.dumps(await db.events(jid=JID))
    # Simulate an aged run without waiting for wall-clock time.
    await db.query("SELECT 1")
    import sqlite3
    with sqlite3.connect(db.path) as con:
        con.execute("UPDATE runs SET started_at='2020-01-01T00:00:00+00:00' WHERE id=?", (run_id,))
        con.execute("UPDATE run_minion_progress SET last_event_at='2020-01-01T00:00:00+00:00' WHERE run_id=? AND status='pending'", (run_id,))
    await db.mark_live_timeouts(1)
    assert (await db.run(run_id))["status"] == "partial"
    assert {r["minion_id"]: r["status"] for r in await db.progress(run_id)}["web03"] == "no_return"


def test_bounded_event_monitor(tmp_path):
    monitor = EventMonitor(None, None, capacity=2, queue_size=1)
    for event in fixture_events()[:3]:
        monitor.publish(event)
    assert len(monitor.recent) == 2
    assert monitor.dropped == 2


@pytest.mark.asyncio
async def test_migration_preserves_stage1_runs(tmp_path):
    import sqlite3
    path = tmp_path / "stage1.db"
    migration = Path(__file__).parents[1] / "src/salt_tui/storage/migrations/001_initial.sql"
    with sqlite3.connect(path) as con:
        con.executescript(migration.read_text())
        con.execute("INSERT INTO schema_migrations VALUES (1)")
        con.execute("""INSERT INTO runs (uuid,started_at,command,argv_json,status) VALUES (?,?,?,?,?)""",
                    ("stage1-uuid", "2026-01-01T00:00:00+00:00", "salt '*' test.ping", "[]", "success"))
    db = Database(path)
    await db.migrate()
    rows = await db.runs()
    assert len(rows) == 1 and rows[0]["uuid"] == "stage1-uuid"
    assert rows[0]["jid"] is None


@pytest.mark.asyncio
async def test_event_monitor_persists_mock_source(tmp_path):
    import asyncio
    class FakeSource:
        async def events(self):
            for item in fixture_events():
                yield item
            await asyncio.Event().wait()
    db = Database(tmp_path / "monitor.db")
    await db.migrate()
    monitor = EventMonitor(FakeSource(), db, capacity=3, queue_size=10)
    monitor.start()
    for _ in range(50):
        if len(monitor.recent) == 3:
            break
        await asyncio.sleep(0.01)
    await asyncio.wait_for(monitor.queue.join(), 2)
    assert len(await db.events()) == 5
    assert len(monitor.recent) == 3
    await monitor.stop()
