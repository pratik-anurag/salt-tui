from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sqlite3
import zipfile

from salt_tui import __version__
from salt_tui.config import Settings
from salt_tui.export import run_bundle
from salt_tui.salt.client import detect_capabilities
from salt_tui.salt.redaction import Redactor
from salt_tui.storage.database import Database


def setup_logging(settings: Settings) -> None:
    logger = logging.getLogger("salt_tui")
    if logger.handlers:
        return
    try:
        path = settings.database.parent / "app.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    except OSError:
        logger.addHandler(logging.NullHandler())


async def create_diagnostics_bundle(settings: Settings, path: Path, run_id: int | None = None) -> Path:
    redactor = Redactor(settings.redaction_patterns)
    caps = await detect_capabilities(settings)
    schema_version = None
    if settings.database.exists():
        try:
            with sqlite3.connect(f"file:{settings.database}?mode=ro", uri=True) as con:
                schema_version = con.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        except (sqlite3.Error, OSError):
            pass
    config = {key: str(value) if isinstance(value, Path) else value for key, value in asdict(settings).items()}
    info = redactor.value({"salt_tui_version": __version__, "created_at": datetime.now(timezone.utc).isoformat(),
                           "salt_version": caps.version, "executables": caps.executables,
                           "schema_version": schema_version, "config": config})
    app_log = settings.database.parent / "app.log"
    log_tail = ""
    if app_log.exists():
        try:
            log_tail = redactor.text("\n".join(app_log.read_text(errors="replace").splitlines()[-200:]))
        except OSError:
            pass
    selected = None
    if run_id is not None:
        db = Database(settings.database, redactor)
        selected = await run_bundle(db, run_id)
    await asyncio.to_thread(_write_bundle, path, info, log_tail, selected)
    return path


def _write_bundle(path: Path, info: dict, log_tail: str, selected: dict | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("diagnostics.json", json.dumps(info, indent=2, ensure_ascii=False, default=str))
        bundle.writestr("app-log-tail.txt", log_tail)
        if selected is not None:
            bundle.writestr("selected-run.json", json.dumps(selected, indent=2, ensure_ascii=False, default=str))
