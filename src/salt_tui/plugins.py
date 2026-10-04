from __future__ import annotations

from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import Any, Callable, Protocol


class SaltTUIPlugin(Protocol):
    def register(self, registry: PluginRegistry) -> None: ...


@dataclass
class PluginRegistry:
    """Narrow extension points; plugins receive no direct database or widget internals."""
    screens: dict[str, Callable[[], Any]] = field(default_factory=dict)
    commands: dict[str, Callable[[Any], None]] = field(default_factory=dict)
    parsers: list[Callable[[str, list[str]], Any | None]] = field(default_factory=list)
    backends: dict[str, Callable[[Any], Any]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def register_screen(self, name: str, factory: Callable[[], Any]) -> None:
        if not name or name in self.screens:
            raise ValueError("Plugin screen name must be unique")
        self.screens[name] = factory

    def register_command(self, name: str, callback: Callable[[Any], None]) -> None:
        if not name or name in self.commands:
            raise ValueError("Plugin command name must be unique")
        self.commands[name] = callback

    def register_parser(self, parser: Callable[[str, list[str]], Any | None]) -> None:
        self.parsers.append(parser)

    def register_backend(self, name: str, factory: Callable[[Any], Any]) -> None:
        if not name or name in self.backends:
            raise ValueError("Plugin backend name must be unique")
        self.backends[name] = factory


def load_plugins() -> PluginRegistry:
    registry = PluginRegistry()
    for entry in entry_points(group="salt_tui.plugins"):
        try:
            plugin = entry.load()
            if isinstance(plugin, type):
                plugin = plugin()
            plugin.register(registry)
        except Exception as exc:
            registry.errors.append(f"{entry.name}: {type(exc).__name__}: {exc}")
    return registry
