"""Salt TUI. Salt itself remains the authority for targeting and state compilation."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("salt-tui")
except PackageNotFoundError:
    __version__ = "0+unknown"
