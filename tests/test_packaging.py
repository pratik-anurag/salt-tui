from importlib import resources
from pathlib import Path
import os
import subprocess
import sys

from salt_tui import __version__


def test_module_version_has_no_runtime_side_effects(tmp_path: Path):
    environment = dict(os.environ, XDG_DATA_HOME=str(tmp_path / "data"), XDG_CONFIG_HOME=str(tmp_path / "config"))
    completed = subprocess.run([sys.executable, "-m", "salt_tui", "--version"],
                               capture_output=True, text=True, env=environment, check=True)
    assert completed.stdout.strip() == f"salt-tui {__version__}"
    assert not (tmp_path / "data").exists()


def test_migrations_are_package_resources():
    migrations = resources.files("salt_tui.storage").joinpath("migrations")
    assert migrations.joinpath("001_initial.sql").is_file()
    assert migrations.joinpath("007_matrix.sql").is_file()
