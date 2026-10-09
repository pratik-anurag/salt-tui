"""Checks for the release cadence and version preparation."""

from datetime import date
from pathlib import Path

from scripts import prepare_scheduled_release as release


def test_fortnightly_cadence_accepts_delayed_run():
    assert not release.due(date(2026, 10, 12))
    assert release.due(date(2026, 10, 19))
    assert release.due(date(2026, 10, 21))
    assert not release.due(date(2026, 10, 26))
    assert release.due(date(2026, 11, 2))


def test_prepare_skips_when_no_new_commits(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(release, "git", lambda *args: "v0.1.3" if args[0] == "describe" else "0")
    assert release.prepare(date(2026, 10, 19)) is None


def test_prepare_bumps_patch_and_moves_changelog(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(release, "git", lambda *args: "v0.1.3" if args[0] == "describe" else "1")
    Path("pyproject.toml").write_text('[project]\nname = "salt-tui"\nversion = "0.1.3"\n')
    Path("CHANGELOG.md").write_text("# Changelog\n\n## Unreleased\n\n- New feature.\n\n## 0.1.3\n")
    Path("debian").mkdir()
    Path("debian/changelog").write_text(
        "salt-tui (0.1.3-1) noble; urgency=medium\n\n"
        "  * Initial Ubuntu package.\n\n"
        " -- Pratik Anurag <panurag247365@gmail.com>  Fri, 10 Oct 2026 00:00:00 +0000\n"
    )

    assert release.prepare(date(2026, 10, 19)) == "v0.1.4"
    assert 'version = "0.1.4"' in Path("pyproject.toml").read_text()
    assert "## 0.1.4 — 2026-10-19\n\n- New feature." in Path("CHANGELOG.md").read_text()
    assert Path("debian/changelog").read_text().startswith("salt-tui (0.1.4-1) noble; urgency=medium")
