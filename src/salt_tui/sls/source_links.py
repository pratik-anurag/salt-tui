from __future__ import annotations

from pathlib import Path
import re

from salt_tui.config import Settings


def locate_source(settings: Settings, env: str, sls: str, state_id: str) -> tuple[Path, int] | None:
    """Return a location only when one literal top-level declaration is found."""
    relative = Path(*sls.split("."))
    candidates = [relative.with_suffix(".sls"), relative / "init.sls"]
    escaped = re.escape(state_id)
    pattern = re.compile(rf"^(?:{escaped}|['\"]{escaped}['\"]):\s*(?:#.*)?$")
    matches: list[tuple[Path, int]] = []
    for root in settings.file_roots.get(env, []):
        for candidate in candidates:
            path = root / candidate
            if not path.is_file():
                continue
            try:
                for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
                    if pattern.match(line):
                        matches.append((path, number))
            except OSError:
                continue
    return matches[0] if len(matches) == 1 else None


def include_hints(path: Path) -> list[str]:
    """Read only simple literal include lists; Jinja/dynamic includes are left to Salt."""
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return []
    includes: list[str] = []
    inside = False
    for line in lines:
        if line.strip() == "include:":
            inside = True
            continue
        if inside and line and not line[0].isspace():
            break
        if inside:
            match = re.match(r"^\s+-\s+([\w.]+)\s*(?:#.*)?$", line)
            if match:
                includes.append(match.group(1))
    return includes


def extend_hints(path: Path) -> list[str]:
    """Return literal IDs under a top-level extend block as source hints."""
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return []
    ids: list[str] = []
    inside = False
    for line in lines:
        if line.strip() == "extend:" and not line[:1].isspace():
            inside = True
            continue
        if inside and line and not line[0].isspace():
            break
        if inside:
            match = re.match(r"^  ([\w.-]+):\s*(?:#.*)?$", line)
            if match:
                ids.append(match.group(1))
    return ids
