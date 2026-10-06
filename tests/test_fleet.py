import json
from pathlib import Path

import pytest

from salt_tui.config import Settings
from salt_tui.salt.nodegroups import names
from salt_tui.salt.redaction import Redactor
from salt_tui.storage.database import Database
from salt_tui.ui.fleet import path_values


def test_nodegroup_names_from_master_config(tmp_path: Path):
    master = tmp_path / "master"
    master.write_text("nodegroups:\n  web: 'G@role:web'\n  db: 'G@role:db'\n")
    assert names(master) == ["db", "web"]
    master.write_text("nodegroups: []\n")
    assert names(master) == []


def test_preview_paths_return_unavailable_marker():
    value = {"os": "Debian", "network": {"ipv4": ["10.0.0.1"]}}
    assert path_values(value, ["os", "network:ipv4:0", "missing"]) == {
        "os": "Debian", "network:ipv4:0": "10.0.0.1", "missing": "—",
    }


@pytest.mark.asyncio
async def test_detail_cache_overwrites_prunes_and_redacts(tmp_path: Path):
    db = Database(tmp_path / "history.db", Redactor())
    await db.migrate()
    await db.save_detail_snapshot("web01", "grains", {"os": "Debian", "api_key": "secret"}, limit=1)
    cached = await db.detail_snapshot("web01", "grains")
    assert cached and cached["payload"] == {"os": "Debian", "api_key": "[REDACTED]"}
    await db.save_detail_snapshot("web01", "grains", {"os": "Ubuntu"}, limit=1)
    assert (await db.detail_snapshot("web01", "grains"))["payload"] == {"os": "Ubuntu"}
    await db.save_detail_snapshot("web02", "grains", {"os": "Fedora"}, limit=1)
    assert await db.detail_snapshot("web01", "grains") is None
    assert (await db.detail_snapshot("web02", "grains"))["payload"] == {"os": "Fedora"}


def test_detail_settings_defaults_are_conservative():
    settings = Settings()
    assert settings.detail_cache_ttl_seconds == 900
    assert settings.detail_cache_minions_per_kind == 1000
    assert not settings.enable_pillar_details
    assert settings.pillar_public_paths == []
    assert "fqdn" in settings.minion_preview_grains
