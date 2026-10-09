# Changelog

## Unreleased

- Add a Samples screen with bundled read-only Salt examples (`test.ping`, `test.version`, and `grains.item os`), command previews, and inline results.
- Require a readable master config before starting event monitoring, and show redacted, single-line summaries for event listener failures.
- Add a responsive task-oriented workbench shell, execution contexts, function catalog and form, guided exact-ID key actions, and single-file `salt-cp` transfer.
- Record execution context and action kind for new runs; classify read-only functions with an exact allowlist so grain and pillar writes require confirmation.

- Show recent runs for the selected SLS beside its source, with per-SLS failure/change counts and a link to the Run Tracker.
- Add a Homebrew tap formula and macOS installation instructions.
- Add Debian package metadata, Ubuntu CI package validation, and Launchpad PPA publication guidance.
- Update the Minions detail pane immediately after changing a temporary selection.
- Add read-only minion detail for live/cached grains, schedules, beacons, responding nodegroups, and opt-in session-only redacted pillars.
- Add temporary minion selection and list-target composition from minions and nodegroups.
- Browse local state files as a tree or query Salt for states available to a target and environment.
- Test a selected SLS, review planned changes, and apply it with an explicit target and command confirmation.
- Track synchronous and live runs together, including missing returns, failed-state source links, and run logs.

## 0.1.3

- Add SLS-specific keyboard guidance, clearer action names, a selected-view label, and compile progress and error feedback.
- Add screen breadcrumbs and Esc navigation to the previous screen.

## 0.1.2

- Make keyboard selection work in SLS Explorer and other row-based tables.
- Focus the SLS file list on entry and show the selected file's source immediately.
- Explain how to browse source states and distinguish them from run results.

## 0.1.1

- Fix screenshots and documentation links in the PyPI project description.

## 0.1.0

- Initial packaged Salt TUI with CLI execution, history, events, state views, and diagnostics.
- Added wheel, source distribution, pipx validation, and trusted publishing workflows.
