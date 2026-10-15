"""Clara: a personal AI agent for one person, on one Linux machine, reachable only over Tailscale."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("clara")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.0.0+source"
