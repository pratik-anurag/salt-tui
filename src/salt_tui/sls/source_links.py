from __future__ import annotations

from pathlib import Path, PureWindowsPath
import re

from salt_tui.config import Settings


def local_sls_paths(settings: Settings, env: str, sls: str) -> tuple[Path, ...]:
    """Return existing SLS files only when they resolve inside configured roots."""
    parts = sls.split(".")
    if (not sls or any(not part or part in {".", ".."} or "/" in part or "\\" in part
                       or "\x00" in part or PureWindowsPath(part).drive for part in parts)):
        return ()
    relative = Path(*parts)
    candidates = (relative.with_suffix(".sls"), relative / "init.sls")
    paths: list[Path] = []
    for root in settings.file_roots.get(env, []):
        try:
            resolved_root = root.resolve()
        except OSError:
            continue
        for candidate in candidates:
            try:
                path = (root / candidate).resolve()
                path.relative_to(resolved_root)
            except (OSError, ValueError):
                # Salt responses are untrusted here; never follow an absolute,
                # traversal, or symlinked path outside the configured root.
                continue
            if path.is_file():
                paths.append(path)
    return tuple(paths)


def locate_source(settings: Settings, env: str, sls: str, state_id: str) -> tuple[Path, int] | None:
    """Return a location only when one literal top-level declaration is found."""
    escaped = re.escape(state_id)
    pattern = re.compile(rf"^(?:{escaped}|['\"]{escaped}['\"]):\s*(?:#.*)?$")
    matches: list[tuple[Path, int]] = []
    for path in local_sls_paths(settings, env, sls):
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
