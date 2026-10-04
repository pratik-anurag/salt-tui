# Releasing Salt TUI

Versions use `MAJOR.MINOR.PATCH` with optional `aN`, `bN`, or `rcN` prerelease suffixes. Release tags have a leading `v`. The installed `salt-tui --version` reads distribution metadata.

## Automatic releases

The [release workflow](../.github/workflows/release.yml) checks every Monday at 08:00 Asia/Kolkata (02:30 UTC). The first eligible date is 19 October 2026 and the cadence is every other Monday after that. GitHub Actions runs scheduled workflows on the default branch and may delay or omit a scheduled run during high load. The script accepts a delayed run during its scheduled week; an omitted run waits until the next eligible fortnight.

An eligible run compares `main` with its latest reachable version tag. If there are no new commits, it exits without building or publishing. Otherwise it increases the patch version, moves the `Unreleased` changelog entries under that version, runs tests, builds and validates the wheel and source distribution, commits the version change to `main`, creates the tag, and publishes to PyPI. A failed test or build stops before committing or tagging. A push rejected because `main` moved also stops publication.

The GitHub `pypi` environment and PyPI Trusted Publisher are configured for `.github/workflows/release.yml`. Publishing uses OpenID Connect; there is no stored PyPI API token. The scheduled job needs permission to push a release commit and tag to `main`. If branch rules prevent GitHub Actions from pushing, adjust those rules or use a different authorized release method before the first scheduled run.

To retry a failed publication after a tag has been created, manually run the **Release** workflow with that existing tag as its `tag` input. A version already on PyPI cannot be uploaded again. Do not create a new tag for the same version.

## Manual releases

For a minor, major, or prerelease version, set the new version in `pyproject.toml`, update `CHANGELOG.md`, run `python -m pytest -q`, `python -m build`, `python -m twine check dist/*`, and `python scripts/check_dist.py`. Test the wheel with pipx, confirm the version is unused on PyPI, commit, and push a matching `vX.Y.Z` tag. The tag triggers the same release workflow. Verify the GitHub Actions run and published PyPI artifacts, then test `pipx install salt-tui` and `salt-tui --version`.

Database migrations happen when the app starts, never during installation.

## TestPyPI

For an optional rehearsal, build the distributions and upload them with `python -m twine upload --repository testpypi dist/*`. Use a unique prerelease version because TestPyPI versions cannot be overwritten. Install the exact wheel URL shown on TestPyPI with pipx. Pipx resolves runtime dependencies from PyPI while the project wheel comes from TestPyPI.
