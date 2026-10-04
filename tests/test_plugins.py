from salt_tui import plugins


def test_plugin_registry_loads_entrypoints_and_isolates_failures(monkeypatch):
    class Good:
        def register(self, registry):
            registry.register_command("hello", lambda app: None)
            registry.register_parser(lambda raw, argv: {"parsed": True})
    class Entry:
        def __init__(self, name, value): self.name, self.value = name, value
        def load(self):
            if isinstance(self.value, Exception): raise self.value
            return self.value
    monkeypatch.setattr(plugins, "entry_points", lambda group: [Entry("good", Good), Entry("bad", RuntimeError("failure"))])
    registry = plugins.load_plugins()
    assert "hello" in registry.commands
    assert len(registry.parsers) == 1
    assert len(registry.errors) == 1
