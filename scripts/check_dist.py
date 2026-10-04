"""Check built archives for the runtime files and accidental private data."""

from __future__ import annotations

from pathlib import Path
import tarfile
from zipfile import ZipFile


REQUIRED = (
    "salt_tui/__init__.py",
    "salt_tui/__main__.py",
    "salt_tui/cli.py",
    "salt_tui/storage/database.py",
    "salt_tui/storage/migrations/001_initial.sql",
    "salt_tui/storage/migrations/007_matrix.sql",
)
FORBIDDEN_PARTS = {".env", "__pycache__", ".pytest_cache", ".venv", "work", ".ssh", ".gnupg"}
FORBIDDEN_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".log", ".pem", ".key", ".pyc"}


def inspect(names: list[str], archive: Path, wheel: bool) -> None:
    normalized = [name if wheel else "/".join(name.split("/")[1:]) for name in names]
    for required in REQUIRED:
        expected = required if wheel else f"src/{required}"
        if expected not in normalized:
            raise SystemExit(f"{archive}: missing {expected}")
    if wheel and not any(name.endswith(".dist-info/licenses/LICENSE") for name in normalized):
        raise SystemExit(f"{archive}: missing packaged Apache-2.0 license")
    if not wheel and "LICENSE" not in normalized:
        raise SystemExit(f"{archive}: missing LICENSE")
    for name in normalized:
        parts = set(Path(name).parts)
        if parts & FORBIDDEN_PARTS or Path(name).suffix in FORBIDDEN_SUFFIXES:
            raise SystemExit(f"{archive}: forbidden content {name}")
    print(f"{archive}: required files present; no forbidden paths")


def main() -> None:
    artifacts = list(Path("dist").iterdir())
    wheels = [path for path in artifacts if path.suffix == ".whl"]
    sdists = [path for path in artifacts if path.name.endswith(".tar.gz")]
    if len(wheels) != 1 or len(sdists) != 1:
        raise SystemExit("Expected exactly one wheel and one source distribution")
    with ZipFile(wheels[0]) as archive:
        inspect(archive.namelist(), wheels[0], True)
    with tarfile.open(sdists[0], "r:gz") as archive:
        inspect(archive.getnames(), sdists[0], False)


if __name__ == "__main__":
    main()
