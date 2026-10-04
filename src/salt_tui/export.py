from __future__ import annotations

import asyncio
import csv
import io
import json
import os
from pathlib import Path
import tempfile
from typing import Any

import yaml

from salt_tui.storage.database import Database


async def run_bundle(db: Database, run_id: int) -> dict[str, Any]:
    run = await db.run(run_id)
    if not run:
        raise ValueError(f"Run #{run_id} not found")
    bundle = {"run": run, "states": await db.states(run_id),
              "failures": await db.query("SELECT * FROM failures WHERE run_id=? ORDER BY id", (run_id,)),
              "logs": await db.logs(limit=10000, run_id=run_id),
              "events": await db.events(jid=run["jid"], limit=10000) if run["jid"] else []}
    return db.redactor.value(bundle)


async def export_run(db: Database, run_id: int, path: Path, format: str) -> Path:
    if format not in {"json", "yaml", "csv", "txt"}:
        raise ValueError("Export format must be json, yaml, csv, or txt")
    bundle = await run_bundle(db, run_id)
    return await asyncio.to_thread(_write, bundle, path, format)


def _write(bundle: dict[str, Any], path: Path, format: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if format == "json":
        content = json.dumps(bundle, indent=2, ensure_ascii=False, default=str)
    elif format == "yaml":
        content = yaml.safe_dump(bundle, allow_unicode=True, sort_keys=False)
    elif format == "csv":
        buffer = io.StringIO()
        fields = ("minion_id", "sls", "state_id", "state_module", "state_function", "name", "result", "duration_ms", "comment")
        writer = csv.DictWriter(buffer, fieldnames=fields)
        writer.writeheader()
        for state in bundle["states"]:
            writer.writerow({field: state.get(field) for field in fields})
        content = buffer.getvalue()
    else:
        run = bundle["run"]
        lines = [f"Run #{run['id']} — {run['status']}", run["command"],
                 f"Target: {run['target_expression']} | JID: {run['jid'] or '-'} | Duration: {run['duration_ms']} ms",
                 "", "States:"]
        lines.extend(f"{s['minion_id']} {s['state_id']} {s['state_module']}.{s['state_function']} result={s['result']} {s['comment']}" for s in bundle["states"])
        lines.extend(["", "Failures:"])
        lines.extend(f"{f['minion_id']} {f['state_id']}: {f['error_text']}" for f in bundle["failures"])
        lines.extend(["", "Logs:"])
        lines.extend(f"{log['timestamp']} {log['level']} {log['message']}" for log in bundle["logs"])
        content = "\n".join(lines)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
    os.replace(temporary, path)
    return path
