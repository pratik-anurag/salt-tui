import pytest

from salt_tui.config import Settings
from salt_tui.models import CommandSpec
from salt_tui.salt.python_client import SaltPythonBackend


class FakeLocalClient:
    def cmd(self, target, function, **kwargs):
        assert target == "web-*"
        assert kwargs["kwarg"]["test"] is True
        return {"web01": {"pkg_|-nginx_|-nginx_|-installed": {
            "__id__": "nginx", "__sls__": "nginx", "name": "nginx", "result": True,
            "changes": {}, "comment": "ok", "duration": 10}}}

    def cmd_async(self, target, function, **kwargs):
        assert kwargs["start_event"] is True
        return "20261004150112345678"


@pytest.mark.asyncio
async def test_python_backend_normalizes_sync_and_async():
    backend = SaltPythonBackend(Settings(), local_client_factory=FakeLocalClient)
    sync = await backend.run(CommandSpec(function="state.apply", target="web-*", test=True))
    assert sync.status == "success"
    assert sync.states[0].state_id == "nginx"
    live = await backend.run(CommandSpec(function="state.highstate", target="web-*", async_run=True))
    assert live.jid == "20261004150112345678"
    assert live.status == "running"
    assert await backend.cancel() is False
