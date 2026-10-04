import pytest

from salt_tui.models import RunResult
from salt_tui.salt.events import normalize_event
from salt_tui.salt.jobs import parse_jobs
from salt_tui.storage.database import Database


def test_job_summary_normalization():
    jobs = parse_jobs({"local": {"20261004150112345678": {
        "Function": "state.highstate", "Target": "web-*", "Target-type": "glob",
        "Returned": ["web01"], "Running": {"web02": 1234}}}})
    assert len(jobs) == 1
    assert jobs[0].expected_count == 2
    assert jobs[0].running == ("web02",)


@pytest.mark.asyncio
async def test_explicit_orchestration_child_jid(tmp_path):
    db = Database(tmp_path / "history.db")
    await db.migrate()
    parent = RunResult("salt-run state.orchestrate web", ["salt-run", "state.orchestrate", "web"],
                       "local", "glob", "state.orchestrate")
    parent.jid = "20261004150112345678"
    parent.parsed = {}
    parent_id = await db.save_run(parent)
    child_jid = "20261004150212345678"
    event = normalize_event({"tag": f"salt/job/{child_jid}/new", "data": {
        "jid": child_jid, "fun": "state.highstate", "tgt": "web-*", "minions": ["web01"],
        "orchestration_jid": parent.jid}})
    await db.save_events([event])
    children = await db.child_jobs(parent_id)
    assert len(children) == 1
    assert children[0]["child_jid"] == child_jid
