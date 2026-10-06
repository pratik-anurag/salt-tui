"""Read configured nodegroup names without treating local configuration as membership truth."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def names(master_config: Path) -> list[str]:
    """Return configured nodegroup names, or raise a useful read/parse exception."""
    loaded: Any = yaml.safe_load(master_config.read_text())
    groups = loaded.get("nodegroups", {}) if isinstance(loaded, dict) else {}
    if not isinstance(groups, dict):
        return []
    return sorted(str(name) for name in groups)
