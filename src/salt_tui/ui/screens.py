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
from textual.widgets import Button, DataTable, Footer, Header, Input, LoadingIndicator, RichLog, Static

from salt_tui.models import CommandSpec
from salt_tui.salt.commands import build_argv, display_argv, is_mutating, parse_line
from salt_tui.salt.jobs import parse_jobs
from salt_tui.sls.explorer import available_states, files, local_tree_rows, sls_name
from salt_tui.sls.source_links import local_sls_paths
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


def format_state_changes(value: Any) -> str:
    if isinstance(value, dict) and isinstance(value.get("diff"), str):
        remainder = {key: item for key, item in value.items() if key != "diff"}
        return value["diff"] + ("\n\nOther changes:\n" + pretty(remainder) if remainder else "")
    return pretty(value)


class ConfirmScreen(ModalScreen[bool]):
    DEFAULT_CSS = """
    ConfirmScreen { align: center middle; background: $background 70%; }
    #confirm_box { width: 80; max-width: 95%; height: auto; padding: 2; border: heavy $warning; background: $surface; }
    #confirm_text { height: auto; margin-bottom: 1; }
    """

    def __init__(self, message: str, confirm_label: str = "Execute"):
        super().__init__()
        self.message = message
        self.confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm_box"):
            yield Static(self.message, id="confirm_text")
            with Horizontal():
                yield Button(self.confirm_label, id="yes", variant="warning")
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
            yield Input(placeholder="dashboard, minions, nodegroups, jobs, states, sls, history, logs, failures, settings, command", id="palette_input")
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
        yield Static(self.shell.breadcrumb_text(), classes="breadcrumb")
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
        if row:
            self.shell.open_live_run(int(event.row_key.value))


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
        if row:
            self.shell.open_live_run(int(event.row_key.value))

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
    title_text = "Minions — Enter details · Space selects · Ctrl+R probes live reachability"
    columns = ("Minion ID", "Last response", "Last result")
    BINDINGS = [("space", "toggle_selection", "Toggle selection")]

    def content(self) -> ComposeResult:
        with Horizontal(classes="toolbar"):
            yield Button("Use selected", id="minions_use", variant="primary")
            yield Button("Save selection", id="minions_save")
            yield Button("Select visible", id="minions_select_visible")
            yield Button("Clear selection", id="minions_clear")
        yield from super().content()

    async def refresh_data(self) -> None:
        self.rows = {r["minion_id"]: r for r in await self.shell.db.minions(self.query_one("#search", Input).value)}
        table = self.query_one("#table", DataTable); table.clear()
        for key, r in self.rows.items():
            table.add_row(key, (r["last_seen"] or "")[:19], r["last_status"] or "", key=key)

    def _selected_id(self) -> str | None:
        table = self.query_one("#table", DataTable)
        if not table.row_count:
            return None
        try:
            return str(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)
        except Exception:
            return None

    def action_toggle_selection(self) -> None:
        minion_id = self._selected_id()
        if not minion_id:
            return
        if minion_id in self.shell.selected_minions:
            self.shell.selected_minions.remove(minion_id)
            self.notify(f"Removed {minion_id}; {len(self.shell.selected_minions)} selected")
        else:
            self.shell.selected_minions.add(minion_id)
            self.notify(f"Selected {minion_id}; {len(self.shell.selected_minions)} selected")
        self._refresh_selection_detail()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "minions_use":
            self.shell.use_selected_minions()
        elif event.button.id == "minions_save":
            self.shell.save_selected_minions()
        elif event.button.id == "minions_select_visible":
            self.shell.selected_minions.update(self.rows)
            self.notify(f"Selected {len(self.shell.selected_minions)} minions")
            self._refresh_selection_detail()
        elif event.button.id == "minions_clear":
            self.shell.selected_minions.clear()
            self.notify("Temporary selection cleared")
            self._refresh_selection_detail()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._show_minion_detail(str(event.row_key.value))

    def _refresh_selection_detail(self) -> None:
        minion_id = self._selected_id()
        if minion_id:
            self._show_minion_detail(minion_id)

    def _show_minion_detail(self, minion_id: str) -> None:
        row = self.rows.get(minion_id, {})
        selected = sorted(self.shell.selected_minions)
        sample = ", ".join(selected[:10]) + (f" +{len(selected) - 10} more" if len(selected) > 10 else "")
        self.show_detail(f"Minion: {minion_id}\nLast response: {row.get('last_seen') or 'unknown'}\n"
                         f"Last result: {row.get('last_status') or 'unknown'}\n\n"
                         f"Temporary selection ({len(selected)}): {sample or 'none'}\n\n"
                         "Enter: inspect details · Space: toggle selection")

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        key = str(event.row_key.value)
        self.shell.open_minion_detail(key)


class SlsScreen(BaseScreen):
    title_text = "SLS explorer — local source and states available from Salt"

    def content(self) -> ComposeResult:
        yield Static("↑/↓ Select state  ·  Shift+Tab Choose action  ·  Enter Activate  ·  Esc Back", id="sls-hint")
        with Horizontal(classes="toolbar"):
            yield Button("Local files", id="local_mode", variant="primary")
            yield Button("From Salt", id="salt_mode")
            yield Input(value=self.shell.settings.default_saltenv, id="env", placeholder="saltenv")
            yield Input(value=(self.shell.current_sls or "").replace(".", "/"), placeholder="Filter states", id="filter")
            yield Input(value="local", id="state_target", placeholder="local or Salt target")
            yield Input(value="glob", id="state_target_type", placeholder="target type", classes="target-type")
        yield Static("Inputs: env · filter · target (local/minions) · type", id="sls-fields")
        with Horizontal(classes="toolbar"):
            yield Button("Reload", id="reload")
            yield Button("Test state", id="test_state", variant="primary")
            yield Button("Apply state", id="apply_state", variant="warning")
            yield Button("Rendered state", id="high")
            yield Button("Execution steps", id="low")
            yield Button("Dependencies", id="deps")
        yield Static("Rendered state: state.show_sls  ·  Execution steps: state.show_low_sls", id="sls-functions")
        yield Static("", id="sls-status")
        yield LoadingIndicator(id="sls-loading")
        yield Button("Open Settings to configure file_roots", id="roots_help")
        with Horizontal(id="split"):
            yield DataTable(id="files", cursor_type="row")
            with Vertical(id="sls-detail"):
                yield Static("Source · Select a file", id="source-title")
                yield Static("", id="source-origin")
                with VerticalScroll(id="detail-scroll"):
                    yield Static("Select an SLS file", id="source")

    async def on_mount(self) -> None:
        self.source_mode = "local"
        self.rows: dict[str, tuple[str | None, Path | None, str | None]] = {}
        self.paths: dict[str, tuple[str, Path]] = {}
        self.query_one("#roots_help", Button).display = False
        self.query_one("#files", DataTable).add_columns("State tree", "SLS", "Origin")
        await self.refresh_data()
        self.query_one("#files", DataTable).focus()
        self._set_compact_labels(self.size.width < 100)

    def on_resize(self, event) -> None:
        self._set_compact_labels(event.size.width < 100)

    def _set_compact_labels(self, compact: bool) -> None:
        labels = ({"test_state": "Test", "apply_state": "Apply", "high": "Rendered",
                   "low": "Low steps", "deps": "Deps"} if compact else
                  {"test_state": "Test state", "apply_state": "Apply state", "high": "Rendered state",
                   "low": "Execution steps", "deps": "Dependencies"})
        for button_id, label in labels.items():
            self.query_one(f"#{button_id}", Button).label = label

    async def refresh_data(self) -> None:
        env = self.query_one("#env", Input).value.strip() or self.shell.settings.default_saltenv
        term = self.query_one("#filter", Input).value.lower()
        target = self.query_one("#state_target", Input).value.strip()
        previous = self.shell.current_sls
        self.rows = {}
        self.paths = {}
        table = self.query_one("#files", DataTable)
        table.clear()
        self.shell.current_sls = None
        self._set_actions_enabled(False)
        roots_help = self.query_one("#roots_help", Button)
        roots_help.display = False
        if self.source_mode == "local":
            found = [(rel, path) for rel, path in await asyncio.to_thread(files, self.shell.settings, env)
                     if term in rel.lower() or term in (sls_name(rel) or "").lower()]
            relative_by_path = {path: rel for rel, path in found}
            for key, label, sls, path in local_tree_rows(found):
                relative = relative_by_path.get(path) if path else None
                self.rows[key] = (relative, path, sls)
                if path and relative:
                    self.paths[key] = (relative, path)
                table.add_row(label, sls or "", "Local" if path else "", key=key)
            if not found:
                roots_help.display = True
                self.query_one("#source", Static).update(
                    f"No local files found for {env}. Configure [file_roots] in Settings, or choose From Salt.")
                self.query_one("#source-title", Static).update("Source · No local files")
                self.query_one("#source-origin", Static).update("Local [file_roots] is empty or not accessible")
                self.query_one("#sls-status", Static).update(f"Local roots: {', '.join(map(str, self.shell.settings.file_roots.get(env, []))) or 'none'}")
            else:
                self.query_one("#sls-status", Static).update(f"Local files · {len(found)} files · environment {env}")
        else:
            if not target:
                self.query_one("#sls-status", Static).update("Enter local or a Salt target before loading states")
                return
            spec = self._selected_spec("cp.list_states", [], env, target)
            self.query_one("#sls-status", Static).update(f"Asking Salt for states on {target}…")
            self.query_one("#sls-loading", LoadingIndicator).display = True
            try:
                run = await self.shell.execute(spec)
                if run.status == "failed":
                    raise RuntimeError(run.stderr or run.stdout or "Salt could not list states")
                if run.parsed and not (isinstance(run.parsed, list) or
                                       isinstance(run.parsed, dict) and any(isinstance(value, list) for value in run.parsed.values())):
                    raise RuntimeError(pretty(run.parsed))
                counts = available_states(run.parsed)
                for sls, count in counts.items():
                    if term and term not in sls.lower():
                        continue
                    key = f"salt:{sls}"
                    self.rows[key] = (None, self._local_path(env, sls), sls)
                    table.add_row(sls.replace(".", "/"), sls, f"Salt · {count} minion{'s' if count != 1 else ''}", key=key)
                self.query_one("#sls-status", Static).update(f"Salt reports {len(counts)} states for {target} · environment {env}")
                if not counts:
                    self.query_one("#source-title", Static).update("Salt · No states")
                    self.query_one("#source-origin", Static).update(f"Salt target: {target} · environment: {env}")
                    self.query_one("#source", Static).update("Salt returned no available states for this target and environment.")
            except Exception as exc:
                message = self.shell.client.redactor.text(str(exc))
                self.query_one("#sls-status", Static).update("Could not list states from Salt")
                self.query_one("#source", Static).update(message[:100000])
                self.notify(message[:180], severity="error")
            finally:
                self.query_one("#sls-loading", LoadingIndicator).display = False
        keys = list(self.rows)
        selected = next((i for i, key in enumerate(keys) if self.rows[key][2] == previous and self.rows[key][2]), None)
        if selected is None:
            selected = next((i for i, key in enumerate(keys) if self.rows[key][2]), None)
        if selected is not None:
            table.move_cursor(row=selected)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        await self.refresh_data()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id in {"local_mode", "salt_mode"}:
            self.source_mode = "local" if event.button.id == "local_mode" else "salt"
            self.query_one("#local_mode", Button).variant = "primary" if self.source_mode == "local" else "default"
            self.query_one("#salt_mode", Button).variant = "primary" if self.source_mode == "salt" else "default"
            await self.refresh_data(); return
        if event.button.id == "roots_help":
            self.shell.show_screen("settings"); return
        if event.button.id == "reload":
            await self.refresh_data(); return
        if event.button.id in {"test_state", "apply_state"}:
            await self.run_state(test=event.button.id == "test_state"); return
        if event.button.id in {"high", "low", "deps"}:
            await self.compiled(event.button.id)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        entry = self.rows.get(str(event.row_key.value))
        if not entry: return
        rel, path, sls = entry
        self.shell.current_sls = sls
        self._set_actions_enabled(sls is not None)
        if not sls:
            self.query_one("#source-title", Static).update(f"Local directory · {str(event.row_key.value).split(':')[-1]}")
            self.query_one("#source-origin", Static).update("Choose a file in the tree")
            self.query_one("#sls-status", Static).update("Local directory selected")
            self.query_one("#source", Static).update("Choose an SLS file below this directory.")
            return
        self.query_one("#source-title", Static).update(f"Source · {rel or sls}")
        self.query_one("#sls-status", Static).update(f"Selected {sls} · {'Local file' if rel else 'Reported by Salt'}")
        origin = self.query_one("#source-origin", Static)
        origin.update(
            f"Local path: {path}" if self.source_mode == "local" and path else
            f"Salt target: {self.query_one('#state_target', Input).value.strip()} · "
            + (f"matching local source: {path}" if path else "no matching local source"))
        origin.tooltip = str(path) if path else "Salt reports this state; no matching local source was found"
        if path is None:
            self.query_one("#source", Static).update(
                f"Salt reports {sls}, but no matching local source exists under configured file_roots.\n"
                "Compiled views and state execution remain available.")
            return
        try:
            text = self.shell.client.redactor.text(path.read_text(errors="replace"))
            lang = SaltSlsLexer() if path.suffix == ".sls" else "jinja" if path.suffix in {".jinja", ".j2"} else "yaml" if path.suffix in {".yaml", ".yml"} else "json" if path.suffix == ".json" else "text"
            self.query_one("#source", Static).update(Syntax(text[:250000], lang, line_numbers=True, word_wrap=False))
        except OSError as exc:
            self.query_one("#source", Static).update(str(exc))

    def _local_path(self, env: str, sls: str) -> Path | None:
        return next(iter(local_sls_paths(self.shell.settings, env, sls)), None)

    def _selected_spec(self, function: str, arguments: list[str], env: str, target: str, *, test: bool = False) -> CommandSpec:
        if not target:
            raise ValueError("Enter local or a Salt target")
        target_type = self.query_one("#state_target_type", Input).value.strip().lower() or "glob"
        if target_type not in {"glob", "grain", "pillar", "compound", "nodegroup", "list", "pcre"}:
            raise ValueError("Target type must be glob, grain, pillar, compound, nodegroup, list, or pcre")
        return CommandSpec(executable="salt-call" if target == "local" else "salt", function=function,
                           target=target, target_type=target_type if target != "local" else "glob",
                           arguments=arguments, saltenv=env, test=test)

    async def run_state(self, *, test: bool) -> None:
        sls = self.shell.current_sls
        if not sls:
            self.notify("Select an SLS state", severity="warning"); return
        try:
            spec = self._selected_spec("state.apply", [sls], self.query_one("#env", Input).value.strip(),
                                       self.query_one("#state_target", Input).value.strip(), test=test)
            status = self.query_one("#sls-status", Static)
            loading = self.query_one("#sls-loading", LoadingIndicator)
            status.update(f"{'Testing' if test else 'Preparing to apply'} {sls} on {spec.target}…")
            loading.display = True
            self._set_actions_enabled(False)
            await self.shell.start_state_workflow(spec)
            status.update(f"{'Dry run complete' if test else 'Awaiting confirmation'} for {sls}")
        except Exception as exc:
            self.notify(self.shell.client.redactor.text(str(exc))[:180], severity="error")
        finally:
            self.query_one("#sls-loading", LoadingIndicator).display = False
            self._set_actions_enabled(self.shell.current_sls is not None)

    async def compiled(self, mode: str) -> None:
        sls = self.shell.current_sls
        if not sls:
            self.notify("Select an SLS file", severity="warning"); return
        fn = "state.show_low_sls" if mode in {"low", "deps"} else "state.show_sls"
        label = "Dependencies" if mode == "deps" else "Execution steps" if mode == "low" else "Rendered state"
        try:
            spec = self._selected_spec(fn, [sls], self.query_one("#env", Input).value.strip(),
                                       self.query_one("#state_target", Input).value.strip())
        except ValueError as exc:
            self.notify(str(exc), severity="error"); return
        status = self.query_one("#sls-status", Static)
        loading = self.query_one("#sls-loading", LoadingIndicator)
        source = self.query_one("#source", Static)
        title = self.query_one("#source-title", Static)
        status.update(f"Compiling {sls} with {fn}…")
        loading.display = True
        self._set_actions_enabled(False)
        try:
            run = await self.shell.execute(spec)
            if run.exit_code not in (None, 0) or run.status == "failed":
                detail = run.stderr or run.stdout or pretty(run.parsed) or "Salt did not return an explanation."
                raise RuntimeError(detail)
            if mode == "deps":
                graph = StateGraph.from_low(run.parsed)
                self.shell.current_graph = graph
                status.update(f"Dependencies ready for {sls}  ·  {fn}")
                self.shell.show_screen("graph")
                self.shell.screen.call_after_refresh(self.shell.screen.refresh_graph)
                return
            title.update(f"{label} · {sls}")
            source.update(pretty(run.parsed)[:250000] + (f"\n\n{run.stderr}" if run.stderr else ""))
            status.update(f"{label} ready for {sls}  ·  {fn}")
        except Exception as exc:
            title.update(f"Compilation failed · {sls}")
            message = self.shell.client.redactor.text(str(exc))
            source.update(message[:250000])
            status.update(f"Could not compile {sls}  ·  {fn}")
            self.notify(f"Compilation failed: {message[:180]}", severity="error", timeout=8)
        finally:
            loading.display = False
            self._set_actions_enabled(self.shell.current_sls is not None)

    def _set_actions_enabled(self, enabled: bool) -> None:
        for button_id in ("high", "low", "deps", "test_state", "apply_state"):
            self.query_one(f"#{button_id}", Button).disabled = not enabled

    async def select_source(self, sls: str) -> None:
        self.query_one("#filter", Input).value = sls.replace(".", "/")
        await self.refresh_data()
        table = self.query_one("#files", DataTable)
        for index, entry in enumerate(self.rows.values()):
            if entry[2] == sls:
                table.move_cursor(row=index)
                return
        self.notify(f"Source file for {sls} not found in configured roots", severity="warning")


class StateReviewScreen(BaseScreen):
    title_text = "Dry-run review — inspect planned changes before applying"

    def content(self) -> ComposeResult:
        yield Static("", id="review_summary")
        yield Static("↑/↓ Inspect planned changes · Shift+Tab Choose action · Enter Activate · Esc Back", id="review_hint")
        with Horizontal(classes="toolbar"):
            yield Button("Apply this state", id="review_apply", variant="warning")
            yield Button("Run tracker", id="review_tracker")
            yield Button("State results", id="review_results")
            yield Button("Open source", id="review_source")
        with Horizontal(id="split"):
            yield DataTable(id="review_table", cursor_type="row")
            with VerticalScroll(id="detail-scroll"):
                yield Static("Select a result", id="review_detail")

    async def on_mount(self) -> None:
        self.query_one("#review_table", DataTable).add_columns("Result", "Minion", "State ID", "Change", "Comment")
        await self.refresh_data()
        self.query_one("#review_table", DataTable).focus()

    async def refresh_data(self) -> None:
        run_id = self.shell.review_run_id
        if run_id is None:
            self.query_one("#review_summary", Static).update("No dry run selected. Test a state from SLS Explorer.")
            self.query_one("#review_apply", Button).disabled = True
            return
        run = await self.shell.db.run(run_id)
        rows = await self.shell.db.states(run_id)
        self.rows = {str(row["id"]): row for row in rows}
        table = self.query_one("#review_table", DataTable)
        table.clear()
        planned = failed = 0
        preferred: int | None = None
        for index, (key, row) in enumerate(self.rows.items()):
            changes = json.loads(row["changes_json"])
            changed = bool(changes)
            planned += int(changed and row["result"] is None)
            failed += int(row["result"] == 0)
            if row["result"] == 0 or (preferred is None and changed and row["result"] is None):
                preferred = index
            result = "FAIL" if row["result"] == 0 else "PLAN" if row["result"] is None else "PASS"
            table.add_row(result, row["minion_id"], row["state_id"], "Yes" if changed else "No",
                          (row["comment"] or "")[:80], key=key)
        if self.rows:
            table.move_cursor(row=preferred or 0)
        if run:
            self.query_one("#review_summary", Static).update(
                f"Run #{run_id} · {run['status'].upper()} · target {run['target_expression']} · "
                f"environment {run['saltenv'] or 'base'} · {len(rows)} states · "
                f"{planned} planned changes · {failed} failures")
            if not rows:
                self.query_one("#review_detail", Static).update(run["stderr"] or run["stdout"] or "Salt returned no state results.")
        self.query_one("#review_apply", Button).disabled = self.shell.review_spec is None

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        row = self.rows.get(str(event.row_key.value))
        if row:
            self.query_one("#review_detail", Static).update(
                f"{row['minion_id']} · {row['sls']} · {row['state_id']}\n"
                f"Result: {'failed' if row['result'] == 0 else 'planned' if row['result'] is None else 'passed'}\n\n"
                f"{row['comment']}\n\nChanges:\n{format_state_changes(json.loads(row['changes_json']))}")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "review_apply" and self.shell.review_spec:
            await self.shell.start_state_workflow(replace(self.shell.review_spec, test=False))
        elif event.button.id == "review_tracker" and self.shell.review_run_id:
            self.shell.open_live_run(self.shell.review_run_id)
        elif event.button.id == "review_results" and self.shell.review_run_id:
            self.shell.open_run(self.shell.review_run_id)
        elif event.button.id == "review_source" and self.shell.review_spec:
            self.shell.open_source(self.shell.review_spec.arguments[0])


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
