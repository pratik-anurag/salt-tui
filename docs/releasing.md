# Releasing Salt TUI

Versions use `MAJOR.MINOR.PATCH` with optional `aN`, `bN`, or `rcN` prerelease suffixes. The package version in `pyproject.toml` is authoritative. Release tags have a leading `v`, such as `v0.1.0` or `v0.2.0rc1`. The installed `salt-tui --version` reads distribution metadata.

## Repository setup

The release workflow assumes this project is at the root of `https://github.com/pratik-anurag/salt-tui`. At packaging time, its `main` branch contained only `LICENSE`; add the packaged project files and workflows to that repository before tagging. Create a GitHub environment named `pypi`. On PyPI, configure a Trusted Publisher for that repository, workflow `.github/workflows/release.yml`, and environment `pypi`. The workflow uses OpenID Connect and does not need a stored PyPI API token. The repository was reachable with Git credentials but its web page was not publicly visible, so confirm the intended visibility before release.

## Preflight

1. Confirm main branch CI passes and update `CHANGELOG.md`.
2. Set the new version in `pyproject.toml`; do not add a leading `v` there.
3. Build locally: `python -m build`.
4. Validate: `python -m twine check dist/*` and `python scripts/check_dist.py`.
5. Test a clean wheel with pipx, including `salt-tui --help` and `salt-tui --version`.
6. Confirm the version is unused on PyPI and the repository's Trusted Publisher is configured.
7. Commit, tag `vX.Y.Z`, and push the tag. Only a version tag triggers publication.
8. Check the GitHub Actions release run and the published PyPI artifacts.
9. Verify `pipx install salt-tui` and `salt-tui --version` from PyPI. For an upgrade, use `pipx upgrade salt-tui`.

The release workflow checks the tag against the package version, rejects an existing PyPI version, runs tests, builds both distributions, validates metadata and included files, and publishes with the official PyPA action. Database migrations happen when the app starts, never during installation.

## TestPyPI

For an optional manual rehearsal, build the distributions and upload them with `python -m twine upload --repository testpypi dist/*`. Use a unique prerelease version because TestPyPI versions cannot be overwritten. Copy the exact wheel download URL from the TestPyPI release page and install that URL with `pipx install 'https://test-files.pythonhosted.org/.../salt_tui-X.Y.Z-py3-none-any.whl'`. Pipx will resolve runtime dependencies from PyPI while the project wheel comes from TestPyPI. Do not treat a TestPyPI upload as authorization to publish to PyPI.
