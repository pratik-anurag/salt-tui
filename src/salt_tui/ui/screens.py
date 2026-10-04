from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich.syntax import Syntax
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, RichLog, Static

from salt_tui.models import CommandSpec
from salt_tui.salt.commands import build_argv, display_argv, is_mutating, parse_line
from salt_tui.salt.jobs import parse_jobs
from salt_tui.sls.explorer import files, sls_name
from salt_tui.sls.dependencies import tree_lines
from salt_tui.sls.syntax import SaltSlsLexer
from salt_tui.sls.graph import StateGraph
from salt_tui.storage.comparison import compare_states
from salt_tui.analytics.comparison import detailed_compare
from salt_tui.analytics.search import parse_history_query
from salt_tui.export import export_run

if TYPE_CHECKING:
    from salt_tui.app import SaltTUI


def pretty(value: Any) -> str:
    try:
        return json.dumps(value, indent=2, ensure_ascii=False, default=str)
    except TypeError:
        return str(value)


class ConfirmScreen(ModalScreen[bool]):
    DEFAULT_CSS = """
    ConfirmScreen { align: center middle; background: $background 70%; }
    #confirm_box { width: 80; max-width: 95%; height: auto; padding: 2; border: heavy $warning; background: $surface; }
    #confirm_text { height: auto; margin-bottom: 1; }
    """

    def __init__(self, message: str):
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm_box"):
            yield Static(self.message, id="confirm_text")
            with Horizontal():
                yield Button("Execute", id="yes", variant="warning")
                yield Button("Cancel", id="no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")


class PaletteScreen(ModalScreen[str | None]):
    DEFAULT_CSS = """
    PaletteScreen { align: center top; background: $background 70%; }
    #palette_box { width: 72; height: auto; margin-top: 3; padding: 1; border: heavy $primary; background: $surface; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="palette_box"):
            yield Input(placeholder="dashboard, minions, jobs, states, sls, history, logs, failures, settings, command", id="palette_input")
            yield Static("Type a screen name or a Salt command, then Enter. Esc closes.")

    def on_mount(self) -> None:
        self.query_one("#palette_input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())

    def key_escape(self) -> None:
        self.dismiss(None)


class BaseScreen(Screen):
    title_text = ""

    @property
    def shell(self) -> SaltTUI:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(self.title_text, classes="page-title")
        yield from self.content()
        yield Footer()

    def content(self) -> ComposeResult:
        yield Static("")


class DashboardScreen(BaseScreen):
    title_text = "Dashboard"

    def content(self) -> ComposeResult:
        yield Static("Checking Salt capabilities…", id="capabilities")
        yield Static("Loading recent runs…", id="summary")
        yield DataTable(id="recent", cursor_type="row")
        yield Static("d dashboard  m minions  j jobs  r states  v live  e events  s SLS  h history  l logs  c commands  ! failures  : palette", classes="hint")


    async def on_mount(self) -> None:
        table = self.query_one("#recent", DataTable)
        table.add_columns("Time", "Target", "Command", "Result", "Changed", "Failed", "Duration")
        await self.refresh_data()

    async def refresh_data(self) -> None:
        caps = self.shell.capabilities
        if caps:
            available = ", ".join(name for name, ok in caps.executables.items() if ok) or "none"
            self.query_one("#capabilities", Static).update(f"Salt: {caps.version} | Available: {available} | Environment: {self.shell.settings.default_saltenv} | Event bus: {self.shell.events.status}")
        runs = await self.shell.db.runs(limit=20)
        table = self.query_one("#recent", DataTable)
        table.clear()
        self.rows = {str(run["id"]): run for run in runs}
        for run in runs:
            table.add_row(run["started_at"][:19], run["target_expression"] or "", run["command_type"] or "",
                          run["status"], str(run["changed"]), str(run["failed"]), f'{run["duration_ms"]/1000:.1f}s', key=str(run["id"]))
        self.query_one("#summary", Static).update(f"Recent runs: {len(runs)} | Failed: {sum(r['status']=='failed' for r in runs)} | Change count: {sum(r['changed'] for r in runs)}")

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        row = self.rows.get(str(event.row_key.value))
        if row and row["status"] == "running" and row["jid"]:
            self.shell.open_live_run(int(event.row_key.value))
        else:
            self.shell.open_run(int(event.row_key.value))


class SettingsScreen(BaseScreen):
    title_text = "Settings — loaded from config.toml"

    def content(self) -> ComposeResult:
        with VerticalScroll():
            yield Static("", id="settings_text")

    def on_mount(self) -> None:
        settings = self.shell.settings
        roots = "\n".join(f"  {env}: {', '.join(map(str, paths))}" for env, paths in settings.file_roots.items())
        self.query_one("#settings_text", Static).update(
            f"Database: {settings.database}\nDefault target: {settings.default_target}\nDefault saltenv: {settings.default_saltenv}\n"
            f"Confirm changes: {settings.confirm_changes}\nRefresh interval: {settings.refresh_seconds}s\n"
            f"History limit: {settings.history_limit}\nLog limit: {settings.log_limit}\n\nFile roots:\n{roots}\n\n"
            "Edit ~/.config/salt-tui/config.toml and restart to apply changes.")


class CommandScreen(BaseScreen):
    title_text = "Command runner — type an exact Salt CLI command; preview before execution"

    def __init__(self, initial: str | None = None):
        super().__init__()
        self.initial = initial
        self.last_test: tuple[CommandSpec, int] | None = None

    def content(self) -> ComposeResult:
        yield Input(value=self.initial or "salt '*' test.version", placeholder="salt '*' test.version", id="command")
        yield Static("", id="preview")
        with Horizontal(classes="toolbar"):
            yield Button("Run", id="run", variant="primary")
            yield Button("Run Live", id="live")
            yield Button("Test first", id="test")
            yield Button("Run For Real", id="promote")
            yield Button("Targets", id="targets")
            yield Button("Copy command", id="copy")
            yield Button("Cancel local CLI", id="cancel")
        yield RichLog(id="output", wrap=True, highlight=True, max_lines=2000)

    def on_mount(self) -> None:
        self.update_preview()
        self.query_one("#command", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "command":
            self.update_preview()

    def update_preview(self) -> None:
        try:
            spec = parse_line(self.query_one("#command", Input).value, self.shell.settings)
            argv = self.shell.client.redactor.argv(build_argv(spec, self.shell.settings))
            self.query_one("#preview", Static).update("Command preview (sensitive values masked): " + display_argv(argv))
        except ValueError as exc:
            self.query_one("#preview", Static).update(f"Command error: {exc}")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.start(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run":
            self.start(False)
        elif event.button.id == "live":
            self.start(False, live=True)
        elif event.button.id == "test":
            self.start(True)
        elif event.button.id == "promote":
            if not self.last_test:
                self.notify("Run Test first, then review its results", severity="warning")
            else:
                prior, run_id = self.last_test
                spec = replace(prior, test=False, async_run=False, arguments=list(prior.arguments), options=list(prior.options))
                asyncio.create_task(self._prepare(spec, force_confirmation=True, parent_run_id=run_id))
        elif event.button.id == "targets":
            self.shell.action_show("targets")
        elif event.button.id == "copy":
            try:
                spec = parse_line(self.query_one("#command", Input).value, self.shell.settings)
                safe = self.shell.client.redactor.argv(build_argv(spec, self.shell.settings))
                self.app.copy_to_clipboard(display_argv(safe))
                self.notify("Redacted command copied")
            except ValueError as exc:
                self.notify(str(exc), severity="error")
        elif event.button.id == "cancel":
            async def cancel_local() -> None:
                stopped = await self.shell.client.cancel()
                self.notify("Local CLI process stopped; remote Salt jobs may still run" if stopped else
                            "No cancellable local CLI process; remote Salt jobs are unchanged")
            asyncio.create_task(cancel_local())

    def start(self, test: bool, live: bool = False) -> None:
        try:
            spec = parse_line(self.query_one("#command", Input).value, self.shell.settings)
            if test:
                if not spec.function.startswith("state.") or spec.function.startswith("state.show_"):
                    raise ValueError("Test first applies only to state execution commands")
                spec.test = True
            if live:
                if spec.executable != "salt" or not spec.function.startswith("state.") or spec.function.startswith("state.show_"):
                    raise ValueError("Run Live supports state execution through the salt master CLI")
                if self.shell.events.source is None or self.shell.events.status not in {"listening", "connected"}:
                    raise ValueError(f"Event bus unavailable ({self.shell.events.status}); live tracking cannot start")
                spec.async_run = True
                if "state_events=True" not in spec.arguments:
                    spec.arguments.append("state_events=True")
            build_argv(spec, self.shell.settings)
        except ValueError as exc:
            self.notify(str(exc), severity="error")
            return
        asyncio.create_task(self._prepare(spec))

    async def _prepare(self, spec: CommandSpec, force_confirmation: bool = False,
                       parent_run_id: int | None = None) -> None:
        minions: list[str] | None = None
        preview_note = "Target preview not applicable."
        if is_mutating(spec) and not spec.test:
            self.query_one("#output", RichLog).write("Checking responding minions for target preview…")
            try:
                minions, preview_note = await self.shell.preview_target(spec)
            except Exception as exc:
                preview_note = f"Target preview unavailable: {exc}"
        count = len(minions) if minions is not None else None
        safe_argv = self.shell.client.redactor.argv(build_argv(spec, self.shell.settings))
        summary = (f"Responded: {count}\n" + "\n".join(minions[:10]) + (f"\n+{count-10} more" if count > 10 else "")) if minions is not None else "Responded: unknown"
        summary += f"\n{preview_note}"
        if is_mutating(spec) and not spec.test and (force_confirmation or self.shell.should_confirm(spec, count)):
            message = (f"Target: {spec.target}\nAction: {spec.function}\nEnvironment: {spec.saltenv or self.shell.settings.default_saltenv}\n"
                       f"Test mode: false\n{summary}\n\n{display_argv(safe_argv)}")
            self.app.push_screen(ConfirmScreen(message), lambda approved: asyncio.create_task(self.execute(spec, parent_run_id)) if approved else None)
        else:
            await self.execute(spec, parent_run_id)

    async def execute(self, spec: CommandSpec, parent_run_id: int | None = None) -> None:
        out = self.query_one("#output", RichLog)
        out.clear()
        out.write(f"$ {display_argv(self.shell.client.redactor.argv(build_argv(spec, self.shell.settings)))}")
        async def log(level: str, line: str) -> None:
            out.write(f"[{level}] {line}")
        try:
            run = await self.shell.execute(spec, log, parent_run_id=parent_run_id)
            out.write(f"Finished: {run.status} | exit={run.exit_code} | {run.duration_ms} ms | run #{run.id}")
            if run.stderr:
                out.write(run.stderr)
            if run.states:
                out.write(f"States: {len(run.states)}, changed: {run.changed}, failed: {run.failed}. Press r to inspect.")
                self.shell.current_run_id = run.id
                if spec.test and run.id:
                    self.last_test = (spec, run.id)
                    minion_count = len({state.minion for state in run.states})
                    out.write(f"Test summary: {minion_count} minions, {len(run.states)} states, {run.changed} planned changes, {run.failed} potential failures. Review, then choose Run For Real.")
            elif spec.async_run and run.jid and run.id:
                out.write(f"Salt job {run.jid} published. Opening live progress. Local cancellation does not terminate this remote job.")
                self.shell.open_live_run(run.id)
            elif run.stdout:
                out.write(run.stdout[:100000])
        except Exception as exc:
            out.write(f"Execution failed: {exc}")


class TableScreen(BaseScreen):
    columns: tuple[str, ...] = ()

    def content(self) -> ComposeResult:
        yield Input(placeholder="Search (Enter)", id="search")
        with Horizontal(id="split"):
            yield DataTable(id="table", cursor_type="row")
            with VerticalScroll(id="detail-scroll"):
                yield Static("Select a row", id="detail")

    async def on_mount(self) -> None:
        self.query_one("#table", DataTable).add_columns(*self.columns)
        await self.refresh_data()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        await self.refresh_data()

    async def refresh_data(self) -> None:
        pass

    def show_detail(self, text: str) -> None:
        self.query_one("#detail", Static).update(self.shell.client.redactor.text(text))


class HistoryScreen(TableScreen):
    title_text = "Run history — select a run to inspect its states"
    columns = ("Time", "Target", "Command", "Result", "Changed", "Failed", "Duration")
    BINDINGS = [("x", "compare", "Compare runs")]

    def __init__(self):
        super().__init__()
        self.baseline_id: int | None = None
        self.page_offset = 0

    def content(self) -> ComposeResult:
        with Horizontal(classes="toolbar"):
            yield Button("Previous page", id="previous")
            yield Button("Next page", id="next")
            yield Input(value="json", placeholder="json/yaml/csv/txt", id="export_format")
            yield Button("Export selected", id="export")
        yield from super().content()

    async def refresh_data(self) -> None:
        try:
            query = parse_history_query(self.query_one("#search", Input).value)
        except ValueError as exc:
            self.notify(str(exc), severity="error")
            return
        self.rows = {str(r["id"]): r for r in await self.shell.db.search_runs(query, limit=200, offset=self.page_offset)}
        table = self.query_one("#table", DataTable); table.clear()
        for key, r in self.rows.items():
            table.add_row(r["started_at"][:19], r["target_expression"] or "", r["command_type"] or "", r["status"],
                          str(r["changed"]), str(r["failed"]), f'{r["duration_ms"]/1000:.1f}s', key=key)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        self.page_offset = 0
        await self.refresh_data()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "export":
            table = self.query_one("#table", DataTable)
            if table.row_count == 0:
                self.notify("Select a run to export", severity="warning")
                return
            try:
                run_id = int(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)
                format = self.query_one("#export_format", Input).value.strip().lower()
                path = self.shell.settings.database.parent / "exports" / f"run-{run_id}.{format}"
                await export_run(self.shell.db, run_id, path, format)
                self.notify(f"Exported {path}", timeout=8)
            except Exception as exc:
                self.notify(str(exc), severity="error")
            return
        if event.button.id == "next" and len(self.rows) == 200:
            self.page_offset += 200
        elif event.button.id == "previous":
            self.page_offset = max(0, self.page_offset - 200)
        await self.refresh_data()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        r = self.rows.get(str(event.row_key.value))
        if r:
            self.show_detail(f"{r['command']}\n\nExit: {r['exit_code']}\nStatus: {r['status']}\n\n{r['stderr'] or r['stdout'] or ''}"[:100000])

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        row = self.rows.get(str(event.row_key.value))
        if row and row["status"] == "running" and row["jid"]:
            self.shell.open_live_run(int(event.row_key.value))
        else:
            self.shell.open_run(int(event.row_key.value))

    def action_compare(self) -> None:
        table = self.query_one("#table", DataTable)
        if table.row_count == 0:
            return
        try:
            current_id = int(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)
        except Exception:
            return
        if self.baseline_id is None:
            self.baseline_id = current_id
            self.notify(f"Baseline run #{current_id} selected. Highlight another run and press x.")
        else:
            baseline = self.baseline_id
            self.baseline_id = None
            asyncio.create_task(self._compare(baseline, current_id))

    async def _compare(self, baseline: int, current: int) -> None:
        rows = detailed_compare(await self.shell.db.states(baseline), await self.shell.db.states(current))
        lines = [f"Run #{baseline} → #{current}", ""]
        for item in rows:
            identity = item["identity"]
            old, new = item["previous"], item["current"]
            lines.append(f"{identity[0]}  {identity[2]}  {', '.join(item['categories'])}")
            if old and new:
                lines.append(f"  Result: {old['result']} → {new['result']} | Duration: {old['duration_ms']} → {new['duration_ms']} ms")
                if old["changes_json"] != new["changes_json"]:
                    lines.append(f"  Changes: {old['changes_json']} → {new['changes_json']}")
                if old["comment"] != new["comment"]:
                    lines.append(f"  Comment: {old['comment']} → {new['comment']}")
        self.show_detail("\n".join(lines) if rows else "Neither run has state results")


class StatesScreen(TableScreen):
    title_text = "State results — f/F jumps between failures"
    columns = ("Status", "Minion", "State ID", "Module", "Function", "Duration")
    BINDINGS = [("f", "next_failure", "Next failure"), ("shift+f", "previous_failure", "Previous failure"),
                ("o", "open_source", "Open source")]

    async def refresh_data(self) -> None:
        run_id = self.shell.current_run_id
        rows = await self.shell.db.states(run_id) if run_id else []
        term = self.query_one("#search", Input).value.lower()
        self.rows = {str(r["id"]): r for r in rows if term in (r["state_id"] + r["minion_id"] + (r["sls"] or "") + (r["comment"] or "")).lower()}
        table = self.query_one("#table", DataTable); table.clear()
        for key, r in self.rows.items():
            icon = "✗ FAIL" if r["result"] == 0 else "✓ PASS" if r["result"] == 1 else "? TEST"
            table.add_row(icon, r["minion_id"], r["state_id"], r["state_module"] or "", r["state_function"] or "", str(r["duration_ms"] or ""), key=key)
        if not rows:
            self.show_detail("No state results. Run state.apply or state.highstate from Command runner.")

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        r = self.rows.get(str(event.row_key.value))
        if r:
            self.show_detail(f"Minion: {r['minion_id']}\nSLS: {r['sls']}\nID: {r['state_id']}\nName: {r['name']}\nResult: {r['result']}\nDuration: {r['duration_ms']} ms\nStart: {r['started_at']}\n\nComment:\n{r['comment']}\n\nChanges:\n{pretty(json.loads(r['changes_json']))}\n\nRaw:\n{pretty(json.loads(r['raw_result_json']))}")

    def action_next_failure(self) -> None:
        self._jump_failure(1)

    def action_previous_failure(self) -> None:
        self._jump_failure(-1)

    def action_open_source(self) -> None:
        table = self.query_one("#table", DataTable)
        if table.row_count == 0:
            return
        try:
            key = str(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)
        except Exception:
            return
        row = self.rows.get(key)
        if row and row["sls"]:
            self.shell.open_source(row["sls"], row["state_id"])
        else:
            self.notify("This state has no SLS origin")

    def _jump_failure(self, direction: int) -> None:
        table = self.query_one("#table", DataTable)
        keys = list(self.rows)
        if not keys:
            return
        start = table.cursor_row or 0
        for step in range(1, len(keys)+1):
            index = (start + direction*step) % len(keys)
            if self.rows[keys[index]]["result"] == 0:
                table.move_cursor(row=index)
                return
        self.notify("No failures in this run")


class FailuresScreen(TableScreen):
    title_text = "Failures — select to open the original run"
    columns = ("Time", "Minion", "SLS", "State ID", "Error", "Run")

    async def refresh_data(self) -> None:
        self.rows = {str(r["id"]): r for r in await self.shell.db.failures(self.query_one("#search", Input).value)}
        table = self.query_one("#table", DataTable); table.clear()
        for key, r in self.rows.items():
            table.add_row(r["created_at"][:19], r["minion_id"] or "", r["sls"] or "", r["state_id"] or "", (r["error_text"] or "")[:80], str(r["run_id"]), key=key)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        r = self.rows.get(str(event.row_key.value))
        if r: self.show_detail(r["error_text"] or "")

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        r = self.rows.get(str(event.row_key.value))
        if r: self.shell.open_run(r["run_id"])


class LogsScreen(TableScreen):
    title_text = "Logs — searchable persisted command output"
    columns = ("Time", "Level", "Run", "Message")

    async def refresh_data(self) -> None:
        rows = await self.shell.db.logs(self.query_one("#search", Input).value)
        self.rows = {str(r["id"]): r for r in rows}
        table = self.query_one("#table", DataTable); table.clear()
        for key, r in self.rows.items():
            table.add_row(r["timestamp"][:19], r["level"], str(r["run_id"]), r["message"][:160], key=key)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        r = self.rows.get(str(event.row_key.value))
        if r: self.show_detail(r["message"])


class MinionsScreen(TableScreen):
    title_text = "Minions — local history; Ctrl+R probes live reachability"
    columns = ("Minion ID", "Last response", "Last result")

    async def refresh_data(self) -> None:
        self.rows = {r["minion_id"]: r for r in await self.shell.db.minions(self.query_one("#search", Input).value)}
        table = self.query_one("#table", DataTable); table.clear()
        for key, r in self.rows.items():
            table.add_row(key, (r["last_seen"] or "")[:19], r["last_status"] or "", key=key)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        key = str(event.row_key.value)
        self.shell.open_command(f"salt {key!r} grains.items")


class SlsScreen(BaseScreen):
    title_text = "SLS explorer — source on disk; compiled views come from Salt"

    def content(self) -> ComposeResult:
        with Horizontal(classes="toolbar"):
            yield Input(value=self.shell.settings.default_saltenv, id="env", placeholder="saltenv")
            yield Input(value=(self.shell.current_sls or "").replace(".", "/"), placeholder="Filter files", id="filter")
            yield Button("Reload", id="reload")
            yield Button("show_sls", id="high")
            yield Button("show_low_sls", id="low")
            yield Button("Dependencies", id="deps")
        with Horizontal(id="split"):
            yield DataTable(id="files", cursor_type="row")
            with VerticalScroll(id="detail-scroll"):
                yield Static("Select an SLS file", id="source")

    async def on_mount(self) -> None:
        self.query_one("#files", DataTable).add_columns("Path", "SLS")
        await self.refresh_data()
        self.query_one("#files", DataTable).focus()

    async def refresh_data(self) -> None:
        env = self.query_one("#env", Input).value
        term = self.query_one("#filter", Input).value.lower()
        self.paths = {str(p): (rel, p) for rel, p in await asyncio.to_thread(files, self.shell.settings, env) if term in rel.lower()}
        table = self.query_one("#files", DataTable); table.clear()
        for key, (rel, _) in self.paths.items():
            table.add_row(rel, sls_name(rel) or "", key=key)
        if not self.paths:
            self.query_one("#source", Static).update(f"No files found for {env}. Configure file_roots in config.toml.")

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        await self.refresh_data()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "reload":
            await self.refresh_data(); return
        if event.button.id in {"high", "low", "deps"}:
            await self.compiled(event.button.id)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        entry = self.paths.get(str(event.row_key.value))
        if not entry: return
        rel, path = entry
        self.shell.current_sls = sls_name(rel)
        try:
            text = self.shell.client.redactor.text(path.read_text(errors="replace"))
            lang = SaltSlsLexer() if path.suffix == ".sls" else "jinja" if path.suffix in {".jinja", ".j2"} else "yaml" if path.suffix in {".yaml", ".yml"} else "json" if path.suffix == ".json" else "text"
            self.query_one("#source", Static).update(Syntax(text[:250000], lang, line_numbers=True, word_wrap=False))
        except OSError as exc:
            self.query_one("#source", Static).update(str(exc))

    async def compiled(self, mode: str) -> None:
        sls = self.shell.current_sls
        if not sls:
            self.notify("Select an SLS file", severity="warning"); return
        fn = "state.show_low_sls" if mode in {"low", "deps"} else "state.show_sls"
        local = self.shell.capabilities is None or self.shell.capabilities.executables.get("salt-call", False)
        spec = CommandSpec(executable="salt-call" if local else "salt", function=fn,
                           target="local" if local else self.shell.settings.default_target,
                           arguments=[sls], saltenv=self.query_one("#env", Input).value)
        self.query_one("#source", Static).update("Asking Salt to compile…")
        run = await self.shell.execute(spec)
        if mode == "deps":
            graph = StateGraph.from_low(run.parsed)
            self.shell.current_graph = graph
            self.shell.switch_screen("graph")
            self.shell.screen.call_after_refresh(self.shell.screen.refresh_graph)
            return
        else:
            content = pretty(run.parsed)
        self.query_one("#source", Static).update(content[:250000] + (f"\n\n{run.stderr}" if run.stderr else ""))

    async def select_source(self, sls: str) -> None:
        self.query_one("#filter", Input).value = sls.replace(".", "/")
        await self.refresh_data()
        table = self.query_one("#files", DataTable)
        for index, (_, (relative, _)) in enumerate(self.paths.items()):
            if sls_name(relative) == sls:
                table.move_cursor(row=index)
                return
        self.notify(f"Source file for {sls} not found in configured roots", severity="warning")


class JobsScreen(BaseScreen):
    title_text = "Jobs — active and cached Salt jobs"

    def content(self) -> ComposeResult:
        with Horizontal(classes="toolbar"):
            yield Button("Active", id="active")
            yield Button("Recent jobs", id="list")
            yield Input(placeholder="JID for lookup", id="jid")
            yield Button("Lookup JID", id="lookup")
            yield Button("Find job", id="find")
            yield Button("Remote kill", id="kill", variant="error")
        with Horizontal(id="split"):
            yield DataTable(id="job_table", cursor_type="row")
            with VerticalScroll(id="detail-scroll"):
                yield Static("Select a job", id="job_detail")

    async def on_mount(self) -> None:
        self.query_one("#job_table", DataTable).add_columns("JID", "Function", "Target", "Returned", "Running")
        self.rows = {}
        await self.run_job("jobs.active")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        match event.button.id:
            case "active": await self.run_job("jobs.active")
            case "list": await self.run_job("jobs.list_jobs")
            case "lookup": await self.run_job("jobs.lookup_jid", [self.query_one("#jid", Input).value])
            case "find": self._open_job_command("saltutil.find_job")
            case "kill": self._open_job_command("saltutil.kill_job")

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        job = self.rows.get(str(event.row_key.value))
        if job:
            self.query_one("#jid", Input).value = job.jid
            self.query_one("#job_detail", Static).update(pretty(job.raw))

    def _open_job_command(self, function: str) -> None:
        jid = self.query_one("#jid", Input).value.strip()
        job = self.rows.get(jid)
        if not jid or not jid.isdigit():
            self.notify("Select a job or enter a numeric JID", severity="warning")
            return
        if function == "saltutil.kill_job":
            if not job or not job.running:
                self.notify("Remote kill requires known running minions from jobs.active", severity="warning")
                return
            spec = CommandSpec(function=function, target=",".join(job.running), target_type="list", arguments=[jid])
        else:
            spec = CommandSpec(function=function, target=job.target if job and job.target else self.shell.settings.default_target,
                               target_type=job.target_type if job and job.target_type in {"glob", "grain", "pillar", "compound", "nodegroup", "list", "pcre"} else "glob",
                               arguments=[jid])
        self.shell.open_command(display_argv(build_argv(spec, self.shell.settings)))

    async def run_job(self, fn: str, args: list[str] | None = None) -> None:
        run = await self.shell.execute(CommandSpec(executable="salt-run", function=fn, arguments=args or []))
        jobs = parse_jobs(run.parsed)
        table = self.query_one("#job_table", DataTable); table.clear()
        self.rows = {job.jid: job for job in jobs}
        for job in jobs:
            table.add_row(job.jid, job.function, job.target, str(len(job.returned)), str(len(job.running)), key=job.jid)
        self.query_one("#job_detail", Static).update(f"{run.command}\n\n{run.stderr or pretty(run.parsed)}"[:100000])
