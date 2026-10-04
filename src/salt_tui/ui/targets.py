from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Static

if TYPE_CHECKING:
    from salt_tui.app import SaltTUI


class TargetsScreen(Screen):
    @property
    def shell(self) -> SaltTUI:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Saved targets — choose one to apply it to the command runner", classes="page-title")
        with Horizontal(classes="toolbar"):
            yield Input(placeholder="Name", id="target_name")
            yield Input(placeholder="Expression", id="target_expression")
            yield Input(value="glob", placeholder="Type: glob/grain/pillar/compound/nodegroup/list/pcre", id="target_type")
            yield Button("Save", id="target_save")
            yield Button("Rename", id="target_rename")
            yield Button("Delete", id="target_delete", variant="error")
            yield Button("Use", id="target_use", variant="primary")
        yield DataTable(id="target_table")
        yield Static("Select a target. Save updates the selected name; Rename uses the Name input as the new name.", id="target_hint")
        yield Footer()

    async def on_mount(self) -> None:
        self.query_one("#target_table", DataTable).add_columns("Name", "Type", "Expression")
        self.selected_name: str | None = None
        await self.refresh_data()

    async def refresh_data(self) -> None:
        rows = await self.shell.db.list_targets()
        self.rows = {row["name"]: row for row in rows}
        table = self.query_one("#target_table", DataTable); table.clear()
        for row in rows:
            table.add_row(row["name"], row["target_type"], row["expression"], key=row["name"])

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        row = self.rows.get(str(event.row_key.value))
        if row:
            self.selected_name = row["name"]
            self.query_one("#target_name", Input).value = row["name"]
            self.query_one("#target_expression", Input).value = row["expression"]
            self.query_one("#target_type", Input).value = row["target_type"]

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        name = self.query_one("#target_name", Input).value.strip()
        expression = self.query_one("#target_expression", Input).value.strip()
        target_type = self.query_one("#target_type", Input).value.strip()
        try:
            if event.button.id == "target_save":
                await self.shell.db.save_target(name, expression, target_type)
            elif event.button.id == "target_rename":
                if not self.selected_name:
                    raise ValueError("Select a target to rename")
                await self.shell.db.rename_target(self.selected_name, name)
            elif event.button.id == "target_delete":
                if not self.selected_name:
                    raise ValueError("Select a target to delete")
                await self.shell.db.delete_target(self.selected_name)
                self.selected_name = None
            elif event.button.id == "target_use":
                row = self.rows.get(self.selected_name or name)
                if not row:
                    raise ValueError("Select a saved target")
                self.shell.selected_target = row
                self.shell.open_command_with_target(row)
                return
            await self.refresh_data()
        except Exception as exc:
            self.notify(str(exc), severity="error")
