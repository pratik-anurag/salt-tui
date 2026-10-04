from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from rich.console import Group
from rich.syntax import Syntax
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Static

from salt_tui.sls.graph import StateGraph
from salt_tui.sls.source_links import extend_hints, include_hints, locate_source
from salt_tui.sls.syntax import SaltSlsLexer

if TYPE_CHECKING:
    from salt_tui.app import SaltTUI


class GraphScreen(Screen):
    BINDINGS = [("u", "upstream", "Upstream"), ("n", "downstream", "Downstream"),
                ("o", "open_source", "Open source"), ("a", "show_all", "Full graph")]

    @property
    def shell(self) -> SaltTUI:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Compiled state graph — Salt low data", classes="page-title")
        with Horizontal(classes="toolbar"):
            yield Input(placeholder="State search", id="graph_search")
            yield Input(placeholder="SLS filter", id="graph_sls")
            yield Input(placeholder="Module filter", id="graph_module")
            yield Input(placeholder="Requisite type", id="graph_kind")
            yield Input(placeholder="Path to state ID", id="graph_path")
            yield Button("Focus", id="focus")
            yield Button("Full", id="full")
            yield Button("Path", id="path")
        yield Static("", id="graph_status")
        with Horizontal(id="graph_split"):
            yield DataTable(id="graph_nodes")
            with Horizontal(id="graph_panes"):
                with VerticalScroll(id="graph_source_scroll"):
                    yield Static("Select a state", id="graph_source")
                with VerticalScroll(id="graph_compiled_scroll"):
                    yield Static("Compiled low state and requisites", id="graph_compiled")
        yield Footer()

    async def on_mount(self) -> None:
        self.query_one("#graph_nodes", DataTable).add_columns("State ID", "Module.Function", "SLS", "In", "Out")
        self.focus_key: str | None = None
        self.view = self.shell.current_graph or StateGraph({}, [])
        self.refresh_graph()

    def refresh_graph(self) -> None:
        graph = self.shell.current_graph or StateGraph({}, [])
        kinds_text = self.query_one("#graph_kind", Input).value.strip()
        kinds = {x.strip() for x in kinds_text.split(",") if x.strip()} or None
        self.view = graph.filtered(search=self.query_one("#graph_search", Input).value,
                                   sls=self.query_one("#graph_sls", Input).value,
                                   module=self.query_one("#graph_module", Input).value, kinds=kinds)
        if self.focus_key and self.focus_key in self.view.nodes:
            self.view = self.view.focused(self.focus_key, 1)
        table = self.query_one("#graph_nodes", DataTable)
        table.clear()
        for key, node in self.view.nodes.items():
            table.add_row(node.state_id, f"{node.module}.{node.function}", node.sls,
                          str(len(self.view.upstream(key))), str(len(self.view.downstream(key))), key=key)
        diagnostics = graph.diagnostics()
        self.query_one("#graph_status", Static).update(
            f"{len(self.view.nodes)}/{len(graph.nodes)} states | {len(self.view.edges)} edges | {len(diagnostics)} diagnostic hints. "
            "u upstream · n downstream · o source · a full")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.refresh_graph()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "focus":
            self.focus_key = self._selected()
        elif event.button.id == "full":
            self.focus_key = None
        elif event.button.id == "path":
            self.show_path()
            return
        self.refresh_graph()

    def show_path(self) -> None:
        source = self._selected()
        target_id = self.query_one("#graph_path", Input).value.strip()
        if not source or not target_id:
            self.notify("Select a source state and enter a destination state ID")
            return
        destination = next((key for key, node in self.view.nodes.items() if node.state_id == target_id), None)
        if not destination:
            self.notify("Destination not found in this view")
            return
        path = self.view.shortest_path(source, destination)
        if path:
            self.query_one("#graph_compiled", Static).update("Shortest compiled path:\n" + "\n  ↓\n".join(self.view.nodes[key].state_id for key in path))
        else:
            self.notify("No directed dependency path found")

    def _selected(self) -> str | None:
        table = self.query_one("#graph_nodes", DataTable)
        if table.row_count == 0:
            return None
        try:
            return str(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)
        except Exception:
            return None

    async def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        key = str(event.row_key.value)
        node = self.view.nodes.get(key)
        if not node:
            return
        up = [f"← {e.kind}: {self.view.nodes[e.source].state_id if e.source in self.view.nodes else e.source}" for e in self.view.upstream(key)]
        down = [f"→ {e.kind}: {self.view.nodes[e.destination].state_id if e.destination in self.view.nodes else e.destination}" for e in self.view.downstream(key)]
        compiled = f"{node.state_id} | {node.module}.{node.function} | {node.sls}\n\nInbound:\n" + "\n".join(up or ["none"])
        compiled += "\n\nOutbound:\n" + "\n".join(down or ["none"])
        compiled += "\n\nLow data:\n" + json.dumps(node.raw, indent=2, ensure_ascii=False, default=str)
        metrics = await self.shell.db.state_metrics(node.sls, node.state_id, node.module, node.function)
        if self._selected() != key:
            return
        compiled += f"\n\nHistory: {metrics['samples']} samples | {metrics['failures']} failures | average {metrics['average_ms'] or 0:.1f} ms | latest {metrics['latest_result']}"
        self.query_one("#graph_compiled", Static).update(compiled)
        location = await asyncio.to_thread(locate_source, self.shell.settings, self.shell.settings.default_saltenv, node.sls, node.state_id)
        if location:
            path, line = location
            lines = self.shell.client.redactor.text(await asyncio.to_thread(path.read_text, errors="replace")).splitlines()
            start = max(0, line - 11)
            excerpt = "\n".join(lines[start:line + 15])
            hints = await asyncio.to_thread(include_hints, path)
            extends = await asyncio.to_thread(extend_hints, path)
            self.query_one("#graph_source", Static).update(Group(
                f"{path}:{line}\nSource include hints: {', '.join(hints) or 'none'}\nSource extend hints: {', '.join(extends) or 'none'}\n",
                Syntax(excerpt, SaltSlsLexer(), line_numbers=True, start_line=start + 1)))
        else:
            self.query_one("#graph_source", Static).update("No unique literal source location found. Jinja-generated IDs need Salt's rendered data.")

    def action_upstream(self) -> None:
        self._move_connected(upstream=True)

    def action_downstream(self) -> None:
        self._move_connected(upstream=False)

    def _move_connected(self, upstream: bool) -> None:
        key = self._selected()
        if not key:
            return
        edges = self.view.upstream(key) if upstream else self.view.downstream(key)
        destination = edges[0].source if upstream and edges else edges[0].destination if edges else None
        if destination and destination in self.view.nodes:
            keys = list(self.view.nodes)
            self.query_one("#graph_nodes", DataTable).move_cursor(row=keys.index(destination))
        else:
            self.notify("No connected state in this view")

    def action_open_source(self) -> None:
        key = self._selected()
        if key and key in self.view.nodes:
            node = self.view.nodes[key]
            self.shell.open_source(node.sls, node.state_id)

    def action_show_all(self) -> None:
        self.focus_key = None
        self.query_one("#graph_search", Input).value = ""
        self.query_one("#graph_sls", Input).value = ""
        self.query_one("#graph_module", Input).value = ""
        self.query_one("#graph_kind", Input).value = ""
        self.refresh_graph()
