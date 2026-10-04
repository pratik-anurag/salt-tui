"""Fail a tagged release if its version is inconsistent or already on PyPI."""

from __future__ import annotations

import os
from pathlib import Path
import re
import tomllib
from urllib.error import HTTPError
from urllib.request import urlopen


def main() -> None:
    version = tomllib.loads(Path("pyproject.toml").read_text())["project"]["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?", version):
        raise SystemExit(f"Unsupported release version: {version}")
    tag = os.environ.get("GITHUB_REF_NAME", "")
    if tag != f"v{version}":
        raise SystemExit(f"Tag {tag!r} does not match package version v{version}")
    url = f"https://pypi.org/pypi/salt-tui/{version}/json"
    try:
        with urlopen(url, timeout=15) as response:
            if response.status == 200:
                raise SystemExit(f"salt-tui {version} already exists on PyPI")
    except HTTPError as error:
        if error.code != 404:
            raise
    print(f"Release v{version}: tag matches and version is not on PyPI")


if __name__ == "__main__":
    main()
