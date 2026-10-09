# Salt TUI

[![CI](https://github.com/pratik-anurag/salt-tui/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/pratik-anurag/salt-tui/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/salt-tui)](https://pypi.org/project/salt-tui/)
[![Python](https://img.shields.io/pypi/pyversions/salt-tui)](https://pypi.org/project/salt-tui/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

PyPI - https://pypi.org/project/salt-tui/

A Textual frontend for Salt CLI operations, structured state returns, local history, and SLS exploration. Salt remains the source of truth for state rendering and execution.

## Install

Python 3.11 or newer and `pipx` are required. The package uses an isolated pipx environment and does not install into Salt's Python runtime. Install from PyPI with:

```sh
pipx install salt-tui
salt-tui
```

Upgrade with `pipx upgrade salt-tui` and uninstall with `pipx uninstall salt-tui`. Uninstalling leaves your configuration and history in your user directories. Check an installation without opening the TUI using `salt-tui --version` or `salt-tui --help`. Prereleases require an explicit request, such as `pipx install --pip-args='--pre' salt-tui`.

On macOS with [Homebrew](https://brew.sh/):

```sh
brew tap pratik-anurag/salt-tui
brew install pratik-anurag/salt-tui/salt-tui
```

Upgrade with `brew upgrade salt-tui` and remove it with `brew uninstall salt-tui`.

For a development checkout:

```sh
git clone https://github.com/pratik-anurag/salt-tui.git
cd salt-tui
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest
salt-tui
python -m salt_tui --version
```

To test a local release artifact, run `python -m build` and then `pipx install ./dist/salt_tui-X.Y.Z-py3-none-any.whl`. Replace `X.Y.Z` with the version you built. Salt CLI executables are discovered at runtime on `PATH`; Salt is not a Python dependency of this package. Ubuntu package maintainers can publish the included Debian packaging through a Launchpad PPA; see the [APT package guide](docs/apt.md). The PPA must be created and populated before `apt install salt-tui` is available.

Without Salt binaries, the UI opens with capability information and retains local history and SLS browsing. With `salt`, master commands are available; `salt-call` enables local minion commands. The app also detects `salt-run` and `salt-key` for their supported views. Live Salt behavior depends on the installed Salt CLI and access to a Salt environment. This package has been exercised on macOS; Linux is used in CI.

Open **Samples** from the Run sidebar, press `Ctrl+E`, or run `salt-tui samples` for three bundled, read-only Salt examples: `test.ping`, `test.version`, and `grains.item os`. Choose Master, Local configured, or Local masterless, set a target for Master, then run an example to see the exact command and result. They use your installed Salt CLI and configuration; they do not install Salt or create a demo master. Event monitoring is enabled only when the configured master file is readable, and a failed listener is summarized in one short status line.

## Use

Set `theme = "light"` in the config for a light palette, or press `Ctrl+Shift+L` to toggle it for the session.

The guided file-copy form supports glob, grain, nodegroup, list, and PCRE targets. Salt's own transport and debug logs are outside salt-tui's control; avoid distributing secrets with `salt-cp` unless the Salt environment is configured appropriately. Salt recommends its fileserver workflow for larger files.

The workbench has a sidebar on wide terminals and a `/` action menu on narrower terminals. Its context bar shows the active execution context and Salt availability. `f` opens the function workbench: choose Master, Local configured, or Local masterless, load `sys.list_functions`, inspect `sys.argspec` and `sys.doc`, fill arguments and a target, and review the exact command before running. Master catalogs require one explicit source minion; a function found there is **not** assumed available on every target. The catalog is in memory for this session and can be refreshed. `auto` context prefers `salt`, then `salt-call`; masterless uses `salt-call --local` without contacting a master. Missing binaries remain unavailable; salt-tui never installs or edits Salt automatically.

`k` opens guided key management for pending, accepted, rejected, and denied keys. The selected key's state and fingerprint are checked again before any exact-ID accept, reject, or delete action, and every write requires confirmation. `Ctrl+F` opens File Copy for one readable local file, a destination, and a Salt target. It shows file size, responding minions (not a complete inventory), the exact `salt-cp` command, and a confirmation. File contents are never stored in history. Both screens retain the generic `c` command runner for advanced Salt options. Salt SSH, Salt Cloud, and salt-api are not part of this milestone.

`c` opens the generic command runner. Enter a normal Salt command, review the exact command preview, then run it. For state changes, a confirmation displays target, action, environment, and test mode. `Test first` adds `test=True` to state functions. `Run Live` publishes a state job asynchronously and opens minion progress when the Salt event bus is available. `e` opens the event stream and `v` returns to the selected live run. `r` opens state results; `f` and `Shift+F` visit failed states. `h` opens persisted history; highlight one run and press `x`, then another and `x` to compare state results. `s` browses configured local file roots and invokes Salt's `state.show_sls` or `state.show_low_sls` for compiled views. `g` opens the compiled dependency graph, where `u` and `n` follow connected states and `o` opens source. `j` shows Salt runner job data.

`Ctrl+M` opens a paged state × minion matrix for the selected run, with fleet drift filters and per-state details.

Press `m` for minions and Enter to inspect a minion's grains, schedules, beacons, cached nodegroup membership, and (when explicitly enabled) pillars. Live detail is redacted before display; grains, schedules, beacons, and nodegroups are cached locally with timestamps, while pillar values are never persisted. Configure preview grain paths, cache limits, and pillar access in `config.toml`. Press Space in Minions to build a temporary selection, then use it as a Salt list target or explicitly save it as a target. `Ctrl+N` lists nodegroups from the configured master file; its membership is responding minions only, not a complete inventory.

To inspect states, press `s` for the SLS Explorer. **Local files** shows a tree from the configured `file_roots`; **From Salt** runs `cp.list_states` for the chosen target and environment. The origin column and source pane distinguish local files from states reported by Salt. Use the arrow keys to select a state, Shift+Tab to choose an action, and Enter to activate it. **Rendered state** runs `state.show_sls`, **Execution steps** runs `state.show_low_sls`, and **Dependencies** builds a graph from the compiled low state. If no local files appear, use the Settings button to inspect `file_roots` in `config.toml`, such as `base = ["/srv/salt"]`.

The SLS detail pane also shows the five most recent recorded runs containing results for the selected SLS, plus direct attempts that failed before returning state results. Failure and change counts refer to that SLS; result and duration refer to the entire run, which may include other states. Focus a history row and press Enter to open its Run Tracker. Highstate runs appear only when they returned results for the selected SLS.

Choose `local` for the configured `salt-call` minion, or enter a Salt target expression and target type to use the master CLI. **Test state** runs `state.apply <sls> test=True` and opens a review of planned changes and failures. **Apply state** asks for confirmation with the exact command, target, environment, and any matching dry-run summary. Applying opens the Run Tracker; select a minion to inspect its results, open its failed state source, or read that run's logs. History also opens runs in the tracker. Press `r` for the detailed State Results table, or Esc to return to the previous screen. A master and event bus are needed for live JID progress; completed local runs are still shown in the tracker.

Direct entry points open the corresponding screen:

```sh
salt-tui sls nginx
salt-tui run state.highstate
salt-tui history
salt-tui failures
salt-tui events
salt-tui intelligence
salt-tui performance
salt-tui runner
salt-tui orchestration
salt-tui targets
salt-tui matrix
salt-tui samples
salt-tui functions
salt-tui keys
salt-tui file-copy
salt-tui diagnostics --output ./salt-tui-diagnostics.zip
```

Configuration is read from `~/.config/salt-tui/config.toml` (or `$XDG_CONFIG_HOME/salt-tui/config.toml`). Copy `sample-config.toml` and adapt command paths and file roots. History defaults to `$XDG_DATA_HOME/salt-tui/history.db` or `~/.local/share/salt-tui/history.db`. It does not write into Salt's cache.

See [architecture](https://github.com/pratik-anurag/salt-tui/blob/main/docs/architecture.md), [plugin API](https://github.com/pratik-anurag/salt-tui/blob/main/docs/plugins.md), [keyboard shortcuts](https://github.com/pratik-anurag/salt-tui/blob/main/docs/keys.md), and the [release checklist](https://github.com/pratik-anurag/salt-tui/blob/main/docs/releasing.md).

![Salt TUI dashboard preview](https://raw.githubusercontent.com/pratik-anurag/salt-tui/main/docs/screenshots/dashboard.png)

![Salt TUI event stream preview](https://raw.githubusercontent.com/pratik-anurag/salt-tui/main/docs/screenshots/events.png)
