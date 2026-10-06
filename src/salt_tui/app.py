from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import logging
from typing import Any

from textual.app import App
from textual.screen import ModalScreen
from textual.widgets import Static

from salt_tui.config import Settings
from salt_tui.models import CommandSpec, RunResult
from salt_tui.salt.client import Capabilities, SubprocessSaltClient, detect_capabilities
from salt_tui.salt.python_client import SaltPythonBackend
from salt_tui.salt.events import EventMonitor, choose_event_source
from salt_tui.storage.database import Database
from salt_tui.ui.live import EventScreen, LiveRunScreen
from salt_tui.ui.graph import GraphScreen
from salt_tui.ui.analytics import FailureIntelligenceScreen, PerformanceScreen
from salt_tui.ui.runner import RunnerScreen, OrchestrationScreen
from salt_tui.ui.targets import TargetsScreen
from salt_tui.ui.matrix import MatrixScreen
from salt_tui.ui.fleet import MinionDetailScreen, NodegroupsScreen
from salt_tui.salt.commands import build_argv, display_argv, parse_line
from salt_tui.salt.nodegroups import names as nodegroup_names
from salt_tui.plugins import PluginRegistry, load_plugins

LOG = logging.getLogger("salt_tui")
from salt_tui.sls.graph import StateGraph
from salt_tui.sls.source_links import locate_source
from salt_tui.ui.screens import (CommandScreen, ConfirmScreen, DashboardScreen, FailuresScreen, HistoryScreen,
                                 JobsScreen, LogsScreen, MinionsScreen, PaletteScreen, SettingsScreen,
                                 SlsScreen, StateReviewScreen, StatesScreen)


class SaltTUI(App):
    TITLE = "Salt TUI"
    CSS = """
    Screen { layout: vertical; }
    .page-title { height: 1; text-style: bold; background: $primary; color: $text; padding: 0 1; }
    .hint { height: 2; padding: 0 1; }
    #capabilities, #summary, #preview { height: auto; min-height: 2; padding: 0 1; }
    #split { height: 1fr; }
    #table, #files { width: 55%; height: 1fr; }
    #detail-scroll { width: 45%; height: 1fr; border-left: solid $primary; }
    #detail, #source { padding: 1; width: auto; height: auto; }
    .breadcrumb { height: 1; padding: 0 1; color: $text-muted; }
    #sls-hint, #sls-fields, #sls-functions, #sls-status, #review_hint, #tracker_hint { height: 1; padding: 0 1; }
    #sls-hint, #sls-fields, #review_hint, #tracker_hint { color: $text-muted; }
    #sls-functions { color: $text-muted; }
    #sls-status { color: $accent; }
    #sls-loading { height: 1; display: none; }
    #sls-detail { width: 45%; height: 1fr; border-left: solid $primary; }
    #sls-detail #detail-scroll { width: 100%; border: none; }
    #source-title { height: 1; padding: 0 1; text-style: bold; background: $surface; }
    #source-origin { height: auto; max-height: 3; padding: 0 1; color: $text-muted; }
    #review_summary { height: auto; min-height: 2; padding: 0 1; text-style: bold; }
    #review_table { width: 55%; height: 1fr; }
    .toolbar { height: 3; }
    .toolbar Input { width: 1fr; }
    .toolbar Input.target-type { width: 14; }
    .toolbar Button { min-width: 12; }
    #recent, #output, #jobs { height: 1fr; }
    #graph_split { height: 1fr; }
    #graph_nodes { width: 35%; height: 1fr; }
    #graph_panes { width: 65%; height: 1fr; }
    #graph_source_scroll, #graph_compiled_scroll { width: 50%; height: 1fr; border-left: solid $primary; }
    #graph_source, #graph_compiled { padding: 1; width: auto; height: auto; }
    #runner_functions { width: 40%; height: 1fr; }
    #runner_output { width: 60%; height: 1fr; }
    #orch_states { height: 40%; }
    #orch_output { height: 1fr; }
    """
    BINDINGS = [
        ("d", "show('dashboard')", "Dashboard"), ("m", "show('minions')", "Minions"),
        ("j", "show('jobs')", "Jobs"), ("r", "show('states')", "States"),
        ("s", "show('sls')", "SLS"), ("h", "show('history')", "History"),
        ("l", "show('logs')", "Logs"), ("c", "show('command')", "Command"),
        ("t", "show('settings')", "Settings"), ("colon", "palette", "Palette"),
        ("e", "show('events')", "Events"), ("v", "show('live')", "Run tracker"),
        ("g", "show('graph')", "Graph"),
        ("i", "show('intelligence')", "Failure patterns"), ("p", "show('performance')", "Slow states"),
        ("ctrl+u", "show('runner')", "Runner"), ("ctrl+o", "show('orchestration')", "Orchestration"),
        ("ctrl+t", "show('targets')", "Saved targets"),
        ("ctrl+n", "show('nodegroups')", "Nodegroups"),
        ("ctrl+m", "show('matrix')", "State matrix"),
        ("exclamation_mark", "show('failures')", "Failures"),
        ("ctrl+r", "refresh", "Refresh"), ("question_mark", "help", "Help"),
        ("escape", "back", "Back"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, settings: Settings | None = None, initial: str = "dashboard", command: str | None = None):
        super().__init__()
        self.settings = settings or Settings.load()
        self.plugins = load_plugins() if self.settings.enable_plugins else PluginRegistry()
        if self.settings.backend in self.plugins.backends:
            self.client = self.plugins.backends[self.settings.backend](self.settings)
        else:
            self.client = SaltPythonBackend(self.settings) if self.settings.backend == "python" else SubprocessSaltClient(self.settings)
        if isinstance(self.client, SubprocessSaltClient):
            self.client.parsers.extend(self.plugins.parsers)
        elif isinstance(self.client, SaltPythonBackend):
            self.client.fallback.parsers.extend(self.plugins.parsers)
        self.db = Database(self.settings.database, self.client.redactor)
        self.events = EventMonitor(choose_event_source(self.settings), self.db, retention=self.settings.event_retention)
        self.capabilities: Capabilities | None = None
        self.current_run_id: int | None = None
        self.current_sls: str | None = None
        self.current_graph: StateGraph | None = None
        self.review_run_id: int | None = None
        self.review_spec: CommandSpec | None = None
        self.last_state_test: tuple[tuple, int] | None = None
        self.selected_target: dict | None = None
        self.selected_minions: set[str] = set()
        self.current_minion_id: str | None = None
        self.target_draft: dict[str, str] | None = None
        self.initial = initial
        self._current_screen_name = initial
        self._screen_history: list[str] = []
        self.initial_command = command
        self._busy = False
        self._fallback_dir: tempfile.TemporaryDirectory | None = None

    async def on_mount(self) -> None:
        try:
            await self.db.migrate()
        except Exception as exc:
            LOG.error("Primary history database unavailable (%s)", type(exc).__name__)
            self.notify(f"History database unavailable: {exc}", severity="error", timeout=10)
            self._fallback_dir = tempfile.TemporaryDirectory(prefix="salt-tui-")
            self.db = Database(Path(self._fallback_dir.name) / "history.db", self.client.redactor)
            await self.db.migrate()
            self.events.database = self.db
        await self.db.seed_targets(self.settings.targets)
        for name, screen in {
            "dashboard": DashboardScreen(), "command": CommandScreen(self.initial_command),
            "minions": MinionsScreen(), "jobs": JobsScreen(), "states": StatesScreen(),
            "sls": SlsScreen(), "history": HistoryScreen(), "logs": LogsScreen(),
            "review": StateReviewScreen(),
            "failures": FailuresScreen(), "settings": SettingsScreen(),
            "events": EventScreen(), "live": LiveRunScreen(),
            "graph": GraphScreen(),
            "intelligence": FailureIntelligenceScreen(), "performance": PerformanceScreen(),
            "runner": RunnerScreen(), "orchestration": OrchestrationScreen(),
            "targets": TargetsScreen(),
            "matrix": MatrixScreen(),
            "minion_detail": MinionDetailScreen(), "nodegroups": NodegroupsScreen(),
        }.items():
            self.install_screen(screen, name)
        for name, factory in self.plugins.screens.items():
            if name not in self._installed_screens:
                self.install_screen(factory(), name)
        for error in self.plugins.errors:
            LOG.error("Plugin loading failed: %s", self.client.redactor.text(error))
        self.push_screen(self.initial)
        self.events.start()
        asyncio.create_task(self._backfill_signatures())
        asyncio.create_task(self._redact_legacy())
        asyncio.create_task(self._detect())
        self.set_interval(max(5, self.settings.refresh_seconds), self._refresh_dashboard)
        self.set_interval(10, self._expire_live)

    async def _expire_live(self) -> None:
        await self.db.mark_live_timeouts(self.settings.live_return_timeout_seconds)

    async def _backfill_signatures(self) -> None:
        try:
            while await self.db.backfill_failure_signatures() > 0:
                await asyncio.sleep(0)
        except Exception as exc:
            LOG.error("Failure signature backfill failed (%s)", type(exc).__name__)
            self.notify(f"Failure signature backfill failed: {exc}", severity="error")

    async def _redact_legacy(self) -> None:
        try:
            await self.db.redact_legacy_history()
        except Exception as exc:
            LOG.error("History redaction failed (%s)", type(exc).__name__)
            self.notify(f"History redaction failed: {exc}", severity="error")

    async def _detect(self) -> None:
        self.capabilities = await detect_capabilities(self.settings)
        if isinstance(self.screen, DashboardScreen):
            self.screen.call_after_refresh(self.screen.refresh_data)

    async def _refresh_dashboard(self) -> None:
        if isinstance(self.screen, DashboardScreen):
            await self.screen.refresh_data()

    async def execute(self, spec: CommandSpec, log: Any = None, parent_run_id: int | None = None) -> RunResult:
        if self._busy:
            raise RuntimeError("A Salt command is already running")
        self._busy = True
        lines: list[tuple[str, str]] = []
        async def capture(level: str, line: str) -> None:
            if len(lines) < 5000:
                lines.append((level, line[:2000]))
            if log:
                await log(level, line)
        try:
            run = await self.client.run(spec, capture)
            run.parent_run_id = parent_run_id
            try:
                run.id = await self.db.save_run(run, saltenv=spec.saltenv, pillarenv=spec.pillarenv,
                                                test_mode=spec.test, log_lines=lines)
                if run.jid and run.status == "running":
                    await self.db.replay_events(run.id, run.jid)
                await self.db.prune(self.settings.history_limit, self.settings.log_limit)
            except Exception as exc:
                LOG.error("Could not save Salt run history (%s)", type(exc).__name__)
                self.notify(f"Could not save history: {exc}", severity="error")
            return run
        finally:
            self._busy = False

    async def preview_target(self, spec: CommandSpec) -> tuple[list[str] | None, str]:
        if spec.executable != "salt":
            return None, "Target preview is unavailable for this Salt executable."
        probe = CommandSpec(function="test.ping", target=spec.target, target_type=spec.target_type,
                            timeout=min(spec.timeout or 10, 10))
        run = await self.execute(probe)
        if run.exit_code != 0 or not isinstance(run.parsed, dict):
            return None, f"Target preview unavailable: {run.stderr or 'no structured response'}"
        minions = sorted(str(minion) for minion in run.parsed)
        return minions, "Responding minions only; unreachable matches may be absent."

    async def inspect_minion_detail(self, minion_id: str, kind: str) -> Any:
        """Run an unpersisted, read-only inspection command for one minion."""
        function = {"grains": "grains.items", "pillars": "pillar.items",
                    "schedules": "schedule.list", "beacons": "beacons.list"}.get(kind)
        if not function:
            raise ValueError("Unsupported minion detail")
        if self._busy:
            raise RuntimeError("A Salt command is already running; use cached details or retry when it finishes")
        self._busy = True
        try:
            run = await self.client.run(CommandSpec(function=function, target=minion_id, target_type="glob"))
        finally:
            self._busy = False
        if run.exit_code != 0 or run.status == "failed" or not isinstance(run.parsed, dict):
            raise RuntimeError(run.stderr or "Salt did not return structured detail data")
        if minion_id not in run.parsed:
            raise RuntimeError("The selected minion did not return detail data")
        return self.client.redactor.value(run.parsed[minion_id])

    def configured_nodegroups(self) -> list[str]:
        return nodegroup_names(self.settings.master_config)

    async def resolve_nodegroup(self, name: str) -> list[str]:
        if self._busy:
            raise RuntimeError("A Salt command is already running; retry when it finishes")
        self._busy = True
        try:
            run = await self.client.run(CommandSpec(function="test.ping", target=name, target_type="nodegroup", timeout=10))
        finally:
            self._busy = False
        if run.exit_code != 0 or not isinstance(run.parsed, dict):
            raise RuntimeError(run.stderr or "Salt could not resolve this nodegroup")
        members = sorted(str(minion) for minion, result in run.parsed.items() if result is not False)
        for minion in members:
            cached = await self.db.detail_snapshot(minion, "nodegroups")
            groups = cached["payload"] if cached and isinstance(cached["payload"], list) else []
            await self.db.save_detail_snapshot(minion, "nodegroups", sorted(set([*groups, name])),
                                               limit=self.settings.detail_cache_minions_per_kind)
        return members

    @staticmethod
    def _state_workflow_key(spec: CommandSpec) -> tuple:
        return (spec.executable, spec.target, spec.target_type, tuple(spec.arguments), spec.saltenv, spec.pillarenv)

    async def start_state_workflow(self, spec: CommandSpec) -> None:
        spec = replace(spec, arguments=list(spec.arguments), options=list(spec.options))
        if spec.function != "state.apply" or not spec.arguments:
            raise ValueError("Select an SLS state to test or apply")
        if spec.test:
            await self._execute_state_workflow(spec, None)
            return
        preview_id = (self.last_state_test[1] if self.last_state_test
                      and self.last_state_test[0] == self._state_workflow_key(spec) else None)
        preview = await self.db.states(preview_id) if preview_id else []
        planned = sum(bool(json.loads(row["changes_json"])) for row in preview)
        failed = sum(row["result"] == 0 for row in preview)
        matched: list[str] | None = None
        note = "Local salt-call" if spec.executable == "salt-call" else "Target reachability not checked"
        if spec.executable == "salt":
            try:
                matched, note = await self.preview_target(spec)
            except Exception as exc:
                note = f"Target preview unavailable: {self.client.redactor.text(str(exc))[:180]}"
            if self.events.source is not None and self.events.status in {"listening", "connected"}:
                spec.async_run = True
                if "state_events=True" not in spec.arguments:
                    spec.arguments.append("state_events=True")
        safe = display_argv(self.client.redactor.argv(build_argv(spec, self.settings)))
        preview_text = (f"Dry run #{preview_id}: {len(preview)} states, {planned} planned changes, {failed} failures"
                        if preview_id else "No matching dry run. Use Test state first to review planned changes.")
        target_text = f"Responding minions: {len(matched)} · {note}" if matched is not None else note
        message = (f"Apply {spec.arguments[0]}?\nTarget: {spec.target}\nEnvironment: {spec.saltenv or self.settings.default_saltenv}\n"
                   f"{target_text}\n{preview_text}\n\n{safe}")
        self.push_screen(ConfirmScreen(message, confirm_label="Apply state"),
                         lambda approved: asyncio.create_task(self._execute_state_workflow(spec, preview_id)) if approved else None)

    async def _execute_state_workflow(self, spec: CommandSpec, preview_id: int | None) -> None:
        try:
            run = await self.execute(spec, parent_run_id=preview_id)
        except Exception as exc:
            self.notify(f"State execution failed: {self.client.redactor.text(str(exc))[:180]}", severity="error")
            return
        if run.id is None:
            self.notify("Salt ran, but its result could not be saved to history", severity="error")
            return
        self.current_run_id = run.id
        if spec.test:
            self.last_state_test = (self._state_workflow_key(spec), run.id)
            self.review_run_id = run.id
            self.review_spec = spec
            self.show_screen("review")
            self.screen.call_after_refresh(self.screen.refresh_data)
        else:
            self.open_live_run(run.id)

    def should_confirm(self, spec: CommandSpec, response_count: int | None) -> bool:
        if not self.settings.confirm_changes:
            return False
        mode = self.settings.confirmation_mode
        if mode == "never":
            return False
        if mode == "large-target":
            return response_count is None or response_count >= self.settings.confirm_if_target_count
        if mode == "production-only":
            env = (spec.saltenv or self.settings.default_saltenv).lower()
            target = spec.target.lower()
            return env in {x.lower() for x in self.settings.production_envs} or "prod" in target
        return True

    def open_run(self, run_id: int) -> None:
        self.current_run_id = run_id
        self.show_screen("states")
        self.screen.call_after_refresh(self.screen.refresh_data)

    def open_live_run(self, run_id: int) -> None:
        self.current_run_id = run_id
        self.show_screen("live")
        self.screen.call_after_refresh(self.screen.refresh_data)

    def open_source(self, sls: str, state_id: str = "") -> None:
        self.current_sls = sls
        self.show_screen("sls")
        async def locate() -> None:
            await self.screen.select_source(sls)
            result = locate_source(self.settings, self.settings.default_saltenv, sls, state_id) if state_id else None
            if result:
                self.notify(f"Source: {result[0]}:{result[1]}", timeout=8)
        self.screen.call_after_refresh(locate)

    def open_command(self, line: str) -> None:
        self.show_screen("command")
        def fill() -> None:
            self.screen.query_one("#command").value = line
        self.screen.call_after_refresh(fill)

    def open_command_with_target(self, target: dict) -> None:
        try:
            existing = self.get_screen("command").query_one("#command").value
            spec = parse_line(existing, self.settings)
            if spec.executable != "salt":
                raise ValueError("Current command has no Salt target")
        except Exception:
            spec = CommandSpec(function="state.apply")
        spec.target = target["expression"]
        spec.target_type = target["target_type"]
        self.open_command(display_argv(build_argv(spec, self.settings)))

    def open_minion_detail(self, minion_id: str) -> None:
        self.current_minion_id = minion_id
        detail = self.get_screen("minion_detail")
        detail.kind = "grains"
        detail.pillar_value = None
        detail.show_full_pillars = False
        self.show_screen("minion_detail")
        self.screen.call_after_refresh(self.screen.load, False)

    def use_selected_minions(self) -> None:
        if not self.selected_minions:
            self.notify("Select at least one minion first", severity="warning")
            return
        self.open_command_with_target({"expression": ",".join(sorted(self.selected_minions)), "target_type": "list"})

    def save_selected_minions(self) -> None:
        if not self.selected_minions:
            self.notify("Select at least one minion first", severity="warning")
            return
        self.target_draft = {"expression": ",".join(sorted(self.selected_minions)), "target_type": "list"}
        self.show_screen("targets")
        self.screen.call_after_refresh(self.screen.apply_draft)

    def action_show(self, name: str) -> None:
        self.show_screen(name)
        if hasattr(self.screen, "refresh_data"):
            self.screen.call_after_refresh(self.screen.refresh_data)

    def show_screen(self, name: str, *, remember: bool = True) -> None:
        if name != self._current_screen_name:
            if remember:
                self._screen_history.append(self._current_screen_name)
            self._current_screen_name = name
        self.switch_screen(name)
        self._update_breadcrumb()

    def breadcrumb_text(self) -> str:
        names = {"sls": "SLS Explorer", "graph": "Dependencies", "states": "State Results",
                 "review": "Dry-run Review", "live": "Run Tracker"}
        trail = (self._screen_history[-2:] + [self._current_screen_name])
        return " › ".join(names.get(name, name.replace("_", " ").title()) for name in trail)

    def _update_breadcrumb(self) -> None:
        breadcrumb = self.screen.query(".breadcrumb")
        if breadcrumb:
            breadcrumb.first().update(self.breadcrumb_text())

    def action_back(self) -> None:
        if isinstance(self.screen, ModalScreen):
            return
        if self._screen_history:
            self.show_screen(self._screen_history.pop(), remember=False)
        elif self._current_screen_name != "dashboard":
            self.show_screen("dashboard", remember=False)
        if isinstance(self.screen, SlsScreen):
            self.screen.query_one("#files").focus()

    def action_refresh(self) -> None:
        if isinstance(self.screen, MinionsScreen):
            async def probe() -> None:
                try:
                    await self.execute(CommandSpec(function="test.ping", target=self.settings.default_target))
                    await self.screen.refresh_data()
                except Exception as exc:
                    self.notify(str(exc), severity="error")
            asyncio.create_task(probe())
        elif hasattr(self.screen, "refresh_data"):
            asyncio.create_task(self.screen.refresh_data())

    def action_help(self) -> None:
        self.notify("d Dashboard | m Minions | Ctrl+N Nodegroups | j Jobs | r States | Ctrl+M Matrix | v Run tracker | e Events | g Graph | s SLS | h History | l Logs | i Failures | p Slow | Ctrl+U Runner | Ctrl+O Orchestration | Ctrl+T Targets | c Command | : Palette | Esc Back | q Quit", timeout=10)

    def action_palette(self) -> None:
        def selected(value: str | None) -> None:
            if not value:
                return
            name = value.lower().replace(" ", "")
            aliases = {"state": "states", "runs": "history", "commands": "command", "slses": "sls"}
            name = aliases.get(name, name)
            if name in self._installed_screens:
                self.action_show(name)
            elif name in self.plugins.commands:
                self.plugins.commands[name](self)
            elif value.startswith(("salt ", "salt-call ", "salt-run ", "salt-key ", "salt-cp ")):
                self.open_command(value)
            else:
                self.notify("Unknown screen or Salt command", severity="warning")
        self.push_screen(PaletteScreen(), selected)

    async def on_unmount(self) -> None:
        await self.events.stop()
        if self._fallback_dir is not None:
            self._fallback_dir.cleanup()
