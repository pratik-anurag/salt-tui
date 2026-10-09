"""Prepare a patch release on the fortnightly publishing cadence."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from email.utils import format_datetime
import os
from pathlib import Path
import re
import subprocess
import tomllib


FIRST_RELEASE_DATE = date(2026, 10, 19)


def prepare_debian_changelog(current: str, version: str, today: date) -> None:
    """Prepend a source-package entry that matches the upstream release."""
    path = Path("debian/changelog")
    changelog = path.read_text()
    match = re.match(r"^salt-tui \(([^)]+)\) noble; urgency=medium\n", changelog)
    expected = f"{current}-1"
    if not match or match.group(1) != expected:
        raise SystemExit(f"Expected Debian version {expected}; found {match.group(1) if match else 'none'}")
    timestamp = format_datetime(datetime.combine(today, time.min, tzinfo=timezone.utc))
    entry = (
        f"salt-tui ({version}-1) noble; urgency=medium\n\n"
        f"  * Release {version}.\n\n"
        f" -- Pratik Anurag <panurag247365@gmail.com>  {timestamp}\n\n"
    )
    path.write_text(entry + changelog)


def due(today: date) -> bool:
    days = (today - FIRST_RELEASE_DATE).days
    return days >= 0 and (days // 7) % 2 == 0


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def prepare(today: date) -> str | None:
    if not due(today):
        print(f"No release due in the week of {today}")
        return None

    last_tag = git("describe", "--tags", "--match", "v[0-9]*", "--abbrev=0")
    if git("rev-list", "--count", f"{last_tag}..HEAD") == "0":
        print(f"No commits since {last_tag}; skipping release")
        return None

    project_file = Path("pyproject.toml")
    project_text = project_file.read_text()
    current = tomllib.loads(project_text)["project"]["version"]
    if current != last_tag.removeprefix("v"):
        raise SystemExit(f"Expected package version {last_tag.removeprefix('v')}; found {current}")
    if not re.fullmatch(r"\d+\.\d+\.\d+", current):
        raise SystemExit(f"Automatic releases require a stable version: {current}")

    major, minor, patch = map(int, current.split("."))
    version = f"{major}.{minor}.{patch + 1}"
    old = f'version = "{current}"'
    if project_text.count(old) != 1:
        raise SystemExit("Could not locate the package version exactly once")

    changelog_file = Path("CHANGELOG.md")
    changelog = changelog_file.read_text()
    heading = "## Unreleased\n"
    if changelog.count(heading) != 1:
        raise SystemExit("Expected exactly one Unreleased changelog section")

    project_file.write_text(project_text.replace(old, f'version = "{version}"', 1))
    changelog_file.write_text(
        changelog.replace(heading, f"{heading}\n## {version} — {today.isoformat()}\n", 1)
    )
    prepare_debian_changelog(current, version, today)
    print(f"Prepared v{version} from {last_tag}")
    return f"v{version}"


def main() -> None:
    tag = prepare(datetime.now(timezone.utc).date())
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a") as stream:
            stream.write(f"release={'true' if tag else 'false'}\n")
            if tag:
                stream.write(f"tag={tag}\n")


if __name__ == "__main__":
    main()
