from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Static

if TYPE_CHECKING:
    from salt_tui.app import SaltTUI


def pretty(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)


def path_values(value: Any, paths: list[str]) -> dict[str, Any]:
    """Read colon-separated paths from a Salt return without evaluating expressions."""
    output: dict[str, Any] = {}
    for path in paths:
        current = value
        for part in path.split(":"):
            if isinstance(current, dict) and part in current:
                current = current[part]
            elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
                current = current[int(part)]
            else:
                current = "—"
                break
        output[path] = current
    return output


class MinionDetailScreen(Screen):
    """Read-only per-minion data; pillars deliberately never enter the database."""
    @property
    def shell(self) -> SaltTUI:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        from salt_tui.ui.workbench import WorkbenchSidebar, WorkbenchContext
        yield Header()
        yield WorkbenchSidebar()
        yield WorkbenchContext()
        yield Static("Minion detail — live data is redacted; pillars are session-only", classes="page-title")
        with Horizontal(classes="toolbar"):
            yield Button("Grains", id="detail_grains", variant="primary")
            yield Button("Pillars", id="detail_pillars")
            yield Button("Show full pillars", id="detail_full_pillars")
            yield Button("Schedules", id="detail_schedules")
            yield Button("Beacons", id="detail_beacons")
            yield Button("Nodegroups", id="detail_nodegroups")
            yield Button("Refresh live", id="detail_live")
            yield Button("Show cached", id="detail_cached")
        yield Static("", id="detail_status")
        with VerticalScroll():
            yield Static("", id="detail_payload")
        yield Footer()

    async def on_mount(self) -> None:
        self.kind = "grains"
        self.pillar_value: Any = None
        self.show_full_pillars = False
        await self.load(live=False)

    @property
    def minion_id(self) -> str:
        return self.shell.current_minion_id or ""

    async def load(self, live: bool) -> None:
        status = self.query_one("#detail_status", Static)
        payload = self.query_one("#detail_payload", Static)
        if not self.minion_id:
            status.update("No minion selected.")
            payload.update("")
            return
        if self.kind == "pillars" and not self.shell.settings.enable_pillar_details:
            status.update("Pillar details are disabled. Set enable_pillar_details = true in config.toml and restart.")
            payload.update("")
            return
        if live:
            status.update(f"Refreshing {self.kind} for {self.minion_id}…")
            try:
                value = await self.shell.inspect_minion_detail(self.minion_id, self.kind)
                if self.kind == "pillars":
                    self.pillar_value = value
                else:
                    await self.shell.db.save_detail_snapshot(self.minion_id, self.kind, value, limit=self.shell.settings.detail_cache_minions_per_kind)
                await self.show_value(value, "live", None)
            except Exception as exc:
                cached = await self.shell.db.detail_snapshot(self.minion_id, self.kind) if self.kind != "pillars" else None
                if cached:
                    await self.show_value(cached["payload"], "cached after live refresh failed", cached["captured_at"])
                else:
                    status.update(f"Live refresh unavailable: {self.shell.client.redactor.text(str(exc))[:180]}")
                    payload.update("")
            return
        if self.kind == "pillars":
            if self.pillar_value is None:
                status.update("Pillars are not cached. Choose Refresh live.")
                payload.update("")
            else:
                await self.show_value(self.pillar_value, "session-only", None)
            return
        cached = await self.shell.db.detail_snapshot(self.minion_id, self.kind)
        if not cached:
            status.update(f"No cached {self.kind}. Choose Refresh live.")
            payload.update("")
            return
        await self.show_value(cached["payload"], "cached", cached["captured_at"])

    async def show_value(self, value: Any, source: str, captured_at: str | None) -> None:
        if self.kind == "grains":
            value = path_values(value, self.shell.settings.minion_preview_grains)
        elif self.kind == "pillars" and self.shell.settings.pillar_public_paths and not self.show_full_pillars:
            value = path_values(value, self.shell.settings.pillar_public_paths)
        stale = ""
        if captured_at:
            try:
                captured = datetime.fromisoformat(captured_at)
                if captured.tzinfo is None:
                    captured = captured.replace(tzinfo=timezone.utc)
                if (datetime.now(timezone.utc) - captured).total_seconds() > self.shell.settings.detail_cache_ttl_seconds:
                    stale = " · STALE"
            except ValueError:
                stale = " · timestamp unavailable"
        self.query_one("#detail_status", Static).update(
            f"{self.minion_id} · {self.kind} · {source}" + (f" · captured {captured_at[:19]}" if captured_at else "") + stale)
        self.query_one("#detail_payload", Static).update(pretty(self.shell.client.redactor.value(value)))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id.startswith("detail_") and event.button.id not in {"detail_live", "detail_cached"}:
            if event.button.id == "detail_full_pillars":
                self.kind = "pillars"
                self.show_full_pillars = True
                asyncio.create_task(self.load(live=False))
                return
            self.kind = event.button.id.removeprefix("detail_")
            if self.kind == "pillars":
                self.show_full_pillars = False
            asyncio.create_task(self.load(live=False))
        elif event.button.id == "detail_live":
            asyncio.create_task(self.load(live=True))
        elif event.button.id == "detail_cached":
            asyncio.create_task(self.load(live=False))


class NodegroupsScreen(Screen):
    @property
    def shell(self) -> SaltTUI:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        from salt_tui.ui.workbench import WorkbenchSidebar, WorkbenchContext
        yield Header()
        yield WorkbenchSidebar()
        yield WorkbenchContext()
        yield Static("Nodegroups — membership means responding minions, not a complete inventory", classes="page-title")
        with Horizontal(classes="toolbar"):
            yield Button("Refresh selected", id="nodegroups_refresh")
            yield Button("Use selected", id="nodegroups_use", variant="primary")
            yield Button("Save selection", id="nodegroups_save")
            yield Button("Clear selection", id="nodegroups_clear")
        with Horizontal(id="split"):
            yield DataTable(id="nodegroups_table", cursor_type="row")
            with VerticalScroll(id="detail-scroll"):
                yield Static("Select a nodegroup; Space resolves and adds its responding members.", id="nodegroups_detail")
        yield Footer()

    async def on_mount(self) -> None:
        table = self.query_one("#nodegroups_table", DataTable)
        table.add_columns("Nodegroup", "Responding members")
        await self.refresh_data()

    async def refresh_data(self) -> None:
        detail = self.query_one("#nodegroups_detail", Static)
        try:
            groups = self.shell.configured_nodegroups()
        except Exception as exc:
            self.rows = {}
            self.query_one("#nodegroups_table", DataTable).clear()
            detail.update(f"Nodegroups unavailable: {self.shell.client.redactor.text(str(exc))[:180]}")
            return
        self.rows = {name: [] for name in groups}
        table = self.query_one("#nodegroups_table", DataTable)
        table.clear()
        for name in groups:
            table.add_row(name, "not resolved", key=name)
        detail.update("Select a nodegroup and choose Refresh selected. Space resolves it and adds responding members to the temporary selection.")

    async def resolve_selected(self, add_selection: bool = False) -> None:
        table = self.query_one("#nodegroups_table", DataTable)
        if table.row_count == 0:
            return
        name = str(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)
        detail = self.query_one("#nodegroups_detail", Static)
        detail.update(f"Resolving responding members for {name}…")
        try:
            members = await self.shell.resolve_nodegroup(name)
        except Exception as exc:
            detail.update(f"Membership unavailable: {self.shell.client.redactor.text(str(exc))[:180]}")
            return
        self.rows[name] = members
        table.update_cell(name, "Responding members", str(len(members)))
        if add_selection:
            member_set = set(members)
            if member_set and member_set.issubset(self.shell.selected_minions):
                self.shell.selected_minions.difference_update(member_set)
                selection_note = "removed from"
            else:
                self.shell.selected_minions.update(member_set)
                selection_note = "added to"
        else:
            selection_note = "not changed"
        detail.update(f"{name}: {len(members)} responding members\n\n" + "\n".join(members) +
                      f"\n\nTemporary selection: {len(self.shell.selected_minions)} ({selection_note})")

    def action_toggle_selection(self) -> None:
        asyncio.create_task(self.resolve_selected(add_selection=True))

    BINDINGS = [("space", "toggle_selection", "Select responding members")]

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "nodegroups_refresh":
            asyncio.create_task(self.resolve_selected())
        elif event.button.id == "nodegroups_use":
            self.shell.use_selected_minions()
        elif event.button.id == "nodegroups_save":
            self.shell.save_selected_minions()
        elif event.button.id == "nodegroups_clear":
            self.shell.selected_minions.clear()
            self.query_one("#nodegroups_detail", Static).update("Temporary selection cleared.")
