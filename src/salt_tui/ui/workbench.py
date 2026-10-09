"""Task-oriented workbench screens and shared navigation."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import shlex
from rich.text import Text

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, DataTable, Input, Select, Static

from salt_tui.models import CommandSpec
from salt_tui.salt.commands import build_argv, copy_action, display_argv, execution_spec, key_action
from salt_tui.ui.screens import BaseScreen, ConfirmScreen, pretty


class TargetFields(Horizontal):
    """Reusable target expression and type controls."""

    def __init__(self, *, prefix: str, default_target: str, copy_only: bool = False):
        super().__init__(classes="toolbar")
        self.prefix = prefix
        self.default_target = default_target
        self.copy_only = copy_only

    def compose(self) -> ComposeResult:
        types = ("glob", "grain", "nodegroup", "list", "pcre") if self.copy_only else (
            "glob", "grain", "pillar", "compound", "nodegroup", "list", "pcre")
        yield Input(value=self.default_target, placeholder="Target", id=f"{self.prefix}target")
        yield Select([(name, name) for name in types], value="glob", id=f"{self.prefix}target_type")


class CommandPreview(Static):
    """Always render the same argv that the service will execute."""

    def show_spec(self, spec: CommandSpec) -> None:
        app = self.app
        safe = app.client.redactor.with_values(spec.secret_values).argv(build_argv(spec, app.settings))
        self.update(Text("Exact command: " + display_argv(safe)))


class WorkbenchSidebar(Vertical):
    """A shared, responsive navigation rail; keyboard bindings remain global."""

    def compose(self) -> ComposeResult:
        for heading, entries in (
            ("Home", [("Overview", "dashboard")]),
            ("Fleet", [("Minions", "minions"), ("Targets", "targets"), ("Nodegroups", "nodegroups"), ("Keys", "keys")]),
            ("States", [("SLS Explorer", "sls"), ("State results", "states")]),
            ("Run", [("Samples", "samples"), ("Functions", "functions"), ("File copy", "file_copy"), ("Command", "command"), ("Runners", "runner")]),
            ("Observe", [("History", "history"), ("Jobs", "jobs"), ("Events", "events")]),
            ("Settings", [("Configuration", "settings")]),
        ):
            yield Static(heading, classes="nav-heading")
            for label, name in entries:
                yield Button(label, id=f"nav-{name}", classes="nav-button", variant="primary" if self.app._current_screen_name == name else "default")

    def on_mount(self) -> None:
        self.display = self.app.size.width >= 100

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id and event.button.id.startswith("nav-"):
            event.stop()
            self.app.action_show(event.button.id[4:])


class WorkbenchContext(Static):
    def on_mount(self) -> None:
        self.refresh_context()

    def refresh_context(self) -> None:
        app = self.app
        context = getattr(app, "execution_context", "master")
        cap = getattr(app, "capabilities", None)
        binary = "salt" if context == "master" else "salt-call"
        available = "Salt ready" if cap and cap.executables.get(binary, False) else "Salt availability unknown" if cap is None else f"{binary} unavailable"
        self.update(f"{context.title()} · {app.settings.default_saltenv}    {available} · Events {app.events.status}    / Search actions")


class SamplesScreen(BaseScreen):
    """Bundled read-only Salt examples that use the user's installed CLI."""

    title_text = "Run › Samples — safe Salt calls against your selected context"

    def content(self) -> ComposeResult:
        yield Static("These examples call Salt directly and show the returned result. They do not change minion state.")
        with Horizontal(classes="toolbar"):
            yield Select([("Master", "master"), ("Local configured", "local"),
                          ("Local masterless", "masterless")],
                         value=self.shell.execution_context, id="sample_context")
            yield Button("Ping minions", id="sample_ping", variant="primary")
            yield Button("Salt version", id="sample_version")
            yield Button("OS grain", id="sample_os")
        yield TargetFields(prefix="sample_", default_target=self.shell.settings.default_target)
        yield Static("Master sends to the target above. Local uses the configured minion; masterless adds --local.",
                     id="sample_hint")
        yield CommandPreview("Choose an example to preview its Salt command", id="sample_preview")
        yield Static("", id="sample_result")

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id in {"sample_context", "sample_target_type"}:
            if event.select.id == "sample_context" and event.value in {"master", "local", "masterless"}:
                self.shell.execution_context = str(event.value)
                for context_bar in self.query("WorkbenchContext"):
                    context_bar.refresh_context()
            self.update_preview()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "sample_target":
            self.update_preview()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        function = {"sample_ping": "test.ping", "sample_version": "test.version",
                    "sample_os": "grains.item"}.get(event.button.id)
        if function:
            asyncio.create_task(self.run_sample(function))

    def _spec(self, function: str) -> CommandSpec:
        context = str(self.query_one("#sample_context", Select).value)
        target = self.query_one("#sample_target", Input).value.strip()
        if context == "master" and not target:
            raise ValueError("Enter a target for Master context")
        return execution_spec(self.shell.settings, function, target=target,
                              target_type=str(self.query_one("#sample_target_type", Select).value),
                              arguments=["os"] if function == "grains.item" else [], context=context)

    def update_preview(self) -> None:
        try:
            spec = self._spec("test.ping")
            self.query_one("#sample_preview", CommandPreview).show_spec(spec)
        except ValueError as exc:
            self.query_one("#sample_preview", Static).update(str(exc))

    async def run_sample(self, function: str) -> None:
        result_widget = self.query_one("#sample_result", Static)
        try:
            spec = self._spec(function)
        except ValueError as exc:
            result_widget.update(str(exc))
            return
        spec.action_kind = "sample"
        self.query_one("#sample_preview", CommandPreview).show_spec(spec)
        result_widget.update(f"Running {function}…")
        try:
            run = await self.shell.execute(spec)
            result_widget.update(f"{run.status} · {run.duration_ms} ms · run #{run.id}\n{json.dumps(run.parsed, indent=2, ensure_ascii=False, default=str)[:12000]}")
        except Exception as exc:
            result_widget.update(f"Sample failed: {self.shell.client.redactor.text(str(exc))}")


class FunctionsScreen(BaseScreen):
    title_text = "Run › Functions — discover on one minion, execute on your chosen target"

    def __init__(self):
        super().__init__()
        self.functions: list[str] = []
        self.docs: dict[str, str] = {}
        self.argspecs: dict[str, object] = {}
        self.catalog_source = ""
        self.captured_at = ""
        self.selected_function = ""
        self.required_fields = 0

    def content(self) -> ComposeResult:
        with Horizontal(classes="toolbar"):
            yield Input(placeholder="Catalog minion ID (Master mode)", id="catalog_source")
            yield Select([("Master", "master"), ("Local configured", "local"), ("Local masterless", "masterless")],
                         value=self.shell.execution_context, id="context")
            yield Button("Refresh catalog", id="catalog_refresh", variant="primary")
        yield Static("Catalog not loaded", id="catalog_status")
        yield Input(placeholder="Search functions", id="function_search")
        with Horizontal(id="split"):
            yield DataTable(id="function_table", cursor_type="row")
            with VerticalScroll(id="detail-scroll"):
                yield Static("Select a function", id="function_doc")
                yield Vertical(id="argument_fields")
                yield Input(placeholder="Positional args, shell-quoted (e.g. nginx 'force=true')", id="positional")
                yield Input(placeholder="Named args: key=value (space separated)", id="named")
                yield Input(placeholder="Secret named args: key=value (masked)", id="secret_named", password=True)
                yield Input(placeholder="Raw arguments escape hatch (shell-quoted)", id="raw_args")
                yield TargetFields(prefix="", default_target=self.shell.settings.default_target)
                with Horizontal(classes="toolbar"):
                    yield Button("Selected minions", id="selected")
                    yield Button("Saved targets", id="targets")
                    yield Button("Check responders", id="responders")
                yield Static("Responding minions: not checked; unreachable matches unknown", id="responding")
                yield CommandPreview("Select a function to preview", id="function_preview")
                with Horizontal(classes="toolbar"):
                    yield Button("Run", id="function_run", variant="primary")
                    yield Button("Copy command", id="function_copy")
                yield Static("", id="function_result")

    def on_mount(self) -> None:
        self.query_one("#function_table", DataTable).add_columns("Function")
        if self.catalog_source:
            self.query_one("#catalog_source", Input).value = self.catalog_source

    def _chosen_context(self) -> str:
        return str(self.query_one("#context", Select).value)

    async def refresh_catalog(self) -> None:
        context = self._chosen_context()
        source = self.query_one("#catalog_source", Input).value.strip()
        if context == "master" and (not source or any(char in source for char in "*?[] ,")):
            self.query_one("#catalog_status", Static).update("Enter exactly one catalog minion ID")
            return
        spec = execution_spec(self.shell.settings, "sys.list_functions", target=source, context=context)
        try:
            run = await self.shell.execute_untracked(spec)
            payload = self._payload(run, source, context)
            if not isinstance(payload, list) or not all(isinstance(item, str) for item in payload):
                raise ValueError("Salt returned an invalid function catalog")
            self.functions = sorted(set(payload))
            self.catalog_source = source if context == "master" else context
            self.captured_at = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
            self.docs.clear(); self.argspecs.clear()
            self.selected_function = ""
            self.required_fields = 0
            await self.query_one("#argument_fields", Vertical).remove_children()
            self.query_one("#catalog_status", Static).update(f"Catalog from: {self.catalog_source} · captured {self.captured_at} · {len(self.functions)} functions. Availability on other targets is unknown.")
            self.filter_functions()
        except Exception as exc:
            self.query_one("#catalog_status", Static).update(f"Catalog unavailable: {self.shell.client.redactor.text(str(exc))}")

    @staticmethod
    def _payload(run, source: str, context: str):
        if run.exit_code != 0:
            raise ValueError(run.stderr or "Salt request failed")
        data = run.parsed
        if context == "master":
            if not isinstance(data, dict) or source not in data:
                raise ValueError("Catalog minion did not respond")
            return data[source]
        if isinstance(data, dict) and "local" in data:
            return data["local"]
        return data

    def filter_functions(self) -> None:
        term = self.query_one("#function_search", Input).value.strip().lower()
        table = self.query_one("#function_table", DataTable)
        table.clear()
        for function in self.functions:
            if term in function.lower():
                table.add_row(function, key=function)

    async def select_function(self, function: str) -> None:
        self.selected_function = function
        catalog_source = self.catalog_source
        context = self._chosen_context()
        source = self.query_one("#catalog_source", Input).value.strip()
        for query, cache in (("sys.argspec", self.argspecs), ("sys.doc", self.docs)):
            if function not in cache:
                try:
                    spec = execution_spec(self.shell.settings, query, target=source, context=context, arguments=[function])
                    run = await self.shell.execute_untracked(spec)
                    payload = self._payload(run, source, context)
                    cache[function] = payload.get(function, payload) if isinstance(payload, dict) else payload
                except Exception as exc:
                    cache[function] = f"Unavailable: {self.shell.client.redactor.text(str(exc))}"
        if self.selected_function != function or self.catalog_source != catalog_source:
            return
        detail = f"{function}\nCatalog source: {self.catalog_source}\n\nArguments: {pretty(self.argspecs[function])}\n\n{self.docs[function]}"
        self.query_one("#function_doc", Static).update(detail[:12000])
        holder = self.query_one("#argument_fields", Vertical)
        await holder.remove_children()
        metadata = self.argspecs[function]
        args = metadata.get("args", []) if isinstance(metadata, dict) else []
        defaults = metadata.get("defaults") if isinstance(metadata, dict) else None
        if not isinstance(args, list):
            args = []
        self.required_fields = max(0, len(args) - len(defaults if isinstance(defaults, list) else []))
        for index, name in enumerate(args[:20]):
            await holder.mount(Input(placeholder=f"{name}{' *' if index < self.required_fields else ''}",
                                     id=f"function_arg_{index}"))
        self.update_preview()

    def _spec(self) -> CommandSpec:
        if not self.selected_function:
            raise ValueError("Select a function")
        positional = shlex.split(self.query_one("#positional", Input).value)
        fields = list(self.query("#argument_fields Input"))
        selected_fields = [field.value.strip() for field in fields]
        for index in range(min(self.required_fields, len(fields))):
            if not selected_fields[index] and positional:
                selected_fields[index] = positional.pop(0)
            if not selected_fields[index]:
                raise ValueError(f"Required argument: {fields[index].placeholder.rstrip(' *')}")
        if any(selected_fields[index] and not selected_fields[index - 1] for index in range(1, len(selected_fields))):
            raise ValueError("Fill earlier positional arguments first")
        selected_fields = [value for value in selected_fields if value]
        named = shlex.split(self.query_one("#named", Input).value)
        secret_named = shlex.split(self.query_one("#secret_named", Input).value)
        raw = shlex.split(self.query_one("#raw_args", Input).value)
        if any("=" not in token for token in [*named, *secret_named]):
            raise ValueError("Named arguments must use key=value")
        secrets = [token.split("=", 1)[1] for token in secret_named]
        context = self._chosen_context()
        target = self.query_one("#target", Input).value.strip()
        if context == "master" and not target:
            raise ValueError("Enter a target")
        spec = execution_spec(self.shell.settings, self.selected_function, target=target,
                              target_type=str(self.query_one("#target_type", Select).value),
                              arguments=[*selected_fields, *positional, *named, *secret_named, *raw], context=context)
        spec.action_kind = "function"
        spec.secret_values = secrets
        return spec

    def update_preview(self) -> None:
        try:
            spec = self._spec()
            self.query_one("#function_preview", CommandPreview).show_spec(spec)
        except ValueError as exc:
            self.query_one("#function_preview", Static).update(str(exc))

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "function_search":
            self.filter_functions()
        elif event.input.id in {"positional", "named", "secret_named", "raw_args", "target"} or event.input.id.startswith("function_arg_"):
            self.update_preview()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id in {"context", "target_type"}:
            if event.select.id == "context" and event.value in {"master", "local", "masterless"}:
                self.shell.execution_context = str(event.value)
                for context_bar in self.query(WorkbenchContext):
                    context_bar.refresh_context()
            self.update_preview()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if self.app.size.width < 100:
            self.set_class(True, "show-function-detail")
        asyncio.create_task(self.select_function(str(event.row_key.value)))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        action = event.button.id
        if action == "catalog_refresh":
            asyncio.create_task(self.refresh_catalog())
        elif action == "selected":
            selected = sorted(self.shell.selected_minions)
            if selected:
                self.query_one("#target", Input).value = ",".join(selected)
                self.query_one("#target_type", Select).value = "list"
        elif action == "targets":
            self.shell.target_destination = "functions"
            self.shell.action_show("targets")
        elif action == "responders":
            asyncio.create_task(self.check_responders())
        elif action == "function_copy":
            try:
                spec = self._spec()
                self.app.copy_to_clipboard(display_argv(self.shell.client.redactor.with_values(spec.secret_values).argv(build_argv(spec, self.shell.settings))))
            except ValueError as exc:
                self.notify(str(exc), severity="error")
        elif action == "function_run":
            asyncio.create_task(self.prepare_run())

    async def check_responders(self) -> None:
        try:
            minions, note = await self.shell.preview_target(self._spec())
            self.query_one("#responding", Static).update(f"Responding minions: {len(minions) if minions is not None else 'unknown'} · {note}")
        except Exception as exc:
            self.query_one("#responding", Static).update(f"Responder check unavailable: {self.shell.client.redactor.text(str(exc))}")

    async def prepare_run(self) -> None:
        try:
            spec = self._spec()
        except ValueError as exc:
            self.notify(str(exc), severity="error"); return
        from salt_tui.salt.commands import is_mutating
        if is_mutating(spec):
            safe = display_argv(self.shell.client.redactor.with_values(spec.secret_values).argv(build_argv(spec, self.shell.settings)))
            self.app.push_screen(ConfirmScreen(f"Run {spec.function}?\nTarget: {spec.target}\n\n{safe}"),
                                 lambda approved: asyncio.create_task(self.run_function(spec)) if approved else None)
        else:
            await self.run_function(spec)

    async def run_function(self, spec: CommandSpec) -> None:
        try:
            run = await self.shell.execute(spec)
            result = f"{run.status} · {run.duration_ms} ms · run #{run.id}\n{pretty(run.parsed)}"
            self.query_one("#function_result", Static).update(result[:20000])
        except Exception as exc:
            self.query_one("#function_result", Static).update(
                f"Execution failed: {self.shell.client.redactor.with_values(spec.secret_values).text(str(exc))}")


class KeysScreen(BaseScreen):
    title_text = "Fleet › Keys — exact-ID key management"

    def __init__(self):
        super().__init__()
        self.keys: dict[str, str] = {}
        self.selected_id = ""
        self.fingerprint = ""

    def content(self) -> ComposeResult:
        with Horizontal(classes="toolbar"):
            yield Button("Refresh", id="keys_refresh")
            yield Button("Accept", id="key_accept")
            yield Button("Reject", id="key_reject")
            yield Button("Delete", id="key_delete")
        yield Static("Keys not loaded", id="keys_status")
        yield DataTable(id="keys_table", cursor_type="row")
        yield Static("Select a key to inspect its fingerprint", id="key_detail")

    def on_mount(self) -> None:
        self.query_one("#keys_table", DataTable).add_columns("State", "Exact key ID")
        self._write_buttons(False)
        asyncio.create_task(self.refresh_data())

    def _write_buttons(self, enabled: bool) -> None:
        for action in ("accept", "reject", "delete"):
            self.query_one(f"#key_{action}", Button).disabled = not enabled

    async def refresh_data(self) -> None:
        self.fingerprint = ""
        self._write_buttons(False)
        try:
            self.keys = await self.shell.inspect_keys()
            table = self.query_one("#keys_table", DataTable)
            table.clear()
            for key_id, state in sorted(self.keys.items(), key=lambda pair: (pair[1], pair[0])):
                table.add_row(state, key_id, key=key_id)
            self.query_one("#keys_status", Static).update(f"{len(self.keys)} keys · pending, accepted, rejected, denied")
        except Exception as exc:
            self.query_one("#keys_status", Static).update(f"Key list unavailable: {self.shell.client.redactor.text(str(exc))}")

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.selected_id = str(event.row_key.value)
        self.fingerprint = ""
        self._write_buttons(False)
        asyncio.create_task(self.inspect_selected())

    async def inspect_selected(self) -> None:
        key_id = self.selected_id
        try:
            fingerprint = await self.shell.key_fingerprint(key_id)
            if key_id != self.selected_id:
                return
            self.fingerprint = fingerprint
            self._write_buttons(True)
            self.query_one("#key_detail", Static).update(f"{key_id} · {self.keys.get(key_id)}\nFingerprint: {self.fingerprint}")
        except Exception as exc:
            self.fingerprint = ""
            self._write_buttons(False)
            self.query_one("#key_detail", Static).update(f"Fingerprint unavailable: {self.shell.client.redactor.text(str(exc))}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "keys_refresh":
            asyncio.create_task(self.refresh_data())
        elif event.button.id in {"key_accept", "key_reject", "key_delete"}:
            asyncio.create_task(self.prepare_action(event.button.id[4:]))

    async def prepare_action(self, action: str) -> None:
        key_id, state, fingerprint = self.selected_id, self.keys.get(self.selected_id), self.fingerprint
        if not key_id or not state or not fingerprint:
            self.notify("Select a key with a visible fingerprint", severity="warning"); return
        try:
            spec = key_action(self.shell.settings, action, key_id)
            if (await self.shell.inspect_keys()).get(key_id) != state or await self.shell.key_fingerprint(key_id) != fingerprint:
                raise ValueError("Key state or fingerprint changed; refresh before acting")
        except Exception as exc:
            self._write_buttons(False)
            self.notify(self.shell.client.redactor.text(str(exc)), severity="error"); return
        safe = display_argv(self.shell.client.redactor.argv(build_argv(spec, self.shell.settings)))
        self.app.push_screen(ConfirmScreen(f"{action.title()} exact key {key_id}?\nState: {state}\nFingerprint: {fingerprint}\n\n{safe}", confirm_label=action.title()),
                             lambda approved: asyncio.create_task(self.apply_action(spec, state, fingerprint)) if approved else None)

    async def apply_action(self, spec: CommandSpec, state: str, fingerprint: str) -> None:
        try:
            if (await self.shell.inspect_keys()).get(spec.target) != state or await self.shell.key_fingerprint(spec.target) != fingerprint:
                raise ValueError("Key state or fingerprint changed; action aborted")
            run = await self.shell.execute(spec)
            self.query_one("#keys_status", Static).update(f"{spec.function}: {run.status} · run #{run.id}")
            await self.refresh_data()
        except Exception as exc:
            self.notify(self.shell.client.redactor.text(str(exc)), severity="error")


class FileCopyScreen(BaseScreen):
    title_text = "Run › File Copy — one local file to responding minions"

    def content(self) -> ComposeResult:
        yield Input(placeholder="Readable local file path", id="copy_source")
        yield Input(placeholder="Destination path on minions", id="copy_destination")
        yield TargetFields(prefix="copy_", default_target=self.shell.settings.default_target, copy_only=True)
        yield Static("Choose a source file", id="copy_file_info")
        yield Static("Responding minions: not checked; unreachable matches unknown", id="copy_responders")
        yield CommandPreview("Exact command preview", id="copy_preview")
        with Horizontal(classes="toolbar"):
            yield Button("Selected minions", id="copy_selected")
            yield Button("Saved targets", id="copy_targets")
            yield Button("Check responders", id="copy_check")
            yield Button("Copy file", id="copy_run", variant="primary")
        yield Static("", id="copy_result")

    def _spec(self) -> CommandSpec:
        return copy_action(self.shell.settings, Path(self.query_one("#copy_source", Input).value).expanduser(),
                           self.query_one("#copy_destination", Input).value.strip(),
                           self.query_one("#copy_target", Input).value.strip(),
                           str(self.query_one("#copy_target_type", Select).value))

    def on_input_changed(self, event: Input.Changed) -> None:
        self.update_preview()

    def on_select_changed(self, event: Select.Changed) -> None:
        self.update_preview()

    def update_preview(self) -> None:
        try:
            spec = self._spec()
            size = Path(self.query_one("#copy_source", Input).value).expanduser().stat().st_size
            self.query_one("#copy_file_info", Static).update(f"One local file · {size:,} bytes. Contents are not stored in history.")
            self.query_one("#copy_preview", CommandPreview).show_spec(spec)
        except (ValueError, OSError) as exc:
            self.query_one("#copy_preview", Static).update(str(exc))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "copy_selected":
            selected = sorted(self.shell.selected_minions)
            if selected:
                self.query_one("#copy_target", Input).value = ",".join(selected)
                self.query_one("#copy_target_type", Select).value = "list"
        elif event.button.id == "copy_targets":
            self.shell.target_destination = "file_copy"
            self.shell.action_show("targets")
        elif event.button.id == "copy_check":
            asyncio.create_task(self.check_responders())
        elif event.button.id == "copy_run":
            asyncio.create_task(self.prepare_copy())

    async def check_responders(self) -> None:
        try:
            spec = self._spec()
            probe = CommandSpec(function="test.ping", target=spec.target, target_type=spec.target_type)
            members, note = await self.shell.preview_target(probe)
            self.query_one("#copy_responders", Static).update(f"Responding minions: {len(members) if members is not None else 'unknown'} · {note}")
        except Exception as exc:
            self.query_one("#copy_responders", Static).update(f"Responder check unavailable: {self.shell.client.redactor.text(str(exc))}")

    async def prepare_copy(self) -> None:
        try:
            spec = self._spec()
            size = Path(self.query_one("#copy_source", Input).value).expanduser().stat().st_size
        except (ValueError, OSError) as exc:
            self.notify(str(exc), severity="error"); return
        try:
            members, note = await self.shell.preview_target(CommandSpec(function="test.ping", target=spec.target,
                                                                          target_type=spec.target_type))
            responder_summary = f"Responding minions: {len(members) if members is not None else 'unknown'} · {note}"
        except Exception as exc:
            responder_summary = f"Responder check unavailable: {self.shell.client.redactor.text(str(exc))}"
        self.query_one("#copy_responders", Static).update(responder_summary)
        safe = display_argv(self.shell.client.redactor.argv(build_argv(spec, self.shell.settings)))
        message = f"Copy one file ({size:,} bytes)?\nTarget: {spec.target} ({spec.target_type})\n{responder_summary}\nDestination: {self.query_one('#copy_destination', Input).value}\n\n{safe}"
        self.app.push_screen(ConfirmScreen(message, confirm_label="Copy file"),
                             lambda approved: asyncio.create_task(self.run_copy(spec)) if approved else None)

    async def run_copy(self, spec: CommandSpec) -> None:
        try:
            run = await self.shell.execute(spec)
            self.query_one("#copy_result", Static).update(f"{run.status} · run #{run.id}\n{pretty(run.parsed)[:12000]}")
        except Exception as exc:
            self.query_one("#copy_result", Static).update(f"Copy failed: {self.shell.client.redactor.text(str(exc))}")
