# Contributing to Salt TUI

Thanks for helping improve Salt TUI. Bug reports, documentation fixes, tests, and code changes are welcome.

## Before you start

Check the existing issues before opening a new one. For a substantial feature or behavior change, open a feature request first so the design can be discussed. Small fixes can go straight to a pull request.

Salt TUI can execute commands against Salt minions. Use a disposable Salt environment for manual testing. Never include real credentials, minion data, or unredacted command output in an issue or pull request.

## Development setup

Use Python 3.11 or newer:

```sh
git clone https://github.com/pratik-anurag/salt-tui.git
cd salt-tui
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest -q
```

The normal test suite uses fixtures and mocked subprocesses; it does not require a Salt master. To explore the app locally, run `salt-tui`. Salt CLI tools must be installed separately to exercise live commands. See the [README](README.md) and [architecture notes](docs/architecture.md) for the current behavior and design.

## Pull requests

1. Create a branch from the current `main` branch.
2. Keep each pull request focused. Add or update tests for behavior changes and update user documentation when commands or screens change.
3. Run `python -m pytest -q`. For packaging changes, also run `python -m build`, `python -m twine check dist/*`, and `python scripts/check_dist.py`.
4. Add a short entry under `Unreleased` in [CHANGELOG.md](CHANGELOG.md) for user-visible changes.
5. Open a pull request describing the change, how it was tested, and any Salt environment used for manual testing. CI runs tests and packaging checks on pull requests.

Please do not bump the package version or create a release tag in a feature pull request. The [release process](docs/releasing.md) handles versioning and publication.

By submitting a contribution, you agree that it will be distributed under this project's [Apache-2.0 license](LICENSE).
