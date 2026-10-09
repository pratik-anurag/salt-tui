from pathlib import Path

import pytest
from textual.widgets import Input, Static

from salt_tui.app import SaltTUI
from salt_tui.config import Settings
from salt_tui.models import CommandSpec, RunResult
from salt_tui.salt.commands import build_argv, copy_action, execution_spec, is_mutating, key_action, parse_line
from salt_tui.storage.database import Database
from salt_tui.ui.workbench import WorkbenchSidebar


def offline_settings(path: Path) -> Settings:
    return Settings(database=path, enable_plugins=False,
                    salt="/nonexistent/salt", salt_call="/nonexistent/salt-call",
                    salt_run="/nonexistent/salt-run", salt_key="/nonexistent/salt-key",
                    salt_cp="/nonexistent/salt-cp")


def test_contexts_and_typed_argv(tmp_path: Path):
    settings = Settings(salt="/usr/bin/salt", salt_call="/usr/bin/salt-call", salt_key="/usr/bin/salt-key", salt_cp="/usr/bin/salt-cp")
    assert build_argv(execution_spec(settings, "test.ping", target="web-*", context="master"), settings) == [
        "/usr/bin/salt", "web-*", "test.ping", "--out=json", "--static"]
    assert build_argv(execution_spec(settings, "test.ping", context="local"), settings) == [
        "/usr/bin/salt-call", "test.ping", "--out=json"]
    assert build_argv(execution_spec(settings, "test.ping", context="masterless"), settings) == [
        "/usr/bin/salt-call", "--local", "test.ping", "--out=json"]
    assert build_argv(key_action(settings, "accept", "web-01"), settings) == [
        "/usr/bin/salt-key", "-a", "web-01", "-y"]
    source = tmp_path / "file with spaces.txt"
    source.write_text("safe")
    assert build_argv(copy_action(settings, source, "/tmp/dest with spaces", "web-*"), settings) == [
        "/usr/bin/salt-cp", "--out=json", "web-*", str(source), "/tmp/dest with spaces"]
    assert build_argv(copy_action(settings, source, "/tmp/dest", "role:web", "grain"), settings)[1:4] == ["-G", "--out=json", "role:web"]
    with pytest.raises(ValueError, match="salt-cp supports"):
        copy_action(settings, source, "/tmp/dest", "web-*", "compound")
    with pytest.raises(ValueError):
        key_action(settings, "delete", "web-*")
    with pytest.raises(ValueError):
        key_action(settings, "delete", "all")


def test_exact_read_only_allowlist_and_raw_families():
    for function in ("grains.setval", "pillar.set", "test.echo", "sys.reload_modules"):
        assert is_mutating(CommandSpec(function=function))
    for function in ("grains.items", "pillar.items", "sys.list_functions", "test.ping"):
        assert not is_mutating(CommandSpec(function=function))
    assert not is_mutating(parse_line("salt-key --list all", Settings()))
    assert is_mutating(parse_line("salt-key -a web-01 -y", Settings()))
    assert is_mutating(parse_line("salt-key --finger web-01 --accept web-02", Settings()))
    assert build_argv(parse_line("salt-call --local test.ping", Settings()), Settings())[:3] == ["salt-call", "--local", "test.ping"]
    assert build_argv(parse_line("salt-cp -G role:web '/tmp/a b' /tmp/d", Settings()), Settings()) == [
        "salt-cp", "-G", "role:web", "/tmp/a b", "/tmp/d"]
    assert parse_line("salt-cp --out=json -G role:web /tmp/a /tmp/b", Settings()).target == "role:web"
    assert build_argv(parse_line("salt-run jobs.list_jobs", Settings()), Settings()) == [
        "salt-run", "jobs.list_jobs", "--out=json"]


@pytest.mark.asyncio
async def test_migration_labels_new_and_legacy_runs(tmp_path: Path):
    db = Database(tmp_path / "history.db")
    await db.migrate()
    legacy = RunResult("salt '*' test.ping", ["salt", "*", "test.ping"], "*", "glob", "test.ping")
    new = RunResult("salt-call --local test.ping", ["salt-call", "--local", "test.ping"], "local", "glob", "test.ping")
    new.execution_context = "masterless"
    new.action_kind = "function"
    await db.save_run(legacy)
    await db.save_run(new)
    rows = await db.runs()
    assert (rows[0]["execution_context"], rows[0]["action_kind"]) == ("masterless", "function")
    assert rows[1]["execution_context"] == "legacy"


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_workbench_navigation_and_responsive_sidebar(tmp_path: Path, size):
    app = SaltTUI(offline_settings(tmp_path / "history.db"), initial="functions")
    async def empty_keys():
        return {}
    app.inspect_keys = empty_keys
    async with app.run_test(size=size) as pilot:
        sidebar = app.screen.query_one(WorkbenchSidebar)
        assert sidebar.display == (size[0] >= 100)
        app.action_show("keys")
        await pilot.pause()
        assert app.screen is app.get_screen("keys")
        app.action_show("file_copy")
        await pilot.pause()
        assert app.screen is app.get_screen("file_copy")
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is app.get_screen("keys")


@pytest.mark.asyncio
async def test_function_catalog_and_secret_preview(tmp_path: Path):
    app = SaltTUI(offline_settings(tmp_path / "history.db"), initial="functions")
    requests = []
    async def fake_inspect(spec):
        requests.append(spec.function)
        value = {"sys.list_functions": ["service.restart"], "sys.argspec": {"service.restart": {"args": ["name"]}},
                 "sys.doc": {"service.restart": "Restart a service"}}[spec.function]
        return RunResult("", [], spec.target, spec.target_type, spec.function, parsed={"web-01": value}, status="success", exit_code=0)
    app.execute_untracked = fake_inspect
    async with app.run_test(size=(120, 40)) as pilot:
        screen = app.screen
        screen.query_one("#catalog_source", Input).value = "web-01"
        await screen.refresh_catalog()
        assert "web-01" in str(screen.query_one("#catalog_status", Static).render())
        await screen.select_function("service.restart")
        await screen.select_function("service.restart")
        assert requests == ["sys.list_functions", "sys.argspec", "sys.doc"]
        assert screen.query_one("#function_arg_0", Input).placeholder == "name *"
        screen.query_one("#positional", Input).value = "nginx"
        screen.query_one("#secret_named", Input).value = "password=topsecret"
        screen.update_preview()
        preview = str(screen.query_one("#function_preview", Static).render())
        assert "[REDACTED]" in preview
        assert "topsecret" not in preview
        assert screen._spec().secret_values == ["topsecret"]


@pytest.mark.asyncio
async def test_compact_function_details_and_back(tmp_path: Path):
    app = SaltTUI(offline_settings(tmp_path / "history.db"), initial="functions")
    async with app.run_test(size=(80, 24)) as pilot:
        screen = app.screen
        assert screen.has_class("compact")
        screen.set_class(True, "show-function-detail")
        await pilot.press("escape")
        assert app.screen is screen
        assert not screen.has_class("show-function-detail")


@pytest.mark.asyncio
async def test_transient_busy_guard_and_malformed_key_output(tmp_path: Path):
    app = SaltTUI(offline_settings(tmp_path / "history.db"))
    app._busy = True
    with pytest.raises(RuntimeError, match="already running"):
        await app.inspect_keys()
    app._busy = False
    async def malformed(spec):
        return RunResult("", [], "local", "glob", spec.function, parsed=["not groups"], status="success", exit_code=0)
    app.execute_untracked = malformed
    with pytest.raises(RuntimeError, match="structured key list"):
        await app.inspect_keys()


@pytest.mark.asyncio
async def test_key_change_aborts_when_fingerprint_changes(tmp_path: Path):
    app = SaltTUI(offline_settings(tmp_path / "history.db"), initial="keys")
    calls = []
    async def keys():
        return {"web-01": "pending"}
    async def fingerprint(key_id):
        return "new-fingerprint"
    async def execute(spec):
        calls.append(spec)
    app.inspect_keys = keys
    app.key_fingerprint = fingerprint
    app.execute = execute
    async with app.run_test(size=(120, 40)):
        screen = app.screen
        screen.selected_id = "web-01"
        screen.keys = {"web-01": "pending"}
        screen.fingerprint = "old-fingerprint"
        await screen.prepare_action("accept")
        assert not calls


@pytest.mark.asyncio
async def test_copy_history_contains_no_file_contents(tmp_path: Path):
    app = SaltTUI(offline_settings(tmp_path / "history.db"))
    async def fake_run(spec, log=None):
        if log:
            await log("INFO", "private file contents")
        return RunResult("salt-cp web-01 /tmp/a /tmp/b", ["salt-cp", "web-01", "/tmp/a", "/tmp/b"],
                         "web-01", "glob", "copy", parsed={"web-01": "private file contents"},
                         stdout="private file contents", stderr="private file contents", status="success", exit_code=0)
    app.client.run = fake_run
    await app.db.migrate()
    spec = CommandSpec(executable="salt-cp", function="copy", target="web-01", action_kind="file_copy")
    run = await app.execute(spec)
    assert run.parsed == {"web-01": "returned"}
    rows = await app.db.runs()
    assert "private file contents" not in str(dict(rows[0]))
    assert "private file contents" not in str(await app.db.logs(run.id))


@pytest.mark.asyncio
async def test_marked_secret_redacted_even_with_backend_that_does_not_redact(tmp_path: Path):
    app = SaltTUI(offline_settings(tmp_path / "history.db"))
    async def fake_run(spec, log=None):
        if log:
            await log("INFO", "secret value topsecret")
        return RunResult("salt web-01 test.echo topsecret", ["salt", "web-01", "test.echo", "topsecret"],
                         "web-01", "glob", "test.echo", parsed={"web-01": "topsecret"},
                         stdout="topsecret", stderr="topsecret", status="success", exit_code=0)
    app.client.run = fake_run
    await app.db.migrate()
    spec = CommandSpec(function="test.echo", target="web-01", secret_values=["topsecret"])
    run = await app.execute(spec)
    assert "topsecret" not in str(run)
    assert "topsecret" not in str(dict((await app.db.runs())[0]))
    assert "topsecret" not in str(await app.db.logs(run.id))


def test_settings_context_validation(tmp_path: Path):
    config = tmp_path / "config.toml"
    config.write_text('default_execution_context = "masterless"\n')
    assert Settings.load(config).default_execution_context == "masterless"
    config.write_text('default_execution_context = "invalid"\n')
    with pytest.raises(ValueError):
        Settings.load(config)
