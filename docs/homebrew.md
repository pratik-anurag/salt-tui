# Homebrew formula

Salt TUI is distributed through this repository as a Homebrew tap formula. On macOS, install it with:

```sh
brew tap pratik-anurag/salt-tui
brew install pratik-anurag/salt-tui/salt-tui
```

The formula builds an isolated Python virtual environment from the PyPI source release and includes checksummed copies of every Python runtime dependency. Salt CLI executables remain optional runtime integrations; Homebrew installation does not install or configure Salt itself.

## Maintainer release step

After a new version has been published to PyPI, update `Formula/salt-tui.rb` before advertising it:

1. Replace the source archive URL and SHA-256 with the new PyPI source distribution.
2. Refresh the resource blocks using `brew update-python-resources` from a checkout installed as a Homebrew tap.
3. Build from source and run `salt-tui --version` on macOS.
4. Commit the formula update, then publish the PyPI release and formula update together.

Homebrew fetches the formula from the default branch of this tap. Formula updates must therefore reference a PyPI release that already exists.
