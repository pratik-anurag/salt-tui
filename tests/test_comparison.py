from salt_tui.storage.comparison import compare_states


def row(state_id, result, changes="{}", duration=100):
    return dict(minion_id="web-01", sls="nginx", state_id=state_id, state_module="service",
                state_function="running", result=result, changes_json=changes, comment="ok", duration_ms=duration)


def test_comparison_categories():
    before = [row("a", 1), row("b", 0), row("c", 1)]
    after = [row("a", 0), row("b", 1), row("d", 1)]
    result = {item["identity"][2]: item["kind"] for item in compare_states(before, after)}
    assert result == {"a": "newly failing", "b": "recovered", "c": "removed", "d": "added"}
