# Architecture

The application uses Textual screens over a Salt service layer. The UI sends `CommandSpec` values to `SubprocessSaltClient`, which builds argument arrays and invokes Salt with `asyncio.create_subprocess_exec`. It never invokes a shell. Salt's JSON output is kept raw and normalized into minion and state rows. `Database` stores a run and its related records in one transaction through `asyncio.to_thread` and uses SQLite WAL mode. The source explorer reads configured `file_roots` for local files and asks `cp.list_states` for target-visible state names. Compiled high/low data and requisites come from Salt, so local source is never treated as authoritative compilation.

Stage 2 Phase A adds an independent `EventSource` interface with a Salt Python adapter and a `salt-run state.event` adapter. `EventMonitor` owns subscription, reconnect, a bounded memory ring, and a bounded persistence queue. Migration 002 adds events and JID keyed run progress. Correlation uses exact JIDs only; it does not infer ownership from similar function names or time windows.

## Data flow

1. User types a Salt CLI command. `parse_line` uses `shlex.split`, then `build_argv` adds JSON output where suitable.
2. The command screen displays the exact generated command and requires confirmation for mutating commands.
3. The service streams stdout/stderr into a bounded UI log and retains the full raw result.
4. The parser extracts state return dictionaries; the database writes run, minion, state, failure, and log rows.
5. History and state screens read indexed, paginated records and support failure navigation.

## Boundaries

`SaltClient` is a protocol, so a Salt Python API or remote API backend can later replace the CLI client. UI code has no subprocess calls. SQLite schema changes use numbered SQL migrations. The source browser is a navigation aid; Salt's compiled output remains authoritative. Local `salt-call` and master `salt` CLI workflows share the same run history and review screens. Live progress requires a reachable Salt event bus; synchronous runs still appear in the tracker after they finish.

## Reliability

The UI stays responsive through asyncio subprocesses and thread backed SQLite/file operations. History and log queries have limits. Normal test suite uses fixtures and a mocked subprocess and needs no Salt master. Errors retain the command, stdout, and stderr for reproduction.
