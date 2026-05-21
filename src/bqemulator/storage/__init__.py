"""Storage layer — DuckDB engine, type mapping, Arrow bridge.

The storage layer owns the only DuckDB connection in the process. All
reads and writes pass through :class:`bqemulator.storage.engine.DuckDBEngine`.
"""

from __future__ import annotations

from bqemulator.storage.engine import DuckDBEngine

__all__ = ["DuckDBEngine"]
