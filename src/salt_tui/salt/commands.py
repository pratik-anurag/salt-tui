from __future__ import annotations

import shlex
import shutil
from pathlib import Path
from salt_tui.config import Settings
from salt_tui.models import CommandSpec

COMMANDS = {"salt", "salt-call", "salt-run", "salt-key", "salt-cp"}
TARGET_FLAGS = {"glob": None, "grain": "-G", "pillar": "-I", "compound": "-C", "nodegroup": "-N", "list": "-L", "pcre": "-E"}
# salt-cp reserves -C for chunked transfer, unlike salt's compound target flag.
COPY_TARGET_FLAGS = {"glob": None, "grain": "-G", "nodegroup": "-N", "list": "-L", "pcre": "-E"}


def build_argv(spec: CommandSpec, settings: Settings) -> list[str]:
    if spec.executable not in COMMANDS:
        raise ValueError(f"Unsupported Salt executable: {spec.executable}")
    if spec.target_type not in TARGET_FLAGS:
        raise ValueError(f"Unsupported target type: {spec.target_type}")
    path = getattr(settings, spec.executable.replace("-", "_"))
    if spec.raw_argv is not None:
        return [path, *spec.raw_argv]
    argv = [path]
    if spec.executable == "salt-call" and spec.local_mode:
        argv.append("--local")
    if spec.executable == "salt":
        flag = TARGET_FLAGS[spec.target_type]
        if flag:
            argv.append(flag)
        argv.append(spec.target)
    if spec.function:
        argv.append(spec.function)
    argv.extend(spec.arguments)
    if spec.test:
        argv.append("test=True")
    if spec.saltenv:
        argv.append(f"saltenv={spec.saltenv}")
    if spec.pillarenv:
        argv.append(f"pillarenv={spec.pillarenv}")
    if spec.timeout is not None:
        if spec.timeout < 1:
            raise ValueError("Timeout must be positive")
        argv.append(f"--timeout={spec.timeout}")
    if spec.batch:
        argv.append(f"--batch={spec.batch}")
    if spec.async_run:
        if spec.executable != "salt":
            raise ValueError("Live asynchronous runs require the salt master CLI")
        argv.extend(["--async", "--start-event"])
    argv.extend(spec.options)
    if spec.executable in {"salt", "salt-call", "salt-run"} and not any(x.startswith("--out=") or x in ("--out", "--output") for x in argv):
        argv.append("--out=json")
    if spec.executable == "salt" and not spec.async_run and "--static" not in argv:
        argv.append("--static")
    return argv


def display_argv(argv: list[str]) -> str:
    return shlex.join(argv)


def parse_line(line: str, settings: Settings) -> CommandSpec:
    """Parse a Salt CLI line; no shell syntax or expansion is evaluated."""
    tokens = shlex.split(line)
    if not tokens:
        raise ValueError("Enter a Salt command")
    executable = Path(tokens.pop(0)).name
    if executable not in COMMANDS:
        raise ValueError("Command must start with salt, salt-call, salt-run, salt-key, or salt-cp")
    if executable in {"salt-key", "salt-cp"}:
        if not tokens:
            raise ValueError(f"{executable} requires arguments")
        if executable == "salt-key":
            read_action = tokens[0] in {"--list", "-L", "--finger", "-F"} and len(tokens) >= 2 and all(
                token in {"--out=json"} for token in tokens[2:])
            function = "key.list_all" if read_action and tokens[0] in {"--list", "-L"} else "key.finger_all" if read_action else "key.action"
            return CommandSpec(executable=executable, function=function,
                               target=tokens[1] if len(tokens) > 1 else "local", raw_argv=tokens, action_kind="keys")
        position = 0
        target_type = "glob"
        while position < len(tokens) and tokens[position].startswith("-"):
            flag = tokens[position]
            target_type = next((kind for kind, option in COPY_TARGET_FLAGS.items() if option == flag), target_type)
            position += 2 if flag in {"--out", "--output", "--config-dir", "-c", "--timeout", "-t"} else 1
        target = tokens[position] if len(tokens) > position else ""
        return CommandSpec(executable=executable, function="copy", target=target, target_type=target_type,
                           raw_argv=tokens, action_kind="file_copy")
    if tokens and tokens[0].startswith("-") and tokens[0] not in set(TARGET_FLAGS.values()) - {None} and tokens[0] != "--local":
        # Preserve unfamiliar CLI options exactly. Without an unambiguous parse,
        # classify the action as mutating rather than guessing a safe function.
        return CommandSpec(executable=executable, function="", target="unknown",
                           raw_argv=tokens, execution_context="master" if executable in {"salt", "salt-run"} else "masterless" if "--local" in tokens else "local",
                           local_mode="--local" in tokens)
    local_mode = executable == "salt-call" and "--local" in tokens
    if executable == "salt-call" and tokens and tokens[0] == "--local":
        tokens.pop(0)
    if executable == "salt":
        target_type = "glob"
        if tokens and tokens[0] in TARGET_FLAGS.values():
            flag = tokens.pop(0)
            target_type = next(k for k, v in TARGET_FLAGS.items() if v == flag)
        if len(tokens) < 2:
            raise ValueError("salt requires a target and function")
        target, function, *rest = tokens
    else:
        target_type, target = "local", "local"
        function, *rest = tokens if tokens else [""]
    # Preserve arbitrary Salt arguments exactly; structured controls are built separately.
    return CommandSpec(executable=executable, function=function, target=target,
                       target_type=target_type if target_type != "local" else "glob", arguments=rest,
                       local_mode=local_mode, execution_context="masterless" if local_mode else "local" if executable == "salt-call" else "master")


def is_mutating(spec: CommandSpec) -> bool:
    if spec.executable == "salt-cp":
        return True
    if spec.executable == "salt-key":
        return spec.function not in {"key.list_all", "key.finger_all"}
    readonly = {
        "test.ping", "test.version", "test.versions_report", "grains.items", "grains.item",
        "grains.get", "grains.ls", "pillar.items", "pillar.item", "pillar.get",
        "schedule.list", "beacons.list", "config.get", "config.items", "config.option",
        "sys.list_functions", "sys.argspec", "sys.doc", "sys.list_modules",
        "status.all_status", "status.cpuinfo", "status.meminfo", "status.diskusage",
        "jobs.list_jobs", "jobs.lookup_jid", "state.show_sls", "state.show_highstate",
        "state.show_lowstate", "state.sls_exists", "saltutil.find_job", "saltutil.running",
        "key.list_all", "key.finger_all",
    }
    return spec.function not in readonly


def execution_spec(settings: Settings, function: str, *, target: str = "*", target_type: str = "glob",
                   arguments: list[str] | None = None, context: str | None = None) -> CommandSpec:
    """Build an execution-module request in the selected Salt context."""
    chosen = context or settings.default_execution_context
    if chosen == "auto":
        chosen = "master" if shutil.which(settings.salt) else "local" if shutil.which(settings.salt_call) else "master"
    if chosen not in {"master", "local", "masterless"}:
        raise ValueError("Unsupported execution context")
    return CommandSpec(executable="salt" if chosen == "master" else "salt-call", function=function,
                       target=target if chosen == "master" else "local", target_type=target_type,
                       arguments=list(arguments or []), local_mode=chosen == "masterless", execution_context=chosen)


def key_action(settings: Settings, action: str, key_id: str) -> CommandSpec:
    if action not in {"accept", "reject", "delete"}:
        raise ValueError("Unsupported key action")
    if not key_id or key_id.lower() == "all" or any(c in key_id for c in "*?[]") or key_id.startswith("-"):
        raise ValueError("Key actions require one exact key ID")
    flag = {"accept": "-a", "reject": "-r", "delete": "-d"}[action]
    return CommandSpec(executable="salt-key", function=action, target=key_id,
                       raw_argv=[flag, key_id, "-y"], action_kind="key_" + action)


def copy_action(settings: Settings, source: Path, destination: str, target: str,
                target_type: str = "glob") -> CommandSpec:
    if not source.is_file() or not destination or not target or target.startswith("-"):
        raise ValueError("Choose one readable source file, destination, and target")
    import os
    if not os.access(source, os.R_OK):
        raise ValueError("Source file is not readable")
    flag = COPY_TARGET_FLAGS.get(target_type)
    if target_type not in COPY_TARGET_FLAGS:
        raise ValueError("salt-cp supports glob, grain, nodegroup, list, or pcre targets")
    args = [*([flag] if flag else []), "--out=json", target, str(source), destination]
    return CommandSpec(executable="salt-cp", function="copy", target=target,
                       target_type=target_type, raw_argv=args, action_kind="file_copy")
