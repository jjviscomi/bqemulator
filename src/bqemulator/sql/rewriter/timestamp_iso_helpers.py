"""Pre-translator rewriter for BigQuery ``FORMAT_TIMESTAMP`` / ``PARSE_TIMESTAMP``.

SQLGlot's BigQuery → DuckDB transpile drops the optional ``zone``
argument from ``FORMAT_TIMESTAMP(fmt, ts, zone)`` and lowers
``PARSE_TIMESTAMP(fmt, value)`` to a bare ``STRPTIME(value, fmt)``.
DuckDB's own ``STRFTIME`` / ``STRPTIME`` reject the BigQuery-only
``%Ez`` extension specifier (ISO offset with colon, ``+HH:MM``) and
silently accept ``%Z`` named-zone abbreviations such as ``IST`` that
real BigQuery rejects with ``Invalid time zone: <zone>``.

This pre-translator routes the affected calls through Python-backed
helpers (:func:`bqemulator.sql.builtin_udfs.bqemu_format_timestamp_iso`
and :func:`bqemulator.sql.builtin_udfs.bqemu_parse_timestamp_iso`)
while the BigQuery AST still carries the zone argument. The helpers
handle ``%Ez`` natively, preserve the zone conversion, and validate
``%Z`` named zones against ``zoneinfo.ZoneInfo`` (strict IANA
semantics).

The rewriter short-circuits when no ``FORMAT_TIMESTAMP`` /
``PARSE_TIMESTAMP`` reference appears in the input.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp


def _format_has_ez_or_z(fmt_node: exp.Expression | None) -> bool:
    """Return True when ``fmt_node`` is a literal containing ``%Ez`` or ``%Z``."""
    if not isinstance(fmt_node, exp.Literal) or not fmt_node.is_string:
        return False
    text = str(fmt_node.this)
    return "%Ez" in text or "%Z" in text


def rewrite_timestamp_iso_helpers(bq_sql: str) -> str:
    """Pre-translate ``FORMAT_TIMESTAMP`` / ``PARSE_TIMESTAMP`` to the helper UDFs.

    The rewrite fires when:

    * ``FORMAT_TIMESTAMP(fmt, ts [, zone])`` carries a ``zone`` argument
      (BigQuery's optional 3rd arg, which SQLGlot drops on translate),
      OR the format string contains a ``%E`` specifier (DuckDB STRFTIME
      cannot parse the ``%E#`` extension family).
    * ``PARSE_TIMESTAMP(fmt, value)`` carries ``%Ez`` (DuckDB STRPTIME
      cannot parse it) or ``%Z`` (DuckDB silently accepts ambiguous
      zone abbreviations real BigQuery rejects).

    Returns the input unchanged when neither function is referenced.
    """
    upper = bq_sql.upper()
    if "FORMAT_TIMESTAMP" not in upper and "PARSE_TIMESTAMP" not in upper:
        return bq_sql
    try:
        parsed = sqlglot.parse_one(bq_sql, read="bigquery")
    except sqlglot.errors.ParseError:
        return bq_sql

    modified = False
    for node in list(parsed.walk()):
        replacement: exp.Expression | None = None
        if isinstance(node, exp.TimeToStr):
            # SQLGlot maps both FORMAT_TIMESTAMP and FORMAT_DATETIME to
            # ``TimeToStr``. We only intervene when a zone arg is set
            # (FORMAT_TIMESTAMP with explicit zone) or the format
            # carries a ``%E`` specifier (any FORMAT_* call).
            fmt = node.args.get("format")
            zone = node.args.get("zone")
            needs_helper = zone is not None or (
                isinstance(fmt, exp.Literal) and fmt.is_string and "%E" in str(fmt.this)
            )
            if needs_helper and fmt is not None:
                ts = node.this
                if ts is None:
                    continue
                zone_arg: exp.Expression = (
                    zone.copy() if zone is not None else exp.Literal.string("UTC")
                )
                replacement = exp.Anonymous(
                    this="bqemu_format_timestamp_iso",
                    expressions=[fmt.copy(), ts.copy(), zone_arg],
                )
        elif isinstance(node, exp.StrToTime):
            # PARSE_TIMESTAMP / PARSE_DATETIME — both arrive as
            # ``StrToTime``. We only intervene when the format carries
            # a BigQuery-only ``%Ez`` or a ``%Z`` named-zone token.
            fmt = node.args.get("format")
            if not _format_has_ez_or_z(fmt) or fmt is None:
                continue
            value = node.this
            if value is None:
                continue
            helper_call = exp.Anonymous(
                this="bqemu_parse_timestamp_iso",
                expressions=[fmt.copy(), value.copy()],
            )
            # Wrap in ``timezone('UTC', …)`` so the wire-format
            # renderer surfaces the result as ``TIMESTAMP`` (matching
            # :class:`ParseTimestampUtcRule`'s contract).
            replacement = exp.Anonymous(
                this="timezone",
                expressions=[exp.Literal.string("UTC"), helper_call],
            )
        if replacement is not None:
            node.replace(replacement)
            modified = True

    if not modified:
        return bq_sql
    return parsed.sql(dialect="bigquery")


__all__ = ["rewrite_timestamp_iso_helpers"]
