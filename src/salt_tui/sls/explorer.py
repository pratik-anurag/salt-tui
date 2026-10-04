from __future__ import annotations

from pathlib import Path
from typing import Any
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


def available_states(parsed: Any) -> dict[str, int]:
    """Count the minions reporting each SLS from cp.list_states output."""
    if isinstance(parsed, list):
        returns = [parsed]
    elif isinstance(parsed, dict):
        returns = list(parsed.values())
    else:
        return {}
    counts: dict[str, int] = {}
    for value in returns:
        if not isinstance(value, list):
            continue
        for name in {item for item in value if isinstance(item, str) and item}:
            counts[name] = counts.get(name, 0) + 1
    return dict(sorted(counts.items()))


def local_tree_rows(found: list[tuple[str, Path]]) -> list[tuple[str, str, str | None, Path | None]]:
    """Return root, directory, and file rows for the local SLS table."""
    roots: dict[Path, list[tuple[str, Path]]] = {}
    for relative, path in found:
        root = path.parents[len(Path(relative).parts) - 1]
        roots.setdefault(root, []).append((relative, path))
    rows: list[tuple[str, str, str | None, Path | None]] = []
    for root, entries in sorted(roots.items()):
        rows.append((f"root:{root}", f"{root.name}/", None, None))
        directories: set[str] = set()
        for relative, path in sorted(entries):
            parts = Path(relative).parts
            for depth in range(1, len(parts)):
                directory = "/".join(parts[:depth])
                if directory not in directories:
                    directories.add(directory)
                    rows.append((f"dir:{root}:{directory}", f"{'│  ' * (depth-1)}▸ {parts[depth-1]}/", None, None))
            rows.append((f"file:{path}", f"{'│  ' * (len(parts)-1)}└─ {parts[-1]}", sls_name(relative), path))
    return rows
