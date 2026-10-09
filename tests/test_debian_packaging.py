import os
from pathlib import Path
import re
import tomllib


ROOT = Path(__file__).parents[1]


def test_debian_package_metadata_matches_project_version():
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    changelog = (ROOT / "debian/changelog").read_text()
    control = (ROOT / "debian/control").read_text()
    rules = ROOT / "debian/rules"

    assert re.match(rf"salt-tui \({re.escape(version)}-1\) noble; urgency=medium", changelog)
    assert "Package: salt-tui" in control
    assert "pybuild-plugin-pyproject" in control
    assert os.access(rules, os.X_OK)
