# Getting help

Start with the [README](README.md), [keyboard shortcuts](docs/keys.md), and [sample configuration](sample-config.toml). Check the existing [issues](https://github.com/pratik-anurag/salt-tui/issues) for similar questions or bugs.

For a usage question or a reproducible problem, open an [issue](https://github.com/pratik-anurag/salt-tui/issues/new/choose). Choose the bug report template for unexpected behavior and the feature request template for a proposed change. Include your Salt TUI and Python versions, operating system, relevant Salt CLI tools, and steps to reproduce. Say whether the issue happens with local `salt-call`, a master target, or without Salt installed.

You can run `salt-tui diagnostics --output ./salt-tui-diagnostics.zip` to collect troubleshooting information. Review the archive before attaching it: Salt command output and logs may contain sensitive data. Never post secrets or live infrastructure details in a public issue.

For a suspected vulnerability, follow [SECURITY.md](SECURITY.md) instead of opening a public issue. For contribution guidance, see [CONTRIBUTING.md](CONTRIBUTING.md).
