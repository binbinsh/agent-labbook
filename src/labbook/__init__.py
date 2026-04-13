from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

__all__ = ["__version__"]

try:
    __version__ = version("agent-labbook")
except PackageNotFoundError:  # pragma: no cover - running from source without install
    __version__ = "0.0.0-dev"
