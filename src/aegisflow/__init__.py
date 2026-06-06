"""AegisFlow: autonomous multi-agent AI incident response engine."""

from importlib.metadata import PackageNotFoundError, version

__all__ = ["__version__"]

try:
    __version__ = version("aegisflow")
except PackageNotFoundError:
    __version__ = "0.1.0"
