"""SQL translation layer.

The core pipeline:

1. Pre-process (rewriters).
2. ``sqlglot.transpile(read="bigquery", write="duckdb")``.
3. Post-process (rule engine applies registered ``TranslationRule``s).

Entry point: :class:`bqemulator.sql.translator.SQLTranslator`.
"""

from __future__ import annotations

from bqemulator.sql.translator import SQLTranslator

__all__ = ["SQLTranslator"]
