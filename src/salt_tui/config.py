from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os
import tomllib


def data_dir() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "salt-tui"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "salt-tui"


@dataclass
class Settings:
    database: Path = field(default_factory=lambda: data_dir() / "history.db")
    default_target: str = "*"
    default_saltenv: str = "base"
    file_roots: dict[str, list[Path]] = field(default_factory=lambda: {"base": [Path("/srv/salt")]})
    confirm_changes: bool = True
    refresh_seconds: int = 30
    history_limit: int = 10000
    log_limit: int = 100000
    salt: str = "salt"
    salt_call: str = "salt-call"
    salt_run: str = "salt-run"
    salt_key: str = "salt-key"
    salt_cp: str = "salt-cp"
    master_config: Path = Path("/etc/salt/master")
    live_return_timeout_seconds: int = 300
    event_retention: int = 100000
    redaction_patterns: list[str] = field(default_factory=list)
    confirmation_mode: str = "always"
    confirm_if_target_count: int = 20
    production_envs: list[str] = field(default_factory=lambda: ["prod", "production"])
    targets: dict[str, dict[str, str]] = field(default_factory=dict)
    backend: str = "cli"
    enable_plugins: bool = True

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "salt-tui/config.toml"
        settings = cls()
        if not path.exists():
            return settings
        raw = tomllib.loads(path.read_text())
        for key, value in raw.items():
            if not hasattr(settings, key):
                continue
            if key in {"database", "master_config"}:
                value = Path(value).expanduser()
            elif key == "file_roots":
                value = {env: [Path(p).expanduser() for p in paths] for env, paths in value.items()}
            setattr(settings, key, value)
        return settings
