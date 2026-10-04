from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Static

from salt_tui.models import SaltEvent
from salt_tui.salt.event_filters import parse_event_filter

if TYPE_CHECKING:
    from salt_tui.app import SaltTUI


def pretty(value: object) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)


class EventScreen(Screen):
    BINDINGS = [("space", "toggle_pause", "Pause/resume"), ("end", "follow", "Follow")]

    @property
    def shell(self) -> SaltTUI:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Event stream — Space pauses, End follows", classes="page-title")
        yield Static("", id="event_status")
        with Horizontal(classes="toolbar"):
            yield Input(placeholder="Filter tag, minion, JID, function, category, or text", id="event_filter")
            yield Button("Pause", id="pause")
            yield Button("Follow: on", id="follow")
        with Horizontal(id="split"):
            yield DataTable(id="event_table", cursor_type="row")
            with VerticalScroll(id="detail-scroll"):
                yield Static("Select an event", id="event_detail")
        yield Footer()

    async def on_mount(self) -> None:
        table = self.query_one("#event_table", DataTable)
        table.add_columns("Time", "Category", "Tag", "Minion", "JID", "Summary")
        self.paused = False
        self.following = True
        self._seen: dict[str, SaltEvent | dict] = {}
        self.shell.events.subscribers.add(self._receive)
        await self.refresh_data()
        self.set_interval(1, self._update_status)

    def on_unmount(self) -> None:
        self.shell.events.subscribers.discard(self._receive)

    def _update_status(self) -> None:
        monitor = self.shell.events
        self.query_one("#event_status", Static).update(
            f"Bus: {monitor.status} | Buffered: {len(monitor.recent)} | Persistence backlog: {monitor.queue.qsize()} | Dropped: {monitor.dropped}")

    async def refresh_data(self) -> None:
        try:
            event_filter = parse_event_filter(self.query_one("#event_filter", Input).value)
        except ValueError as exc:
            self.notify(str(exc), severity="error")
            return
        rows = await self.shell.db.events_filtered(event_filter, limit=500)
        table = self.query_one("#event_table", DataTable)
        table.clear()
        self._seen.clear()
        for row in reversed(rows):
            key = f"db-{row['id']}"
            self._seen[key] = row
            table.add_row(row["timestamp"][:19], row["category"], row["tag"], row["minion_id"] or "",
                          row["jid"] or "", row["summary"], key=key)
        self._update_status()

    def _receive(self, event: SaltEvent) -> None:
        if self.paused or not self.is_mounted:
            return
        try:
            event_filter = parse_event_filter(self.query_one("#event_filter", Input).value)
        except ValueError:
            return
        if not event_filter.matches(event):
            return
        table = self.query_one("#event_table", DataTable)
        key = f"live-{id(event)}"
        self._seen[key] = event
        table.add_row(event.timestamp[:19], event.category, event.tag, event.minion_id or "", event.jid or "", event.summary, key=key)
        if table.row_count > 500:
            first = next(iter(self._seen))
            table.remove_row(first)
            self._seen.pop(first, None)
        if self.following:
            table.move_cursor(row=table.row_count - 1)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        await self.refresh_data()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "pause":
            self.action_toggle_pause()
        elif event.button.id == "follow":
            self.action_follow()

    def action_toggle_pause(self) -> None:
        self.paused = not self.paused
        self.query_one("#pause", Button).label = "Resume" if self.paused else "Pause"

    def action_follow(self) -> None:
        self.following = not self.following
        self.query_one("#follow", Button).label = f"Follow: {'on' if self.following else 'off'}"

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        value = self._seen.get(str(event.row_key.value))
        if isinstance(value, SaltEvent):
            self.query_one("#event_detail", Static).update(pretty(self.shell.client.redactor.value(value.payload)))
        elif isinstance(value, dict):
            self.query_one("#event_detail", Static).update(pretty(self.shell.client.redactor.value(json.loads(value["payload_json"]))))


class LiveRunScreen(Screen):
    BINDINGS = [("f", "next_failure", "Next failed minion"), ("n", "next_failed_state", "Next failed state"),
                ("o", "open_failed_source", "Failed source")]

    @property
    def shell(self) -> SaltTUI:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(self.shell.breadcrumb_text(), classes="breadcrumb")
        yield Static("Run tracker — progress, failures, and logs", classes="page-title")
        yield Static("", id="live_header")
        yield Static("", id="run_summary")
        yield Static("f Next failed minion · n Next failed state · o Open failed source · Esc Back", id="tracker_hint")
        with Horizontal(classes="toolbar"):
            yield Button("State results", id="tracker_results")
            yield Button("Next failed state", id="tracker_next_failure")
            yield Button("Failed source", id="tracker_source")
            yield Button("Run logs", id="tracker_logs")
            yield Button("Refresh", id="tracker_refresh")
        with Horizontal(id="split"):
            yield DataTable(id="minion_progress", cursor_type="row")
            with VerticalScroll(id="detail-scroll"):
                yield Static("Select a minion", id="live_detail")
        yield Footer()

    async def on_mount(self) -> None:
        table = self.query_one("#minion_progress", DataTable)
        for label, key in (("Minion", "minion"), ("Status", "status"), ("States", "states"), ("Return code", "return_code")):
            table.add_column(label, key=key)
        self._rendered: dict[str, tuple[str, str, str]] = {}
        self._shown_run_id: int | None = None
        self._rows: dict[str, dict] = {}
        self._selected_failure: tuple[str, str] | None = None
        self._failure_rows: list[dict] = []
        self._failure_index = 0
        self._base_detail = ""
        self._shown_minion: str | None = None
        self.query_one("#tracker_source", Button).disabled = True
        self.query_one("#tracker_next_failure", Button).disabled = True
        await self.refresh_data()
        table.focus()
        self.set_interval(1, self.refresh_data)

    async def refresh_data(self) -> None:
        run_id = self.shell.current_run_id
        if not run_id:
            self.query_one("#live_header", Static).update("No run selected. Start from SLS Explorer or History.")
            return
        run = await self.shell.db.run(run_id)
        if not run:
            return
        self.query_one("#live_header", Static).update(
            f"Run #{run_id} | {run['command_type']} | Target: {run['target_expression']} | JID: {run['jid'] or '?'} | Status: {run['status']}")
        progress = await self.shell.db.progress(run_id)
        returned = await self.shell.db.run_minions(run_id)
        states = await self.shell.db.states(run_id)
        state_counts: dict[str, int] = {}
        for state in states:
            state_counts[state["minion_id"]] = state_counts.get(state["minion_id"], 0) + 1
        merged = {row["minion_id"]: dict(row) for row in progress}
        for row in returned:
            minion = row["minion_id"]
            if minion not in merged:
                merged[minion] = {"minion_id": minion, "status": "success" if row["success"] else "failed",
                                  "completed_states": state_counts.get(minion, 0), "total_states": state_counts.get(minion, 0),
                                  "return_code": row["return_code"]}
        counts = {status: sum(row["status"] == status for row in merged.values())
                  for status in ("success", "failed", "pending", "running", "no_return")}
        bus = self.shell.events.status
        if bus not in {"listening", "connected", "connecting"}:
            bus = "unavailable"
        self.query_one("#run_summary", Static).update(
            f"{len(merged)} known minions · {counts['success']} complete · {counts['failed']} failed · "
            f"{counts['pending'] + counts['running']} pending · {counts['no_return']} no return · "
            f"Event bus: {bus}")
        table = self.query_one("#minion_progress", DataTable)
        if self._shown_run_id != run_id:
            table.clear()
            self._rendered.clear()
            self._shown_minion = None
            self._failure_rows = []
            self._failure_index = 0
            self._shown_run_id = run_id
        self._rows = dict(sorted(merged.items()))
        if not self._rows:
            self._selected_failure = None
            self._failure_rows = []
            self.query_one("#tracker_source", Button).disabled = True
            self.query_one("#tracker_next_failure", Button).disabled = True
            self.query_one("#live_detail", Static).update(
                "No minion returns yet. The target may still be running or may not have responded.\n"
                "Use Refresh or check the event bus status above.")
        for row in self._rows.values():
            states = str(row["completed_states"]) + (f"/{row['total_states']}" if row["total_states"] is not None else "/?")
            values = (row["status"].upper().replace("_", " "), states,
                      str(row["return_code"]) if row["return_code"] is not None else "")
            old = self._rendered.get(row["minion_id"])
            if old is None:
                table.add_row(row["minion_id"], *values, key=row["minion_id"])
            elif old != values:
                for column, previous, current in zip(("status", "states", "return_code"), old, values):
                    if previous != current:
                        table.update_cell(row["minion_id"], column, current)
            self._rendered[row["minion_id"]] = values
        if table.row_count:
            try:
                selected = str(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)
            except Exception:
                selected = None
            if selected in self._rows:
                await self._show_minion(selected)

    async def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        await self._show_minion(str(event.row_key.value))

    async def _show_minion(self, minion: str) -> None:
        run_id = self.shell.current_run_id
        if not run_id or minion not in self._rows:
            return
        completed = [s for s in await self.shell.db.states(run_id) if s["minion_id"] == minion]
        live = await self.shell.db.live_states(run_id, minion)
        table = self.query_one("#minion_progress", DataTable)
        if not table.row_count:
            return
        try:
            if str(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value) != minion:
                return
        except Exception:
            return
        failures = [state for state in completed if state["result"] == 0]
        if self._shown_minion != minion:
            self._failure_index = 0
        self._shown_minion = minion
        self._failure_rows = failures
        self._failure_index %= max(1, len(failures))
        self.query_one("#tracker_next_failure", Button).disabled = len(failures) < 2
        lines = [f"Minion: {minion}", f"Status: {self._rows.get(minion, {}).get('status', '?')}",
                 f"Progress events: {len(live)}", ""]
        if completed:
            for state in completed:
                lines.append(f"{'✗' if state['result'] == 0 else '✓' if state['result'] == 1 else '?'} "
                             f"{state['state_id']}  {state['state_module']}.{state['state_function']}  "
                             f"{state['duration_ms'] or '?'}ms")
                if state["result"] == 0:
                    lines.append(f"  {state['comment'] or 'No error comment from Salt'}")
        else:
            lines.extend(f"{s['status'] or '?'} {s['state_id'] or s['event_tag']}" for s in live)
        self._base_detail = "\n".join(lines)
        self._show_failure_selection()

    def _show_failure_selection(self) -> None:
        if self._failure_rows:
            row = self._failure_rows[self._failure_index]
            self._selected_failure = (row["sls"], row["state_id"]) if row["sls"] else None
            detail = (f"\n\nSelected failure {self._failure_index + 1}/{len(self._failure_rows)}: "
                      f"{row['state_id']} · {row['sls'] or 'source unavailable'}\n"
                      "Press n for the next failed state, o to open its source.")
        else:
            self._selected_failure = None
            detail = ""
        self.query_one("#tracker_source", Button).disabled = self._selected_failure is None
        self.query_one("#live_detail", Static).update(self._base_detail + detail)

    def action_next_failed_state(self) -> None:
        if len(self._failure_rows) < 2:
            self.notify("No other failed state for this minion")
            return
        self._failure_index = (self._failure_index + 1) % len(self._failure_rows)
        self._show_failure_selection()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        run_id = self.shell.current_run_id
        if event.button.id == "tracker_results" and run_id:
            self.shell.open_run(run_id)
        elif event.button.id == "tracker_next_failure":
            self.action_next_failed_state()
        elif event.button.id == "tracker_source":
            self.action_open_failed_source()
        elif event.button.id == "tracker_refresh":
            await self.refresh_data()
        elif event.button.id == "tracker_logs" and run_id:
            rows = await self.shell.db.logs(run_id=run_id)
            self.query_one("#live_detail", Static).update(
                "Run logs:\n\n" + "\n".join(f"[{row['level']}] {row['message']}" for row in rows)
                if rows else "No logs recorded for this run.")

    def action_open_failed_source(self) -> None:
        if self._selected_failure:
            self.shell.open_source(*self._selected_failure)
        else:
            self.notify("Select a minion with a failed state", severity="warning")

    def action_next_failure(self) -> None:
        table = self.query_one("#minion_progress", DataTable)
        keys = list(self._rows)
        if not keys:
            return
        start = table.cursor_row or 0
        for step in range(1, len(keys) + 1):
            index = (start + step) % len(keys)
            if self._rows[keys[index]]["status"] in {"failed", "no_return"}:
                table.move_cursor(row=index)
                return
        self.notify("No failed or non-returning minions")
