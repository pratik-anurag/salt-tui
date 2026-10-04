from datetime import datetime, timezone

import pytest

from salt_tui.analytics.comparison import detailed_compare
from salt_tui.analytics.failures import failure_signature
from salt_tui.analytics.search import parse_history_query
from salt_tui.models import RunResult, StateResult
from salt_tui.storage.database import Database


def state(duration: float, result: bool = True) -> StateResult:
    return StateResult("web01", "nginx-service", "service", "running", "nginx", "nginx.service",
                       result, {}, "Service failed at 2026-10-04T15:01:00 pid 1234" if not result else "ok",
                       duration, None, {"result": result})


def test_failure_normalization_and_search_ast():
    first = "Service failed at 2026-10-04T15:01:00 pid 1234 /tmp/job-abc"
    second = "Service failed at 2026-10-05T16:02:03 pid 9876 /tmp/job-def"
    assert failure_signature(first) == failure_signature(second)
    query = parse_history_query("result:failed sls:nginx since:7d", datetime(2026, 10, 4, tzinfo=timezone.utc))
    assert [(term.field, term.value) for term in query.terms[:2]] == [("result", "failed"), ("sls", "nginx")]
    assert query.terms[2].value.startswith("2026-09-27")
    with pytest.raises(ValueError):
        parse_history_query("result:broken")


def test_detailed_comparison():
    old = dict(minion_id="web01", sls="nginx", state_id="nginx", state_module="service", state_function="running",
               name="nginx", result=1, changes_json="{}", comment="ok", duration_ms=100)
    new = {**old, "result": 0, "comment": "failed", "duration_ms": 500}
    item = detailed_compare([old], [new])[0]
    assert {"REGRESSED", "CHANGED", "SLOWER"}.issubset(item["categories"])


@pytest.mark.asyncio
async def test_failure_groups_search_and_duration_stats(tmp_path):
    db = Database(tmp_path / "history.db")
    await db.migrate()
    for index, duration in enumerate((100, 110, 120, 130, 600)):
        run = RunResult(f"salt web01 state.sls nginx {index}", ["salt", "web01", "state.sls", "nginx"],
                        "web01", "glob", "state.sls")
        s = state(duration, result=index != 4)
        run.states = [s]
        run.parsed = {"web01": {"service_|-nginx-service_|-nginx_|-running": s.raw}}
        run.status = "failed" if index == 4 else "success"
        run.exit_code = 2 if index == 4 else 0
        await db.save_run(run)
    query = parse_history_query("minion:web01 sls:nginx result:failed")
    assert len(await db.search_runs(query)) == 1
    groups = await db.failure_groups()
    assert groups[0]["occurrences"] == 1
    stats = await db.slow_states()
    assert stats[0]["samples"] == 5
    assert stats[0]["median_ms"] == 120
    assert stats[0]["p95_ms"] == 600
    assert stats[0]["regressed"]
