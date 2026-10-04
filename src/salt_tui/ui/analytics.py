from __future__ import annotations

from textual.widgets import DataTable, Input

from salt_tui.ui.screens import TableScreen


class FailureIntelligenceScreen(TableScreen):
    title_text = "Recurring failures — grouped by normalized signature"
    columns = ("Signature", "Count", "Minions", "Median", "First", "Last")

    async def refresh_data(self) -> None:
        rows = await self.shell.db.failure_groups(self.query_one("#search", Input).value)
        self.rows = {str(index): row for index, row in enumerate(rows)}
        table = self.query_one("#table", DataTable); table.clear()
        for key, row in self.rows.items():
            table.add_row(row["signature"][:90], str(row["occurrences"]), str(row["minions"]),
                          f"{row['median_ms']:.0f} ms" if row["median_ms"] is not None else "-",
                          row["first_seen"][:19], row["last_seen"][:19], key=key)

    async def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        row = self.rows.get(str(event.row_key.value))
        if not row:
            return
        occurrences = await self.shell.db.failure_occurrences(row["signature"], 25)
        self.show_detail(f"{row['signature']}\n\n{row['occurrences']} occurrences across {row['minions']} minions\n"
                         f"First: {row['first_seen']}\nLast: {row['last_seen']}\n\nRecent:\n" +
                         "\n".join(f"#{item['run_id']} {item['minion_id']} {item['state_id']}: {item['error_text']}" for item in occurrences))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        row = self.rows.get(str(event.row_key.value))
        if row:
            self.shell.open_run(row["recent_run_id"])


class PerformanceScreen(TableScreen):
    title_text = "Slow states — statistics from local run history"
    columns = ("SLS", "State", "Module.Function", "Samples", "Mean", "Median", "P95", "Trend")

    async def refresh_data(self) -> None:
        rows = await self.shell.db.slow_states()
        term = self.query_one("#search", Input).value.lower()
        self.rows = {str(index): row for index, row in enumerate(rows)
                     if term in (row["sls"] + row["state_id"] + row["state_module"]).lower()}
        table = self.query_one("#table", DataTable); table.clear()
        for key, row in self.rows.items():
            table.add_row(row["sls"], row["state_id"], f"{row['state_module']}.{row['state_function']}",
                          str(row["samples"]), f"{row['mean_ms']:.0f} ms", f"{row['median_ms']:.0f} ms",
                          f"{row['p95_ms']:.0f} ms", "SLOWER" if row["regressed"] else "", key=key)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        row = self.rows.get(str(event.row_key.value))
        if row:
            self.show_detail(f"{row['sls']} / {row['state_id']}\n{row['state_module']}.{row['state_function']}\n\n"
                             f"Samples: {row['samples']}\nMean: {row['mean_ms']:.1f} ms\nMedian: {row['median_ms']:.1f} ms\n"
                             f"P95: {row['p95_ms']:.1f} ms\nMinimum: {row['min_ms']:.1f} ms\nMaximum: {row['max_ms']:.1f} ms\n"
                             f"Latest: {row['latest_ms']:.1f} ms" + (" — regression hint" if row["regressed"] else ""))
