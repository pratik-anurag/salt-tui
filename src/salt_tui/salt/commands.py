from __future__ import annotations

import shlex
from pathlib import Path
from salt_tui.config import Settings
from salt_tui.models import CommandSpec

COMMANDS = {"salt", "salt-call", "salt-run", "salt-key", "salt-cp"}
TARGET_FLAGS = {"glob": None, "grain": "-G", "pillar": "-I", "compound": "-C", "nodegroup": "-N", "list": "-L", "pcre": "-E"}


def build_argv(spec: CommandSpec, settings: Settings) -> list[str]:
    if spec.executable not in COMMANDS:
        raise ValueError(f"Unsupported Salt executable: {spec.executable}")
    if spec.target_type not in TARGET_FLAGS:
        raise ValueError(f"Unsupported target type: {spec.target_type}")
    path = getattr(settings, spec.executable.replace("-", "_"))
    argv = [path]
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
                       target_type=target_type if target_type != "local" else "glob", arguments=rest)


def is_mutating(spec: CommandSpec) -> bool:
    if spec.executable in {"salt-cp", "salt-key"}:
        return True
    readonly_prefixes = ("test.", "grains.", "pillar.", "schedule.list", "beacons.list", "config.", "sys.", "status.", "jobs.", "state.show_", "state.sls_exists", "saltutil.find_job", "saltutil.running")
    return not spec.function.startswith(readonly_prefixes)
