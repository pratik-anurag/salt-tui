"""Synthetic scale benchmark; no Salt master required.

Run after `pip install -e .`:
    python benchmarks/scale.py --minions 10000 --states 20000 --history 1000000
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sqlite3
import tempfile
from time import perf_counter

from salt_tui.models import SaltEvent
from salt_tui.salt.events import EventMonitor
from salt_tui.sls.graph import StateGraph
from salt_tui.storage.database import Database


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minions", type=int, default=10000)
    parser.add_argument("--states", type=int, default=20000)
    parser.add_argument("--history", type=int, default=1000000)
    parser.add_argument("--events", type=int, default=100000)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "scale.db"
        db = Database(path)
        await db.migrate()
        chunks = []
        for index in range(args.states):
            chunk = {"__id__": f"state-{index}", "state": "test", "fun": "succeed_without_changes",
                     "__sls__": f"formula.module{index % 100}", "name": f"state-{index}"}
            if index:
                chunk["require"] = [{"test": f"state-{index - 1}"}]
            chunks.append(chunk)
        started = perf_counter()
        graph = StateGraph.from_low(chunks)
        build_seconds = perf_counter() - started
        started = perf_counter()
        diagnostics = graph.diagnostics()
        diagnostic_seconds = perf_counter() - started
        with sqlite3.connect(path) as con:
            con.execute("INSERT INTO runs (uuid,started_at,command,argv_json,status) VALUES (?,?,?,?,?)",
                        ("scale", "2026-01-01T00:00:00+00:00", "synthetic", "[]", "success"))
            run_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
            con.executemany("INSERT INTO run_minion_progress (run_id,minion_id,status) VALUES (?,?,?)",
                            ((run_id, f"minion-{index:05d}", "success") for index in range(args.minions)))
            started = perf_counter()
            batch = []
            for index in range(args.history):
                batch.append((run_id, f"minion-{index % max(1,args.minions):05d}", f"formula.module{index % 100}",
                              f"state-{index % 1000}", "test", "succeed_without_changes", f"state-{index % 1000}",
                              1, "{}", "ok", float(index % 1000)))
                if len(batch) == 10000:
                    con.executemany("""INSERT INTO state_results (run_id,minion_id,sls,state_id,state_module,state_function,name,
                        result,changes_json,comment,duration_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?)""", batch)
                    batch.clear()
            if batch:
                con.executemany("""INSERT INTO state_results (run_id,minion_id,sls,state_id,state_module,state_function,name,
                    result,changes_json,comment,duration_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?)""", batch)
        insert_seconds = perf_counter() - started
        started = perf_counter()
        minions = await db.progress(run_id)
        progress_seconds = perf_counter() - started
        started = perf_counter()
        history = await db.slow_states(limit=50)
        analytics_seconds = perf_counter() - started
        started = perf_counter()
        matrix = await db.matrix_summary(run_id, limit=200)
        summary_seconds = perf_counter() - started
        started = perf_counter()
        visible_minions = await db.matrix_minions(run_id)
        cells = await db.matrix_cells(run_id, visible_minions, [row["state_id"] for row in matrix])
        cell_seconds = perf_counter() - started
        monitor = EventMonitor(None, None, capacity=2000, queue_size=1000)
        event = SaltEvent("2026-01-01T00:00:00+00:00", "salt/job/test/ret/web01", "job", {}, "test")
        started = perf_counter()
        for _ in range(args.events):
            monitor.publish(event)
        event_seconds = perf_counter() - started
        print(f"Graph: {len(graph.nodes)} states, {len(graph.edges)} edges; build {build_seconds:.3f}s, diagnostics {diagnostic_seconds:.3f}s ({len(diagnostics)} hints)")
        print(f"SQLite: {args.history} state rows inserted in {insert_seconds:.3f}s; {len(minions)} minion progress rows read in {progress_seconds:.3f}s")
        print(f"Slow-state top {len(history)} groups: {analytics_seconds:.3f}s")
        print(f"Matrix: {len(matrix)} state groups in {summary_seconds:.3f}s; {len(visible_minions)} minions, {len(cells)} visible cells in {cell_seconds:.3f}s")
        print(f"Event ring: {args.events} synthetic events in {event_seconds:.3f}s; retained {len(monitor.recent)}")
        with sqlite3.connect(path) as con:
            for query in ("SELECT * FROM events WHERE jid='20261004150112345678' ORDER BY id DESC LIMIT 500",
                          "SELECT * FROM state_results WHERE sls='formula.module1' AND state_id='state-1' AND state_module='test' AND state_function='succeed_without_changes' ORDER BY duration_ms LIMIT 1"):
                print("PLAN", *con.execute("EXPLAIN QUERY PLAN " + query).fetchall())


if __name__ == "__main__":
    asyncio.run(main())
