from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import logging
from typing import Any

from textual.app import App

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
from salt_tui.salt.commands import build_argv, display_argv, parse_line
from salt_tui.plugins import PluginRegistry, load_plugins

LOG = logging.getLogger("salt_tui")
from salt_tui.sls.graph import StateGraph
from salt_tui.sls.source_links import locate_source
from salt_tui.ui.screens import (CommandScreen, DashboardScreen, FailuresScreen, HistoryScreen,
                                 JobsScreen, LogsScreen, MinionsScreen, PaletteScreen, SettingsScreen,
                                 SlsScreen, StatesScreen)


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
    .toolbar { height: 3; }
    .toolbar Input { width: 1fr; }
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
        ("e", "show('events')", "Events"), ("v", "show('live')", "Live run"),
        ("g", "show('graph')", "Graph"),
        ("i", "show('intelligence')", "Failure patterns"), ("p", "show('performance')", "Slow states"),
        ("ctrl+u", "show('runner')", "Runner"), ("ctrl+o", "show('orchestration')", "Orchestration"),
        ("ctrl+t", "show('targets')", "Saved targets"),
        ("ctrl+m", "show('matrix')", "State matrix"),
        ("exclamation_mark", "show('failures')", "Failures"),
        ("ctrl+r", "refresh", "Refresh"), ("question_mark", "help", "Help"),
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
        self.selected_target: dict | None = None
        self.initial = initial
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
            "failures": FailuresScreen(), "settings": SettingsScreen(),
            "events": EventScreen(), "live": LiveRunScreen(),
            "graph": GraphScreen(),
            "intelligence": FailureIntelligenceScreen(), "performance": PerformanceScreen(),
            "runner": RunnerScreen(), "orchestration": OrchestrationScreen(),
            "targets": TargetsScreen(),
            "matrix": MatrixScreen(),
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
        self.switch_screen("states")
        self.screen.call_after_refresh(self.screen.refresh_data)

    def open_live_run(self, run_id: int) -> None:
        self.current_run_id = run_id
        self.switch_screen("live")
        self.screen.call_after_refresh(self.screen.refresh_data)

    def open_source(self, sls: str, state_id: str = "") -> None:
        self.current_sls = sls
        self.switch_screen("sls")
        async def locate() -> None:
            await self.screen.select_source(sls)
            result = locate_source(self.settings, self.settings.default_saltenv, sls, state_id) if state_id else None
            if result:
                self.notify(f"Source: {result[0]}:{result[1]}", timeout=8)
        self.screen.call_after_refresh(locate)

    def open_command(self, line: str) -> None:
        self.switch_screen("command")
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

    def action_show(self, name: str) -> None:
        self.switch_screen(name)
        if hasattr(self.screen, "refresh_data"):
            self.screen.call_after_refresh(self.screen.refresh_data)

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
        self.notify("d Dashboard | m Minions | j Jobs | r States | Ctrl+M Matrix | v Live | e Events | g Graph | s SLS | h History | l Logs | i Failures | p Slow | Ctrl+U Runner | Ctrl+O Orchestration | Ctrl+T Targets | c Command | : Palette | q Quit", timeout=10)

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
