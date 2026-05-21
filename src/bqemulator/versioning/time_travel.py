"""``FOR SYSTEM_TIME AS OF`` rewriter.

BigQuery's `FOR SYSTEM_TIME AS OF <timestamp>` syntax lets a SELECT
read the state of a table as-of a past timestamp. SQLGlot parses this
as a ``version`` (``system_time``) modifier on a :class:`sqlglot.exp.Table`
node; the rewriter walks every such node, resolves the timestamp
against the :class:`SnapshotManager`, and either:

* Rewrites the table reference to the snapshot table in
  ``_bqemulator_snapshots`` (snapshot exists), or
* Clears the ``version`` (the target is between the last captured
  snapshot and now — the live table is the answer), or
* Raises :class:`OutOfRangeError` via the snapshot manager.

The rewriter runs *before* the BigQuery→DuckDB translator so it can
use SQLGlot's BigQuery dialect for accurate parsing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import sqlglot
from sqlglot import exp

from bqemulator.catalog.migrations.m002_versioning import SNAPSHOTS_SCHEMA
from bqemulator.domain.errors import InvalidQueryError

if TYPE_CHECKING:  # pragma: no cover
    from bqemulator.storage.engine import DuckDBEngine
    from bqemulator.versioning.snapshots import SnapshotManager


def rewrite_for_system_time(
    bq_sql: str,
    project_id: str,
    snapshots: SnapshotManager,
    engine: DuckDBEngine,
) -> str:
    """Resolve and rewrite every ``FOR SYSTEM_TIME AS OF ...`` clause.

    Short-circuits when the SQL doesn't contain the marker — the parse
    pass is the dominant cost here, so this keeps single-statement
    hot-path queries cheap.
    """
    if "SYSTEM_TIME" not in bq_sql.upper():
        return bq_sql

    try:
        tree = sqlglot.parse_one(bq_sql, read="bigquery")
    except Exception:  # noqa: BLE001 — fall through so later layers error cleanly
        return bq_sql

    modified = False
    for table_node in list(tree.find_all(exp.Table)):
        version = table_node.args.get("version")
        if version is None:
            continue

        target = _resolve_target_ts(version, engine)
        catalog = table_node.catalog
        db = table_node.db
        name = table_node.name
        src_project = catalog or project_id
        src_dataset = db
        src_table = name
        if not src_dataset or not src_table:
            # ``FOR SYSTEM_TIME`` on a bare or sub-query reference is not
            # a valid catalog lookup — leave as-is.
            continue

        snap = snapshots.resolve_time_travel(
            src_project,
            src_dataset,
            src_table,
            target,
        )

        if snap is None:
            # No snapshot captured yet — the live table is the answer.
            # Drop the version modifier and let the rest of the pipeline
            # rewrite the table the usual way.
            table_node.set("version", None)
        else:
            # Redirect the table node to the snapshot table.
            table_node.set("version", None)
            table_node.set("catalog", None)
            table_node.set(
                "db",
                exp.Identifier(this=snap.duckdb_schema, quoted=True),
            )
            table_node.set(
                "this",
                exp.Identifier(this=snap.duckdb_table, quoted=True),
            )
        modified = True

    if modified:
        return tree.sql(dialect="bigquery")
    return bq_sql


def _resolve_target_ts(
    version: exp.Expression,
    engine: DuckDBEngine,
) -> datetime:
    """Evaluate the ``AS OF <expr>`` expression to a Python datetime.

    The expression may be a literal timestamp, a function call such as
    ``TIMESTAMP '...'`` or ``TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL
    1 HOUR)``, or any scalar expression that evaluates to a timestamp.

    Evaluation strategy:

    1. If the expression is a string literal in ISO-8601 form, parse it
       directly with :func:`datetime.fromisoformat`. This is the
       overwhelmingly common case and avoids spinning up DuckDB +
       Python's ``pytz`` (which DuckDB requires only to handle
       ``TIMESTAMP WITH TIME ZONE``).
    2. Otherwise we fall back to DuckDB, but force the result type to
       a *naïve* ``TIMESTAMP`` and attach UTC ourselves — so the
       evaluation never crosses a ``TIMESTAMPTZ`` boundary that
       requires ``pytz`` at the Python layer.
    """
    expr_node = version.args.get("expression") or version.this
    if expr_node is None:
        raise InvalidQueryError("FOR SYSTEM_TIME AS OF requires an expression")

    # Fast path: literal timestamps and date strings. We accept the
    # ``Cast(Literal(...), TIMESTAMPTZ)`` shape SQLGlot emits for
    # ``TIMESTAMP '...'`` plus a bare string literal.
    literal_value = _extract_literal_timestamp(expr_node)
    if literal_value is not None:
        try:
            parsed = datetime.fromisoformat(literal_value)
        except ValueError as exc:
            raise InvalidQueryError(
                f"FOR SYSTEM_TIME AS OF requires an ISO-8601 timestamp: {exc}",
            ) from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed

    if isinstance(expr_node, exp.Expression):
        expr_sql = expr_node.sql(dialect="bigquery")
    else:
        expr_sql = str(expr_node)

    # Force a naïve TIMESTAMP so DuckDB doesn't reach for pytz.
    try:
        duckdb_expr_list = sqlglot.transpile(
            f"SELECT CAST(({expr_sql}) AS TIMESTAMP)",
            read="bigquery",
            write="duckdb",
        )
    except Exception as exc:
        raise InvalidQueryError(
            f"Could not translate FOR SYSTEM_TIME AS OF expression: {exc}",
        ) from exc
    duckdb_expr = duckdb_expr_list[0] if duckdb_expr_list else ""
    if not duckdb_expr:
        raise InvalidQueryError("Empty FOR SYSTEM_TIME AS OF expression")

    try:
        result = engine.execute(duckdb_expr).fetchone()
    except Exception as exc:
        raise InvalidQueryError(
            f"Could not evaluate FOR SYSTEM_TIME AS OF expression: {exc}",
        ) from exc

    if result is None or result[0] is None:
        raise InvalidQueryError("FOR SYSTEM_TIME AS OF evaluated to NULL")

    value = result[0]
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value
    raise InvalidQueryError(
        f"FOR SYSTEM_TIME AS OF must be a TIMESTAMP, got {type(value).__name__}",
    )


def _extract_literal_timestamp(node: exp.Expression | str) -> str | None:
    """Pull the inner literal text from a TIMESTAMP-flavoured expression.

    Handles:
      * ``Cast(Literal(...), TIMESTAMP[TZ])`` — what SQLGlot produces
        for ``TIMESTAMP 'YYYY-...'``.
      * Bare ``Literal(...)`` strings — what SQLGlot keeps for
        ``'YYYY-...'`` without an explicit cast.
    """
    if isinstance(node, str):
        return node
    if isinstance(node, exp.Cast):
        inner = node.this
        if isinstance(inner, exp.Literal) and inner.is_string:
            return str(inner.this)
        return None
    if isinstance(node, exp.Literal) and node.is_string:
        return str(node.this)
    return None


_SNAPSHOTS_SCHEMA = SNAPSHOTS_SCHEMA  # re-exported for tests


__all__ = ["rewrite_for_system_time"]
