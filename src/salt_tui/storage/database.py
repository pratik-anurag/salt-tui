from __future__ import annotations

import asyncio
import json
import hashlib
import sqlite3
from pathlib import Path
from typing import Any

from salt_tui.models import RunResult, SaltEvent, now
from salt_tui.salt.parser import extract_states
from salt_tui.salt.event_filters import EventFilter
from salt_tui.analytics.failures import failure_signature
from salt_tui.analytics.search import HistoryQuery
from salt_tui.salt.redaction import Redactor

MIGRATIONS = Path(__file__).parent / "migrations"


def encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


class Database:
    def __init__(self, path: Path, redactor: Redactor | None = None):
        self.path = path
        self.redactor = redactor or Redactor()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        return con

    async def migrate(self) -> None:
        await asyncio.to_thread(self._migrate)

    def _migrate(self) -> None:
        with self._connect() as con:
            con.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY)")
            for path in sorted(MIGRATIONS.glob("*.sql")):
                version = int(path.stem.split("_")[0])
                if not con.execute("SELECT 1 FROM schema_migrations WHERE version=?", (version,)).fetchone():
                    try:
                        con.executescript(f"BEGIN IMMEDIATE;\n{path.read_text()}\nINSERT INTO schema_migrations VALUES ({version});\nCOMMIT;")
                    except Exception:
                        con.rollback()
                        raise

    async def save_run(self, run: RunResult, *, saltenv: str | None = None, pillarenv: str | None = None,
                       test_mode: bool = False, log_lines: list[tuple[str, str]] | None = None) -> int:
        return await asyncio.to_thread(self._save_run, run, saltenv, pillarenv, test_mode, log_lines or [])

    def _save_run(self, run: RunResult, saltenv: str | None, pillarenv: str | None,
                  test_mode: bool, log_lines: list[tuple[str, str]]) -> int:
        safe_argv = self.redactor.argv(run.argv)
        safe_command = self.redactor.text(run.command)
        safe_stdout = self.redactor.text(run.stdout)
        safe_stderr = self.redactor.text(run.stderr)
        safe_parsed = self.redactor.value(run.parsed)
        with self._connect() as con:
            expected = isinstance(run.parsed, dict) and isinstance(run.parsed.get("minions"), list)
            cur = con.execute("""INSERT INTO runs (uuid,started_at,finished_at,command,argv_json,command_type,target_expression,target_type,
                saltenv,pillarenv,test_mode,exit_code,status,duration_ms,stdout,stderr,raw_result_json,jid,expected_minions_known,parent_run_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (run.uuid, run.started_at, None if run.status == "running" else run.finished_at, safe_command,
                encode(safe_argv), run.function, run.target, run.target_type, saltenv, pillarenv, int(test_mode),
                run.exit_code, run.status, run.duration_ms, safe_stdout, safe_stderr, encode(safe_parsed), run.jid, int(expected), run.parent_run_id))
            run_id = cur.lastrowid
            if isinstance(run.parsed, dict) and run.status != "running":
                for minion, result in run.parsed.items():
                    success = result is not False and not any(s.minion == str(minion) and s.result is False for s in run.states)
                    con.execute("INSERT INTO minion_results (run_id,minion_id,success,raw_result_json) VALUES (?,?,?,?)",
                        (run_id, str(minion), int(success), encode(self.redactor.value(result))))
            for state in run.states:
                cur = con.execute("""INSERT INTO state_results (run_id,minion_id,sls,state_id,state_module,state_function,name,result,
                    changes_json,comment,started_at,duration_ms,raw_result_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (run_id, state.minion, state.sls, state.state_id, state.module, state.function, state.name,
                     None if state.result is None else int(state.result), encode(self.redactor.value(state.changes)), self.redactor.text(state.comment),
                     state.started_at, state.duration_ms, encode(self.redactor.value(state.raw))))
                if state.result is False:
                    con.execute("INSERT INTO failures (run_id,state_result_id,minion_id,sls,state_id,error_text,created_at,signature) VALUES (?,?,?,?,?,?,?,?)",
                                (run_id, cur.lastrowid, state.minion, state.sls, state.state_id, self.redactor.text(state.comment), run.started_at, failure_signature(self.redactor.text(state.comment))))
            for level, message in log_lines:
                con.execute("INSERT INTO logs (run_id,timestamp,level,source,message) VALUES (?,?,?,?,?)",
                            (run_id, now(), level, "salt-cli", self.redactor.text(message)))
            if run.stderr:
                con.execute("INSERT INTO logs (run_id,timestamp,level,source,message) VALUES (?,?,?,?,?)",
                            (run_id, now(), "ERROR", "salt-cli", safe_stderr[:100000]))
            if run.status == "running" and isinstance(run.parsed, dict):
                for minion in run.parsed.get("minions", []) if isinstance(run.parsed.get("minions"), list) else []:
                    con.execute("INSERT OR IGNORE INTO run_minion_progress (run_id,minion_id,status) VALUES (?,?,?)",
                                (run_id, str(minion), "pending"))
            return run_id

    async def save_events(self, events: list[SaltEvent]) -> None:
        await asyncio.to_thread(self._save_events, events)

    def _save_events(self, events: list[SaltEvent]) -> None:
        with self._connect() as con:
            for event in events:
                event.payload = self.redactor.value(event.payload)
                event.summary = self.redactor.text(event.summary)
                cur = con.execute("""INSERT INTO events (timestamp,tag,category,kind,jid,minion_id,function,target,summary,payload_json)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""", (event.timestamp, event.tag, event.category, event.kind,
                    event.jid, event.minion_id, event.function, event.target, self.redactor.text(event.summary), encode(self.redactor.value(event.payload))))
                event.id = cur.lastrowid
                parent_jid = event.payload.get("orchestration_jid")
                metadata = event.payload.get("metadata")
                if not parent_jid and isinstance(metadata, dict):
                    parent_jid = metadata.get("orchestration_jid")
                if event.category == "job" and event.kind == "new" and parent_jid and event.jid:
                    for parent in con.execute("SELECT id FROM runs WHERE jid=?", (str(parent_jid),)).fetchall():
                        con.execute("INSERT OR IGNORE INTO run_relationships VALUES (?,?,?)",
                                    (parent[0], event.jid, "orchestration child job"))
                if event.jid:
                    rows = con.execute("SELECT id FROM runs WHERE jid=?", (event.jid,)).fetchall()
                    for row in rows:
                        self._link_event(con, row[0], event)

    def _link_event(self, con: sqlite3.Connection, run_id: int, event: SaltEvent) -> None:
        con.execute("INSERT OR IGNORE INTO event_run_links VALUES (?,?)", (event.id, run_id))
        if event.category != "job":
            return
        if event.kind == "new":
            con.execute("UPDATE runs SET expected_minions_known=1 WHERE id=?", (run_id,))
            for minion in event.payload.get("minions", []) if isinstance(event.payload.get("minions"), list) else []:
                con.execute("INSERT OR IGNORE INTO run_minion_progress (run_id,minion_id,status,last_event_at) VALUES (?,?,?,?)",
                            (run_id, str(minion), "pending", event.timestamp))
        elif event.kind in {"start", "prog", "ret"} and event.minion_id:
            minion = event.minion_id
            con.execute("INSERT OR IGNORE INTO run_minion_progress (run_id,minion_id,status,last_event_at) VALUES (?,?,?,?)",
                        (run_id, minion, "running", event.timestamp))
            if event.kind == "start":
                con.execute("UPDATE run_minion_progress SET status='running',last_event_at=? WHERE run_id=? AND minion_id=? AND status='pending'",
                            (event.timestamp, run_id, minion))
            elif event.kind == "prog":
                data = event.payload.get("data")
                states = extract_states({minion: data}) if isinstance(data, dict) else []
                state_id = states[0].state_id if states else None
                status = "failed" if states and states[0].result is False else "changed" if states and states[0].changes else "success"
                con.execute("INSERT OR IGNORE INTO run_state_progress (run_id,minion_id,event_tag,state_id,status,payload_json) VALUES (?,?,?,?,?,?)",
                            (run_id, minion, event.tag, state_id, status, encode(data)))
                con.execute("""UPDATE run_minion_progress SET status='running',last_event_at=?,
                    completed_states=(SELECT COUNT(*) FROM run_state_progress WHERE run_id=? AND minion_id=?)
                    WHERE run_id=? AND minion_id=? AND status NOT IN ('success','failed')""",
                    (event.timestamp, run_id, minion, run_id, minion))
            else:
                result = event.payload.get("return")
                states = extract_states({minion: result}) if isinstance(result, dict) else []
                success = event.payload.get("retcode", 0) == 0 and event.payload.get("success", True) is not False and not any(s.result is False for s in states)
                con.execute("""UPDATE run_minion_progress SET status=?,last_event_at=?,return_code=?,raw_return_json=?,
                    completed_states=?,total_states=? WHERE run_id=? AND minion_id=?""",
                    ("success" if success else "failed", event.timestamp, event.payload.get("retcode"), encode(result),
                     len(states), len(states), run_id, minion))
                con.execute("DELETE FROM failures WHERE run_id=? AND minion_id=?", (run_id, minion))
                con.execute("DELETE FROM state_results WHERE run_id=? AND minion_id=?", (run_id, minion))
                con.execute("DELETE FROM minion_results WHERE run_id=? AND minion_id=?", (run_id, minion))
                con.execute("INSERT INTO minion_results (run_id,minion_id,success,return_code,raw_result_json) VALUES (?,?,?,?,?)",
                            (run_id, minion, int(success), event.payload.get("retcode"), encode(result)))
                for state in states:
                    cur = con.execute("""INSERT INTO state_results (run_id,minion_id,sls,state_id,state_module,state_function,name,result,
                        changes_json,comment,started_at,duration_ms,raw_result_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (run_id, state.minion, state.sls, state.state_id, state.module, state.function, state.name,
                         None if state.result is None else int(state.result), encode(state.changes), state.comment,
                         state.started_at, state.duration_ms, encode(state.raw)))
                    if state.result is False:
                        con.execute("INSERT INTO failures (run_id,state_result_id,minion_id,sls,state_id,error_text,created_at,signature) VALUES (?,?,?,?,?,?,?,?)",
                                    (run_id, cur.lastrowid, minion, state.sls, state.state_id, state.comment, event.timestamp, failure_signature(state.comment)))
                statuses = [r[0] for r in con.execute("SELECT status FROM run_minion_progress WHERE run_id=?", (run_id,))]
                expected_known = con.execute("SELECT expected_minions_known FROM runs WHERE id=?", (run_id,)).fetchone()[0]
                if expected_known and statuses and all(s in {"success", "failed", "no_return"} for s in statuses):
                    status = "partial" if "no_return" in statuses else "failed" if "failed" in statuses else "success"
                    self._finish_run(con, run_id, status, event.timestamp)

    def _finish_run(self, con: sqlite3.Connection, run_id: int, status: str, finished_at: str) -> None:
        from datetime import datetime, timezone
        started = con.execute("SELECT started_at FROM runs WHERE id=?", (run_id,)).fetchone()[0]
        try:
            start_time = datetime.fromisoformat(started)
            end_time = datetime.fromisoformat(finished_at)
            if start_time.tzinfo is None:
                start_time = start_time.replace(tzinfo=timezone.utc)
            if end_time.tzinfo is None:
                end_time = end_time.replace(tzinfo=timezone.utc)
            duration_ms = max(0, int((end_time - start_time).total_seconds() * 1000))
        except ValueError:
            duration_ms = 0
        con.execute("UPDATE runs SET status=?,finished_at=?,duration_ms=? WHERE id=?", (status, finished_at, duration_ms, run_id))

    async def replay_events(self, run_id: int, jid: str) -> None:
        await asyncio.to_thread(self._replay_events, run_id, jid)

    def _replay_events(self, run_id: int, jid: str) -> None:
        with self._connect() as con:
            rows = con.execute("SELECT * FROM events WHERE jid=? ORDER BY id", (jid,)).fetchall()
            for row in rows:
                event = SaltEvent(row["timestamp"], row["tag"], row["category"], json.loads(row["payload_json"]),
                                  row["summary"], row["jid"], row["minion_id"], row["function"], row["target"], row["kind"], row["id"])
                self._link_event(con, run_id, event)

    async def events(self, search: str = "", limit: int = 500, jid: str | None = None) -> list[dict]:
        return await self.events_filtered(EventFilter(text=search, jid=jid or ""), limit)

    async def events_filtered(self, event_filter: EventFilter, limit: int = 500) -> list[dict]:
        terms = []
        params: list[Any] = []
        for field, column in (("tag", "tag"), ("minion", "minion_id"), ("function", "function")):
            value = getattr(event_filter, field)
            if value:
                terms.append(f"{column} LIKE ?")
                params.append(f"%{value}%")
        if event_filter.prefix:
            terms.append("tag LIKE ?")
            params.append(f"{event_filter.prefix}%")
        for field in ("jid", "category"):
            value = getattr(event_filter, field)
            if value:
                terms.append(f"{field}=?")
                params.append(value)
        if event_filter.since:
            terms.append("timestamp>=?")
            params.append(event_filter.since)
        if event_filter.until:
            terms.append("timestamp<=?")
            params.append(event_filter.until)
        if event_filter.text:
            terms.append("(tag LIKE ? OR summary LIKE ? OR minion_id LIKE ? OR jid LIKE ? OR function LIKE ? OR payload_json LIKE ?)")
            params.extend([f"%{event_filter.text}%"] * 6)
        where = " WHERE " + " AND ".join(terms) if terms else ""
        return await self.query("SELECT * FROM events" + where + " ORDER BY id DESC LIMIT ?", (*params, limit))

    async def prune_events(self, limit: int) -> None:
        await asyncio.to_thread(self._prune_events, limit)

    def _prune_events(self, limit: int) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT ?)", (limit,))

    async def progress(self, run_id: int) -> list[dict]:
        return await self.query("SELECT * FROM run_minion_progress WHERE run_id=? ORDER BY minion_id", (run_id,))

    async def live_states(self, run_id: int, minion_id: str) -> list[dict]:
        return await self.query("SELECT * FROM run_state_progress WHERE run_id=? AND minion_id=? ORDER BY event_tag", (run_id, minion_id))

    async def run(self, run_id: int) -> dict | None:
        rows = await self.query("SELECT * FROM runs WHERE id=?", (run_id,))
        return rows[0] if rows else None

    async def child_jobs(self, run_id: int) -> list[dict]:
        return await self.query("""SELECT x.child_jid,x.relation,
          (SELECT COUNT(*) FROM events e WHERE e.jid=x.child_jid AND e.kind='ret') returned,
          (SELECT summary FROM events e WHERE e.jid=x.child_jid AND e.kind='new' ORDER BY e.id DESC LIMIT 1) summary
          FROM run_relationships x WHERE x.parent_run_id=? ORDER BY x.child_jid""", (run_id,))

    async def list_targets(self) -> list[dict]:
        return await self.query("SELECT * FROM saved_targets ORDER BY name")

    async def save_target(self, name: str, expression: str, target_type: str) -> None:
        if target_type not in {"glob", "grain", "pillar", "compound", "nodegroup", "list", "pcre"}:
            raise ValueError("Unsupported target type")
        if not name.strip() or not expression.strip():
            raise ValueError("Name and target expression are required")
        await asyncio.to_thread(self._save_target, name.strip(), expression.strip(), target_type)

    def _save_target(self, name: str, expression: str, target_type: str) -> None:
        with self._connect() as con:
            con.execute("""INSERT INTO saved_targets (name,expression,target_type,created_at,updated_at) VALUES (?,?,?,?,?)
              ON CONFLICT(name) DO UPDATE SET expression=excluded.expression,target_type=excluded.target_type,updated_at=excluded.updated_at""",
              (name, expression, target_type, now(), now()))

    async def rename_target(self, old: str, new: str) -> None:
        if not new.strip():
            raise ValueError("New name is required")
        await asyncio.to_thread(self._rename_target, old, new.strip())

    def _rename_target(self, old: str, new: str) -> None:
        with self._connect() as con:
            con.execute("UPDATE saved_targets SET name=?,updated_at=? WHERE name=?", (new, now(), old))

    async def delete_target(self, name: str) -> None:
        await asyncio.to_thread(self._delete_target, name)

    def _delete_target(self, name: str) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM saved_targets WHERE name=?", (name,))

    async def seed_targets(self, targets: dict[str, dict[str, str]]) -> None:
        await asyncio.to_thread(self._seed_targets, targets)

    def _seed_targets(self, targets: dict[str, dict[str, str]]) -> None:
        with self._connect() as con:
            for name, definition in targets.items():
                expression, target_type = definition.get("expression", ""), definition.get("type", "glob")
                if name and expression and target_type in {"glob", "grain", "pillar", "compound", "nodegroup", "list", "pcre"}:
                    con.execute("INSERT OR IGNORE INTO saved_targets (name,expression,target_type,created_at,updated_at) VALUES (?,?,?,?,?)",
                                (name, expression, target_type, now(), now()))

    async def redact_legacy_history(self, batch_size: int = 200) -> None:
        """Incrementally sanitize records created before redaction was added."""
        tables = {
            "runs": ("command", "argv_json", "stdout", "stderr", "raw_result_json"),
            "minion_results": ("raw_result_json",),
            "state_results": ("changes_json", "comment", "raw_result_json"),
            "logs": ("message",),
            "failures": ("error_text", "signature"),
            "events": ("summary", "payload_json"),
            "run_minion_progress": (),
        }
        fingerprint = hashlib.sha256("|".join(p.pattern for p in self.redactor.patterns).encode()).hexdigest()[:12]
        for table, columns in tables.items():
            if not columns:
                continue
            key = f"redact-v1:{fingerprint}:{table}"
            while await asyncio.to_thread(self._redact_batch, table, columns, key, batch_size):
                await asyncio.sleep(0)

    def _redact_batch(self, table: str, columns: tuple[str, ...], key: str, limit: int) -> int:
        with self._connect() as con:
            row = con.execute("SELECT value FROM maintenance_state WHERE key=?", (key,)).fetchone()
            cursor = int(row[0]) if row else 0
            rows = con.execute(f"SELECT id,{','.join(columns)} FROM {table} WHERE id>? ORDER BY id LIMIT ?", (cursor, limit)).fetchall()
            for item in rows:
                values = []
                for column in columns:
                    original = item[column]
                    if original is None:
                        values.append(None)
                    elif column == "argv_json":
                        try:
                            values.append(encode(self.redactor.argv(json.loads(original))))
                        except (json.JSONDecodeError, TypeError):
                            values.append(self.redactor.text(original))
                    elif column.endswith("_json") or column == "stdout":
                        try:
                            values.append(encode(self.redactor.value(json.loads(original))))
                        except (json.JSONDecodeError, TypeError):
                            values.append(self.redactor.text(original))
                    else:
                        values.append(self.redactor.text(original))
                if table == "failures":
                    values[1] = failure_signature(values[0] or "")
                assignments = ",".join(f"{column}=?" for column in columns)
                con.execute(f"UPDATE {table} SET {assignments} WHERE id=?", (*values, item["id"]))
            if rows:
                con.execute("INSERT INTO maintenance_state (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (key, str(rows[-1]["id"])))
            return len(rows)

    async def mark_live_timeouts(self, seconds: int) -> None:
        await asyncio.to_thread(self._mark_live_timeouts, seconds)

    def _mark_live_timeouts(self, seconds: int) -> None:
        from datetime import datetime, timezone, timedelta
        cutoff_time = datetime.now(timezone.utc) - timedelta(seconds=seconds)
        cutoff = cutoff_time.isoformat()
        with self._connect() as con:
            runs = con.execute("SELECT id FROM runs WHERE status='running' AND jid IS NOT NULL AND started_at<?", (cutoff,)).fetchall()
            for row in runs:
                run_id = row[0]
                progress = con.execute("SELECT minion_id,status,last_event_at FROM run_minion_progress WHERE run_id=?", (run_id,)).fetchall()
                for item in progress:
                    if item["status"] not in {"pending", "running"}:
                        continue
                    stamp = item["last_event_at"] or con.execute("SELECT started_at FROM runs WHERE id=?", (run_id,)).fetchone()[0]
                    try:
                        last_seen = datetime.fromisoformat(stamp)
                        if last_seen.tzinfo is None:
                            last_seen = last_seen.replace(tzinfo=timezone.utc)
                    except ValueError:
                        last_seen = cutoff_time - timedelta(seconds=1)
                    if last_seen < cutoff_time:
                        con.execute("UPDATE run_minion_progress SET status='no_return' WHERE run_id=? AND minion_id=?", (run_id, item["minion_id"]))
                statuses = [r[0] for r in con.execute("SELECT status FROM run_minion_progress WHERE run_id=?", (run_id,))]
                if not statuses or all(s in {"success", "failed", "no_return"} for s in statuses):
                    status = "partial" if any(s in {"success", "failed"} for s in statuses) and "no_return" in statuses else \
                             "failed" if "failed" in statuses else "success" if statuses and all(s == "success" for s in statuses) else "timed_out"
                    self._finish_run(con, run_id, status, now())

    async def query(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._query, sql, params)

    def _query(self, sql: str, params: tuple) -> list[dict[str, Any]]:
        with self._connect() as con:
            return [dict(row) for row in con.execute(sql, params).fetchall()]

    async def runs(self, search: str = "", limit: int = 200, offset: int = 0) -> list[dict]:
        term = f"%{search}%"
        return await self.query("""SELECT r.*, (SELECT COUNT(*) FROM state_results s WHERE s.run_id=r.id AND s.result=0) failed,
          (SELECT COUNT(*) FROM state_results s WHERE s.run_id=r.id AND s.changes_json NOT IN ('{}','null')) changed
          FROM runs r WHERE r.command LIKE ? OR r.target_expression LIKE ? ORDER BY r.id DESC LIMIT ? OFFSET ?""",
          (term, term, limit, offset))

    async def search_runs(self, query: HistoryQuery, limit: int = 200, offset: int = 0) -> list[dict]:
        conditions: list[str] = []
        params: list[Any] = []
        for term in query.terms:
            if term.field == "minion":
                conditions.append("(EXISTS (SELECT 1 FROM minion_results m WHERE m.run_id=r.id AND m.minion_id LIKE ?) OR EXISTS (SELECT 1 FROM run_minion_progress p WHERE p.run_id=r.id AND p.minion_id LIKE ?))")
                params.extend([f"%{term.value}%"] * 2)
            elif term.field in {"sls", "state"}:
                column = {"sls": "sls", "state": "state_id"}[term.field]
                conditions.append(f"EXISTS (SELECT 1 FROM state_results s WHERE s.run_id=r.id AND s.{column} LIKE ?)")
                params.append(f"%{term.value}%")
            elif term.field == "result":
                conditions.append("r.status=?")
                params.append(term.value)
            elif term.field == "since":
                conditions.append("r.started_at>=?")
                params.append(term.value)
            else:
                column = {"command": "command", "target": "target_expression", "jid": "jid"}[term.field]
                conditions.append(f"r.{column} LIKE ?")
                params.append(f"%{term.value}%")
        for text in query.text:
            conditions.append("(r.command LIKE ? OR r.target_expression LIKE ? OR r.jid LIKE ?)")
            params.extend([f"%{text}%"] * 3)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        sql = """SELECT r.*, (SELECT COUNT(*) FROM state_results s WHERE s.run_id=r.id AND s.result=0) failed,
          (SELECT COUNT(*) FROM state_results s WHERE s.run_id=r.id AND s.changes_json NOT IN ('{}','null')) changed
          FROM runs r""" + where + " ORDER BY r.id DESC LIMIT ? OFFSET ?"
        return await self.query(sql, (*params, limit, offset))

    async def states(self, run_id: int) -> list[dict]:
        return await self.query("SELECT * FROM state_results WHERE run_id=? ORDER BY id", (run_id,))

    async def state_metrics(self, sls: str, state_id: str, module: str, function: str) -> dict[str, Any]:
        rows = await self.query("""SELECT COUNT(*) samples, AVG(duration_ms) average_ms,
          SUM(CASE WHEN result=0 THEN 1 ELSE 0 END) failures,
          (SELECT result FROM state_results x WHERE x.sls=? AND x.state_id=? AND x.state_module=?
           AND x.state_function=? ORDER BY x.id DESC LIMIT 1) latest_result
          FROM state_results WHERE sls=? AND state_id=? AND state_module=? AND state_function=?""",
          (sls, state_id, module, function, sls, state_id, module, function))
        return rows[0]

    async def matrix_minions(self, run_id: int, limit: int = 20, offset: int = 0) -> list[str]:
        rows = await self.query("""SELECT minion_id FROM (
          SELECT minion_id FROM minion_results WHERE run_id=? UNION SELECT minion_id FROM run_minion_progress WHERE run_id=?
          ) ORDER BY minion_id LIMIT ? OFFSET ?""", (run_id, run_id, limit, offset))
        return [row["minion_id"] for row in rows]

    async def matrix_minion_count(self, run_id: int) -> int:
        rows = await self.query("""SELECT COUNT(*) count FROM (
          SELECT minion_id FROM minion_results WHERE run_id=? UNION SELECT minion_id FROM run_minion_progress WHERE run_id=?
          )""", (run_id, run_id))
        return rows[0]["count"]

    async def matrix_summary(self, run_id: int, sort: str = "failures", filter: str = "all",
                             search: str = "", limit: int = 200, offset: int = 0) -> list[dict]:
        sort_sql = {"failures": "failures DESC", "changed": "changed DESC", "duration": "mean_ms DESC",
                    "state_id": "state_id", "sls": "sls"}.get(sort, "failures DESC")
        having = {"all": "1=1", "failed": "failures>0", "changed": "changed>0",
                  "divergent": "variants>1", "slow": "mean_ms>=1000", "missing": "present<?"}.get(filter, "1=1")
        total = await self.matrix_minion_count(run_id)
        params: list[Any] = [run_id, f"%{search}%", f"%{search}%"]
        if filter == "missing":
            params.append(total)
        params.extend([limit, offset])
        return await self.query(f"""SELECT sls,state_id,state_module,state_function,
          SUM(CASE WHEN result=0 THEN 1 ELSE 0 END) failures,
          SUM(CASE WHEN changes_json NOT IN ('{{}}','null') THEN 1 ELSE 0 END) changed,
          AVG(duration_ms) mean_ms,COUNT(DISTINCT minion_id) present,
          CASE WHEN MIN(COALESCE(result,-1) * 2 + CASE WHEN changes_json NOT IN ('{{}}','null') THEN 1 ELSE 0 END)
             = MAX(COALESCE(result,-1) * 2 + CASE WHEN changes_json NOT IN ('{{}}','null') THEN 1 ELSE 0 END)
             THEN 1 ELSE 2 END variants
          FROM state_results WHERE run_id=? AND (state_id LIKE ? OR sls LIKE ?)
          GROUP BY sls,state_id,state_module,state_function HAVING {having}
          ORDER BY {sort_sql},sls,state_id LIMIT ? OFFSET ?""", tuple(params))

    async def matrix_cells(self, run_id: int, minions: list[str], state_ids: list[str]) -> list[dict]:
        if not minions or not state_ids:
            return []
        minion_marks = ",".join("?" for _ in minions)
        state_marks = ",".join("?" for _ in state_ids)
        return await self.query(f"""SELECT minion_id,sls,state_id,state_module,state_function,result,changes_json,duration_ms
          FROM state_results WHERE run_id=? AND minion_id IN ({minion_marks}) AND state_id IN ({state_marks})""",
          (run_id, *minions, *state_ids))

    async def failures(self, search: str = "", limit: int = 200) -> list[dict]:
        term = f"%{search}%"
        return await self.query("""SELECT * FROM failures WHERE minion_id LIKE ? OR sls LIKE ? OR state_id LIKE ? OR error_text LIKE ?
          ORDER BY id DESC LIMIT ?""", (term, term, term, term, limit))

    async def backfill_failure_signatures(self, limit: int = 500) -> int:
        return await asyncio.to_thread(self._backfill_failure_signatures, limit)

    def _backfill_failure_signatures(self, limit: int) -> int:
        with self._connect() as con:
            rows = con.execute("SELECT id,error_text FROM failures WHERE signature IS NULL LIMIT ?", (limit,)).fetchall()
            for row in rows:
                con.execute("UPDATE failures SET signature=? WHERE id=?", (failure_signature(row["error_text"] or ""), row["id"]))
            return len(rows)

    async def failure_groups(self, search: str = "", limit: int = 200) -> list[dict]:
        groups = await self.query("""SELECT COALESCE(signature,lower(error_text)) signature, COUNT(*) occurrences,
          COUNT(DISTINCT minion_id) minions, MIN(created_at) first_seen, MAX(created_at) last_seen,
          MAX(run_id) recent_run_id FROM failures WHERE signature LIKE ? OR error_text LIKE ?
          GROUP BY COALESCE(signature,lower(error_text)) ORDER BY occurrences DESC,last_seen DESC LIMIT ?""",
          (f"%{search}%", f"%{search}%", limit))
        for group in groups:
            count_rows = await self.query("""SELECT COUNT(*) count FROM failures f JOIN state_results s ON s.id=f.state_result_id
              WHERE f.signature=? AND s.duration_ms IS NOT NULL""", (group["signature"],))
            count = count_rows[0]["count"]
            if count:
                offsets = ((count - 1) // 2, count // 2)
                values = []
                for offset in offsets:
                    result = await self.query("""SELECT s.duration_ms FROM failures f JOIN state_results s ON s.id=f.state_result_id
                      WHERE f.signature=? AND s.duration_ms IS NOT NULL ORDER BY s.duration_ms LIMIT 1 OFFSET ?""",
                      (group["signature"], offset))
                    values.append(result[0]["duration_ms"])
                group["median_ms"] = sum(values) / 2
            else:
                group["median_ms"] = None
        return groups

    async def failure_occurrences(self, signature: str, limit: int = 200) -> list[dict]:
        return await self.query("SELECT * FROM failures WHERE signature=? ORDER BY id DESC LIMIT ?", (signature, limit))

    async def slow_states(self, limit: int = 100) -> list[dict]:
        groups = await self.query("""SELECT s.sls,s.state_id,s.state_module,s.state_function,COUNT(*) samples,
          AVG(s.duration_ms) mean_ms,MIN(s.duration_ms) min_ms,MAX(s.duration_ms) max_ms,
          (SELECT x.duration_ms FROM state_results x WHERE x.sls=s.sls AND x.state_id=s.state_id
           AND x.state_module=s.state_module AND x.state_function=s.state_function ORDER BY x.id DESC LIMIT 1) latest_ms
          FROM state_results s WHERE s.duration_ms IS NOT NULL
          GROUP BY s.sls,s.state_id,s.state_module,s.state_function ORDER BY mean_ms DESC LIMIT ?""", (limit,))
        for group in groups:
            count = group["samples"]
            import math
            offsets = (max(0, (count - 1) // 2), count // 2, max(0, math.ceil(count * 0.95) - 1))
            values = []
            for offset in offsets:
                rows = await self.query("""SELECT duration_ms FROM state_results WHERE sls=? AND state_id=?
                  AND state_module=? AND state_function=? AND duration_ms IS NOT NULL
                  ORDER BY duration_ms LIMIT 1 OFFSET ?""", (group["sls"], group["state_id"], group["state_module"], group["state_function"], offset))
                values.append(rows[0]["duration_ms"] if rows else None)
            group["median_ms"] = (values[0] + values[1]) / 2 if values[0] is not None and values[1] is not None else None
            group["p95_ms"] = values[2]
            group["regressed"] = count >= 5 and group["latest_ms"] is not None and group["median_ms"] is not None and \
                group["latest_ms"] >= max(group["median_ms"] * 2, group["median_ms"] + 100)
        return groups

    async def logs(self, search: str = "", limit: int = 500, run_id: int | None = None) -> list[dict]:
        term = f"%{search}%"
        return await self.query("""SELECT * FROM logs WHERE (message LIKE ? OR level LIKE ?)
          AND (? IS NULL OR run_id=?) ORDER BY id DESC LIMIT ?""", (term, term, run_id, run_id, limit))

    async def minions(self, search: str = "", limit: int = 500) -> list[dict]:
        return await self.query("""SELECT m.minion_id, MAX(r.started_at) last_seen,
          (SELECT CASE y.success WHEN 1 THEN 'success' ELSE 'failed' END FROM runs x JOIN minion_results y ON y.run_id=x.id WHERE y.minion_id=m.minion_id ORDER BY x.id DESC LIMIT 1) last_status
          FROM minion_results m JOIN runs r ON r.id=m.run_id WHERE m.minion_id LIKE ? GROUP BY m.minion_id
          ORDER BY m.minion_id LIMIT ?""", (f"%{search}%", limit))

    async def prune(self, history_limit: int, log_limit: int) -> None:
        await asyncio.to_thread(self._prune, history_limit, log_limit)

    def _prune(self, history_limit: int, log_limit: int) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM logs WHERE id NOT IN (SELECT id FROM logs ORDER BY id DESC LIMIT ?)", (log_limit,))
            old_ids = [r[0] for r in con.execute("SELECT id FROM runs ORDER BY id DESC LIMIT -1 OFFSET ?", (history_limit,))]
            for run_id in old_ids:
                for table in ("failures", "logs", "state_results", "minion_results"):
                    con.execute(f"DELETE FROM {table} WHERE run_id=?", (run_id,))
                con.execute("DELETE FROM runs WHERE id=?", (run_id,))
