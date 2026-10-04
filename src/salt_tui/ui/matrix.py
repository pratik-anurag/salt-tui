from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Static

if TYPE_CHECKING:
    from salt_tui.app import SaltTUI


class MatrixScreen(Screen):
    """Paged state × minion matrix; only visible cells are materialized."""
    @property
    def shell(self) -> SaltTUI:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Fleet state matrix — ✓ pass  C changed  ✗ failed  … pending  ? no return  - not applicable", classes="page-title")
        with Horizontal(classes="toolbar"):
            yield Input(placeholder="Search state or SLS", id="matrix_search")
            yield Input(value="all", placeholder="Filter: all/failed/changed/divergent/slow/missing", id="matrix_filter")
            yield Input(value="failures", placeholder="Sort: failures/changed/duration/state_id/sls", id="matrix_sort")
            yield Button("◀ States", id="states_prev")
            yield Button("States ▶", id="states_next")
            yield Button("◀ Minions", id="minions_prev")
            yield Button("Minions ▶", id="minions_next")
        yield Static("", id="matrix_status")
        with Horizontal(id="split"):
            yield DataTable(id="matrix_table", cursor_type="row")
            with VerticalScroll(id="detail-scroll"):
                yield Static("Select a state row", id="matrix_detail")
        yield Footer()

    async def on_mount(self) -> None:
        self.state_offset = 0
        self.minion_offset = 0
        self.rows: dict[str, dict] = {}
        self.minions: list[str] = []
        await self.refresh_data()

    async def refresh_data(self) -> None:
        run_id = self.shell.current_run_id
        if not run_id:
            self.query_one("#matrix_status", Static).update("Select a run in History or execute a state run first.")
            return
        sort = self.query_one("#matrix_sort", Input).value.strip()
        filter = self.query_one("#matrix_filter", Input).value.strip()
        search = self.query_one("#matrix_search", Input).value.strip()
        self.minions = await self.shell.db.matrix_minions(run_id, limit=20, offset=self.minion_offset)
        summaries = await self.shell.db.matrix_summary(run_id, sort=sort, filter=filter, search=search,
                                                        limit=200, offset=self.state_offset)
        self.rows = {self._key(row): row for row in summaries}
        states = [row["state_id"] for row in summaries]
        cells = await self.shell.db.matrix_cells(run_id, self.minions, states)
        cell_map = {}
        for cell in cells:
            key = (self._key(cell), cell["minion_id"])
            status = "✗" if cell["result"] == 0 else "C" if cell["changes_json"] not in ("{}", "null", None) else "✓" if cell["result"] == 1 else "?"
            rank = {"✗": 4, "C": 3, "✓": 2, "?": 1}
            if key not in cell_map or rank[status] > rank[cell_map[key]]:
                cell_map[key] = status
        marks = ",".join("?" for _ in self.minions)
        progress = {row["minion_id"]: row["status"] for row in await self.shell.db.query(
            f"SELECT minion_id,status FROM run_minion_progress WHERE run_id=? AND minion_id IN ({marks})", (run_id, *self.minions))} if self.minions else {}
        returned = {row["minion_id"] for row in await self.shell.db.query(
            f"SELECT minion_id FROM minion_results WHERE run_id=? AND minion_id IN ({marks})", (run_id, *self.minions))} if self.minions else set()
        table = self.query_one("#matrix_table", DataTable)
        table.clear(columns=True)
        table.add_columns("State", "SLS", "Fail", "Changed", *self.minions)
        for key, row in self.rows.items():
            values = []
            for minion in self.minions:
                values.append(cell_map.get((key, minion), "-" if minion in returned else "?" if progress.get(minion) == "no_return" else "…" if progress.get(minion) in {"pending", "running"} else "-"))
            table.add_row(row["state_id"], row["sls"], str(row["failures"]), str(row["changed"]), *values, key=key)
        total_minions = await self.shell.db.matrix_minion_count(run_id)
        state_range = f"{self.state_offset+1}–{self.state_offset+len(summaries)}" if summaries else "none"
        minion_range = f"{self.minion_offset+1}–{self.minion_offset+len(self.minions)}" if self.minions else "none"
        self.query_one("#matrix_status", Static).update(
            f"Run #{run_id} | States {state_range} | Minions {minion_range} of {total_minions}")

    @staticmethod
    def _key(row: dict) -> str:
        return "|".join(str(row[field]) for field in ("sls", "state_id", "state_module", "state_function"))

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        self.state_offset = 0
        await self.refresh_data()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        name = event.button.id
        if name == "states_prev": self.state_offset = max(0, self.state_offset - 200)
        elif name == "states_next" and len(self.rows) == 200: self.state_offset += 200
        elif name == "minions_prev": self.minion_offset = max(0, self.minion_offset - 20)
        elif name == "minions_next" and len(self.minions) == 20: self.minion_offset += 20
        await self.refresh_data()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        row = self.rows.get(str(event.row_key.value))
        if row:
            self.query_one("#matrix_detail", Static).update(
                f"{row['sls']} / {row['state_id']}\n{row['state_module']}.{row['state_function']}\n\n"
                f"Failures: {row['failures']}\nChanged: {row['changed']}\nPresent: {row['present']}\nMean: {row['mean_ms'] or 0:.1f} ms\n"
                f"Variants: {row['variants']}\n\nSort or filter to find fleet drift.")
