from __future__ import annotations

import asyncio
import json
import re
import shlex
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, RichLog, Static

from salt_tui.models import CommandSpec
from salt_tui.salt.commands import build_argv, display_argv, is_mutating
from salt_tui.ui.screens import ConfirmScreen

if TYPE_CHECKING:
    from salt_tui.app import SaltTUI


class RunnerScreen(Screen):
    @property
    def shell(self) -> SaltTUI:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Salt runners — discover with salt-run -d or enter any runner function", classes="page-title")
        with Horizontal(classes="toolbar"):
            yield Input(placeholder="Runner function, e.g. jobs.active", id="runner_fun")
            yield Input(placeholder="Arguments", id="runner_args")
            yield Button("Discover", id="discover")
            yield Button("Run", id="runner_run", variant="primary")
            yield Button("Copy", id="runner_copy")
        yield Static("", id="runner_preview")
        with Horizontal(id="split"):
            yield DataTable(id="runner_functions")
            yield RichLog(id="runner_output", wrap=True, max_lines=2000)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#runner_functions", DataTable).add_columns("Discovered runner function")
        self._preview()

    def _spec(self) -> CommandSpec:
        function = self.query_one("#runner_fun", Input).value.strip()
        if not function:
            raise ValueError("Enter a runner function")
        return CommandSpec(executable="salt-run", function=function,
                           arguments=shlex.split(self.query_one("#runner_args", Input).value))

    def _preview(self) -> None:
        try:
            command = display_argv(self.shell.client.redactor.argv(build_argv(self._spec(), self.shell.settings)))
        except ValueError as exc:
            command = str(exc)
        self.query_one("#runner_preview", Static).update("Exact command: " + command)

    def on_input_changed(self, event: Input.Changed) -> None:
        self._preview()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.query_one("#runner_fun", Input).value = str(event.row_key.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "discover":
            asyncio.create_task(self.discover())
        elif event.button.id == "runner_run":
            self.start()
        elif event.button.id == "runner_copy":
            try:
                self.app.copy_to_clipboard(display_argv(self.shell.client.redactor.argv(build_argv(self._spec(), self.shell.settings))))
                self.notify("Redacted runner command copied")
            except ValueError as exc:
                self.notify(str(exc), severity="error")

    async def discover(self) -> None:
        log = self.query_one("#runner_output", RichLog)
        log.write("Discovering runner documentation via salt-run -d…")
        run = await self.shell.execute(CommandSpec(executable="salt-run", function="-d"))
        functions = sorted(set(re.findall(r"(?m)^\s*([A-Za-z_]\w*(?:\.[A-Za-z_]\w+)+):?\s*$", run.stdout)))
        table = self.query_one("#runner_functions", DataTable); table.clear()
        for function in functions[:5000]:
            table.add_row(function, key=function)
        log.write(f"Found {len(functions)} documented functions" if functions else (run.stderr or "No runner names could be parsed; enter a function manually."))

    def start(self) -> None:
        try:
            spec = self._spec()
            command = display_argv(self.shell.client.redactor.argv(build_argv(spec, self.shell.settings)))
        except ValueError as exc:
            self.notify(str(exc), severity="error"); return
        if self.shell.settings.confirm_changes and is_mutating(spec):
            self.app.push_screen(ConfirmScreen(f"Runner action: {spec.function}\n\n{command}"),
                                 lambda approved: asyncio.create_task(self.execute(spec)) if approved else None)
        else:
            asyncio.create_task(self.execute(spec))

    async def execute(self, spec: CommandSpec) -> None:
        log = self.query_one("#runner_output", RichLog); log.clear()
        run = await self.shell.execute(spec)
        log.write(f"{run.command}\nStatus: {run.status} | run #{run.id}\n")
        log.write(json.dumps(run.parsed, indent=2, ensure_ascii=False, default=str)[:100000])
        if run.stderr: log.write(run.stderr)


class OrchestrationScreen(Screen):
    @property
    def shell(self) -> SaltTUI:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Orchestration — salt-run state.orchestrate", classes="page-title")
        with Horizontal(classes="toolbar"):
            yield Input(placeholder="Orchestration SLS", id="orch_sls")
            yield Input(value=self.shell.settings.default_saltenv, placeholder="saltenv", id="orch_env")
            yield Input(placeholder="pillarenv (optional)", id="orch_pillarenv")
            yield Button("Test", id="orch_test")
            yield Button("Run", id="orch_run", variant="warning")
        yield Static("", id="orch_preview")
        yield DataTable(id="orch_states")
        yield RichLog(id="orch_output", wrap=True, max_lines=2000)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#orch_states", DataTable).add_columns("Status", "Step", "Module.Function", "Duration")
        self._preview()

    def _spec(self, test: bool) -> CommandSpec:
        sls = self.query_one("#orch_sls", Input).value.strip()
        if not sls:
            raise ValueError("Enter an orchestration SLS")
        return CommandSpec(executable="salt-run", function="state.orchestrate", arguments=[sls],
                           saltenv=self.query_one("#orch_env", Input).value.strip() or None,
                           pillarenv=self.query_one("#orch_pillarenv", Input).value.strip() or None, test=test)

    def _preview(self) -> None:
        try:
            command = display_argv(self.shell.client.redactor.argv(build_argv(self._spec(False), self.shell.settings)))
        except ValueError as exc:
            command = str(exc)
        self.query_one("#orch_preview", Static).update("Exact command: " + command)

    def on_input_changed(self, event: Input.Changed) -> None:
        self._preview()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        test = event.button.id == "orch_test"
        try:
            spec = self._spec(test)
            command = display_argv(self.shell.client.redactor.argv(build_argv(spec, self.shell.settings)))
        except ValueError as exc:
            self.notify(str(exc), severity="error"); return
        if not test and self.shell.settings.confirm_changes:
            self.app.push_screen(ConfirmScreen(f"Orchestration: {spec.arguments[0]}\nEnvironment: {spec.saltenv}\nTest mode: false\n\n{command}"),
                                 lambda approved: asyncio.create_task(self.execute(spec)) if approved else None)
        else:
            asyncio.create_task(self.execute(spec))

    async def execute(self, spec: CommandSpec) -> None:
        log = self.query_one("#orch_output", RichLog); log.clear()
        run = await self.shell.execute(spec)
        self.shell.current_run_id = run.id
        log.write(f"{run.command}\nStatus: {run.status} | run #{run.id} | JID: {run.jid or 'not exposed'}")
        if run.stderr: log.write(run.stderr)
        table = self.query_one("#orch_states", DataTable); table.clear()
        for state in run.states:
            table.add_row("FAIL" if state.result is False else "PASS" if state.result else "TEST",
                          state.state_id, f"{state.module}.{state.function}", f"{state.duration_ms or 0:.0f} ms")
        if run.id:
            children = await self.shell.db.child_jobs(run.id)
            for child in children:
                log.write(f"Child job {child['child_jid']}: {child['summary'] or ''} | returned: {child['returned']}")
        if not run.states:
            log.write(json.dumps(run.parsed, indent=2, ensure_ascii=False, default=str)[:100000])
