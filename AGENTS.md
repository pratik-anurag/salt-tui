# Salt TUI agent guidance

Keep this file focused on repository-specific decisions. Use `README.md` for user workflows, `CONTRIBUTING.md` for contribution steps, `docs/architecture.md` for service boundaries, and `docs/releasing.md` when working on packaging or releases. Read the relevant document for the task; there is no need to read all of them for every change.

## Development and verification

- This is a Python 3.11+ Textual application with source under `src/salt_tui/` and tests under `tests/`.
- Install development dependencies with `python -m pip install -e '.[dev]'` in a virtual environment.
- Run affected tests during development and `python -m pytest -q` before finishing a code change. The normal suite uses fixtures and mocked subprocesses; it needs no Salt master.
- For packaging changes, also run `python -m build`, `python -m twine check dist/*`, and `python scripts/check_dist.py`.
- Use a disposable Salt environment for manual integration checks. Do not run state-changing commands against real targets as a test.

## Project boundaries

- Keep Salt subprocess execution in the service layer. Build argument arrays and use `asyncio.create_subprocess_exec`; do not invoke a shell or launch Salt commands from UI code.
- Treat Salt's compiled output as authoritative for rendered states and dependencies. Local SLS files are for browsing and source navigation.
- Preserve command previews, target details, dry-run review, and confirmation before applying state changes.
- Add numbered SQL migrations for persistent schema changes; do not rewrite migrations already in use.
- Keep logs, history, and diagnostics bounded, and avoid exposing credentials or sensitive Salt output in examples and test fixtures.

## Changes and releases

- Add or update focused tests for behavior changes. Update the README or relevant docs when user-facing controls or commands change, and add user-visible changes under `Unreleased` in `CHANGELOG.md`.
- Do not bump the package version or create a release tag for ordinary code changes. Follow `docs/releasing.md` for release work; the scheduled workflow publishes a patch release when an eligible fortnight has new commits.
- Preserve unrelated working-tree changes. Do not overwrite or commit another contributor's edits as part of a task.
