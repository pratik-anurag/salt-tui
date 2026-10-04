# Plugin registration surface

Plugins are opt-in through Python entry points in the `salt_tui.plugins` group. The core works with no plugins installed. Set `enable_plugins = false` to disable discovery.

```toml
[project.entry-points."salt_tui.plugins"]
my_extension = "my_package.plugin:MyPlugin"
```

```python
class MyPlugin:
    def register(self, registry):
        registry.register_command("hello", lambda app: app.notify("Hello"))
        registry.register_parser(lambda stdout, argv: None)
        # registry.register_screen("my-screen", lambda: MyScreen())
        # registry.register_backend("my-backend", lambda settings: MyBackend(settings))
```

Backend adapters must expose `run(spec, log)`, `cancel()`, and a `redactor` with `text`, `value`, and `argv` methods. Parser callbacks receive raw stdout and the executable argv; return `None` when they do not recognize an output format. Plugin screens and commands are loaded before the first screen is displayed. Plugin loading failures are recorded in the application log and do not stop the core app.

Only these registration methods are intended as plugin API. Other internal classes may change between minor releases.
