from __future__ import annotations

from pathlib import Path
from salt_tui.config import Settings


def files(settings: Settings, env: str, limit: int = 10000) -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    for root in settings.file_roots.get(env, []):
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.is_file():
                found.append((str(path.relative_to(root)), path))
                if len(found) >= limit:
                    return sorted(found)
    return sorted(found)


def sls_name(relative: str) -> str | None:
    path = Path(relative)
    if path.suffix != ".sls":
        return None
    parts = path.with_suffix("").parts
    if parts[-1] == "init":
        parts = parts[:-1]
    return ".".join(parts)
