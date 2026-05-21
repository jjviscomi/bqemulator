"""User-defined routine runtimes.

See [ADR 0014](../docs/adr/0014-udf-materialization-strategy.md) for the
strategy pattern + eager-materialization design rationale.
"""

from __future__ import annotations

from bqemulator.udf.js_udf import JavaScriptUDFRuntime, JSUDFUnavailableError
from bqemulator.udf.runtime import UDFRegistry, UDFRuntime
from bqemulator.udf.sql_udf import SQLUDFRuntime
from bqemulator.udf.table_valued import TableValuedRuntime

__all__ = [
    "JSUDFUnavailableError",
    "JavaScriptUDFRuntime",
    "SQLUDFRuntime",
    "TableValuedRuntime",
    "UDFRegistry",
    "UDFRuntime",
]
