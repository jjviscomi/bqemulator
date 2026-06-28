"""bqemulator — a local emulator for Google BigQuery.

This is the root package. Public re-exports here are intentionally minimal;
consumers should import from dedicated submodules.

Typical uses:
    from bqemulator import __version__
    from bqemulator.server import EmulatorServer
    from bqemulator.config import Settings
"""

from __future__ import annotations

__all__ = ["__version__"]

# Single source of truth for the version; hatchling reads this via
# `[tool.hatch.version] path = "src/bqemulator/__init__.py"`.
__version__ = "1.4.0"
