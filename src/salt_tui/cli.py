from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from salt_tui import __version__
from salt_tui.app import SaltTUI
from salt_tui.config import Settings
from salt_tui.diagnostics import create_diagnostics_bundle, setup_logging


def main() -> None:
    parser = argparse.ArgumentParser(description="Interactive SaltStack operations console")
    parser.add_argument("--version", action="version", version=f"salt-tui {__version__}")
    sub = parser.add_subparsers(dest="screen")
    sls = sub.add_parser("sls", help="Open SLS explorer")
    sls.add_argument("name", nargs="?")
    run = sub.add_parser("run", help="Open command runner prefilled with a Salt function")
    run.add_argument("function")
    run.add_argument("arguments", nargs="*")
    diagnostics = sub.add_parser("diagnostics", help="Write a redacted support bundle")
    diagnostics.add_argument("--output", type=Path)
    diagnostics.add_argument("--run", type=int, help="Explicitly include one run")
    for name in ("history", "failures", "intelligence", "performance", "minions", "jobs", "logs", "events", "live", "graph", "runner", "orchestration", "targets", "matrix", "samples", "functions", "keys", "file-copy"):
        sub.add_parser(name)
    args = parser.parse_args()
    settings = Settings.load()
    setup_logging(settings)
    if args.screen == "diagnostics":
        path = args.output or settings.database.parent / "diagnostics.zip"
        print(asyncio.run(create_diagnostics_bundle(settings, path, args.run)))
        return
    initial = args.screen or "dashboard"
    if initial == "file-copy":
        initial = "file_copy"
    command = None
    if initial == "run":
        initial = "command"
        command = " ".join(["salt", repr(settings.default_target), args.function, *args.arguments])
    app = SaltTUI(settings, initial, command)
    if args.screen == "sls" and args.name:
        app.current_sls = args.name
    app.run()


if __name__ == "__main__":
    main()
