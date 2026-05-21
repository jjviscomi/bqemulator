"""Pre-translator rewriter for BigQuery specialized-type syntax.

Three BigQuery forms must be rewritten *before* SQLGlot transpiles the
SQL to DuckDB, because the SQLGlot/DuckDB pipeline cannot handle them
correctly downstream:

1. **Compound interval literals**
   (``INTERVAL '1-2 3 4:5:6.789' YEAR TO SECOND``) — DuckDB's parser
   refuses the ``YEAR TO SECOND`` form entirely. We parse the literal
   via :func:`bqemulator.types.interval.parse_interval_literal` and
   replace it with a parenthesised sum of single-unit intervals
   (``INTERVAL '1' YEAR + INTERVAL '2' MONTH + …``).

2. **``RANGE(a, b)`` constructor** — SQLGlot's DuckDB parser folds it
   into the same :class:`exp.GenerateSeries` node as
   BigQuery's ``GENERATE_ARRAY(a, b)``, which makes the two
   indistinguishable in the post-translator rule pass. Doing the
   rewrite here — while the BigQuery AST still carries the
   ``Anonymous(this="RANGE")`` shape — preserves the distinction.
   We emit ``STRUCT(a AS start, b AS end)`` (with the field names
   quoted as identifiers) which SQLGlot transpiles to
   ``{'start': a, 'end': b}`` in DuckDB.

3. **``RANGE<T> '[start, end)'`` typed literal** (ADR 0023 §1.G) —
   SQLGlot parses this as ``Cast(literal, RANGE(T))``, but DuckDB
   rejects ``CAST(... AS RANGE(T))`` because RANGE is not a DuckDB
   type. We rewrite to ``STRUCT(CAST(<start> AS T) AS start, CAST(<end>
   AS T) AS end)`` so DuckDB sees an ordinary struct literal.
   ``UNBOUNDED`` endpoints become ``CAST(NULL AS T)`` so the struct
   type signature stays uniform across rows. The element-type mapping
   matches :func:`bqemulator.types.range_type._bq_to_duckdb_element`
   (BQ DATETIME → DuckDB TIMESTAMP, BQ TIMESTAMP → DuckDB TIMESTAMPTZ).

The function short-circuits when the SQL contains nothing that needs
rewriting (the common case for queries that don't touch INTERVAL or
RANGE syntax).

Other Phase 9 transforms (``ST_*`` renames, ``RANGE_*`` expansions,
``JUSTIFY_*``) all happen in the *post-translator* rule pass because
their syntax parses cleanly under DuckDB's grammar — only the names
and call shapes need rewriting.
"""

from __future__ import annotations

import re
from typing import Any

import sqlglot
from sqlglot import exp

from bqemulator.types.interval import parse_interval_literal, parts_to_duckdb_expr
from bqemulator.types.range_type import END_FIELD, START_FIELD


def rewrite_specialized_types(bq_sql: str) -> str:
    """Pre-translate BigQuery SQL for specialized-type literal forms.

    Rewrites compound interval literals, the ``RANGE(a, b)``
    constructor, ``RANGE<T> '[start, end)'`` typed literals, and
    ``RANGE<T>`` column-type / cast-type references that survive the
    literal pass (e.g. ``CREATE TABLE t (col RANGE<DATE>)``,
    ``CAST(x AS RANGE<DATE>)``). Returns the input unchanged when no
    rewrite is needed (the common case).

    Failures during parse/rewrite are tolerated by returning the input
    SQL — the downstream SQLGlot transpile will report the parse error
    in its own clean format.
    """
    upper = bq_sql.upper()
    needs_interval = "INTERVAL" in upper and "TO" in upper
    needs_range_ctor = "RANGE(" in upper.replace(" ", "")
    needs_range_literal = "RANGE<" in upper.replace(" ", "")
    if not (needs_interval or needs_range_ctor or needs_range_literal):
        return bq_sql

    try:
        parsed = sqlglot.parse_one(bq_sql, read="bigquery")
    except sqlglot.errors.ParseError:
        return bq_sql

    modified_interval = _rewrite_intervals(parsed) if needs_interval else False
    modified_range_ctor = _rewrite_range_constructor(parsed) if needs_range_ctor else False
    modified_range_literal = _rewrite_range_literals(parsed) if needs_range_literal else False
    # The column-type pass runs after the literal pass so any
    # ``Cast(literal, RANGE<T>)`` shape has already been replaced with
    # a ``STRUCT(...)`` expression (the literal handler replaces the
    # entire Cast node, not just the type). Whatever ``DataType.RANGE``
    # nodes remain belong to column definitions or non-literal CASTs.
    modified_range_type = _rewrite_range_data_types(parsed) if needs_range_literal else False
    if not (
        modified_interval or modified_range_ctor or modified_range_literal or modified_range_type
    ):
        return bq_sql
    return parsed.sql(dialect="bigquery")


#: BigQuery RANGE element types → SQLGlot ``DataType.Type`` used in the
#: CAST emitted by the rewrite. The mapping mirrors SQLGlot's own
#: BigQuery → DuckDB transpile (DATETIME → ``TIMESTAMP`` naive,
#: TIMESTAMP → ``TIMESTAMPTZ``) so the post-transpile DuckDB SQL types
#: each endpoint correctly — and the resulting STRUCT column's
#: ``bqemu.duckdb_type`` metadata matches
#: :func:`bqemulator.types.range_type._bq_to_duckdb_element`.
#:
#: ``exp.DataType.Type`` is a runtime enum that mypy can't type-check
#: directly, so the dict values are annotated as :class:`Any`. The
#: keys/values are exercised in unit tests so the silent-cast is safe.
_RANGE_BQ_ELEM_TO_SQLGLOT_DTYPE: dict[str, Any] = {
    "DATE": exp.DataType.Type.DATE,
    "DATETIME": exp.DataType.Type.DATETIME,
    "TIMESTAMP": exp.DataType.Type.TIMESTAMPTZ,
}

#: SQLGlot ``DataType.Type`` (as parsed from ``RANGE<T>``) → BigQuery
#: RANGE element name. SQLGlot's BigQuery parser folds element-type
#: names asymmetrically: BQ ``DATETIME`` parses as ``DType.TIMESTAMP``
#: (naive) and BQ ``TIMESTAMP`` parses as ``DType.TIMESTAMPTZ`` (TZ-
#: aware). The mapping reverses that fold so we emit the right
#: BigQuery type in the CAST replacement.
_RANGE_DTYPE_TO_BQ_ELEM: dict[Any, str] = {
    exp.DataType.Type.DATE: "DATE",
    exp.DataType.Type.DATETIME: "DATETIME",
    exp.DataType.Type.TIMESTAMP: "DATETIME",
    exp.DataType.Type.TIMESTAMPTZ: "TIMESTAMP",
    exp.DataType.Type.TIMESTAMPLTZ: "TIMESTAMP",
}

#: Matches the BigQuery RANGE literal body ``[<start>, <end>)``.
#: Both endpoints may be either a typed value (``2024-01-01``,
#: ``2024-01-01 00:00:00``, etc.) or the literal token ``UNBOUNDED``.
_RANGE_BODY_RE = re.compile(r"^\[\s*(?P<start>[^,]+?)\s*,\s*(?P<end>[^,]+?)\s*\)$")


def _rewrite_range_literals(tree: exp.Expression) -> bool:
    """Rewrite ``CAST('[start, end)' AS RANGE<T>)`` literals to STRUCT form.

    SQLGlot parses BigQuery's ``RANGE<T> '[start, end)'`` typed literal
    as ``Cast(Literal, DataType.RANGE)``. DuckDB rejects that CAST
    (RANGE is not a DuckDB type), so we replace each occurrence with a
    BigQuery ``STRUCT(CAST(<start> AS T) AS start, CAST(<end> AS T) AS
    end)`` expression. ``UNBOUNDED`` endpoints become
    ``CAST(NULL AS T)`` so the struct's column type stays uniform
    across rows (DuckDB's struct typing demands a single concrete type
    per field — without the NULL cast the field type would default to
    ``BIGINT``).

    Returns ``True`` when at least one literal was rewritten.
    """
    modified = False
    for node in list(tree.walk()):
        if not isinstance(node, exp.Cast):
            continue
        to_type = node.to
        if to_type.this != exp.DataType.Type.RANGE:
            continue
        elem_dtype = _range_element_dtype(to_type)
        if elem_dtype is None:
            continue
        bq_elem = _RANGE_DTYPE_TO_BQ_ELEM.get(elem_dtype)
        if bq_elem is None:
            continue
        literal = node.this
        if not isinstance(literal, exp.Literal):
            continue
        bounds = _parse_range_body(str(literal.this))
        if bounds is None:
            continue
        start_text, end_text = bounds
        target_dtype = _RANGE_BQ_ELEM_TO_SQLGLOT_DTYPE[bq_elem]
        start_expr = _range_endpoint_expr(start_text, target_dtype)
        end_expr = _range_endpoint_expr(end_text, target_dtype)
        replacement = exp.Struct(
            expressions=[
                exp.Alias(
                    this=start_expr,
                    alias=exp.Identifier(this=START_FIELD, quoted=True),
                ),
                exp.Alias(
                    this=end_expr,
                    alias=exp.Identifier(this=END_FIELD, quoted=True),
                ),
            ],
        )
        node.replace(replacement)
        modified = True
    return modified


def _range_element_dtype(range_type: exp.DataType) -> Any:
    """Pull the inner element ``DataType.Type`` from a ``RANGE<T>`` data type.

    Returns ``None`` for shapes the rewriter doesn't recognise — leaving
    the AST untouched lets the downstream SQLGlot transpile surface a
    clean parse error rather than us masking a malformed query. The
    return type is :class:`Any` because ``exp.DataType.Type`` is a
    runtime enum that mypy can't introspect directly.
    """
    if not range_type.expressions:
        return None
    inner = range_type.expressions[0]
    if not isinstance(inner, exp.DataType):
        return None
    return inner.this


def _parse_range_body(text: str) -> tuple[str | None, str | None] | None:
    """Parse a ``[start, end)`` range-literal body.

    Returns ``(start, end)`` where ``UNBOUNDED`` endpoints map to
    ``None``. Returns ``None`` when the body shape is not recognised.
    """
    match = _RANGE_BODY_RE.match(text.strip())
    if match is None:
        return None
    start = match.group("start").strip()
    end = match.group("end").strip()
    return (
        None if start.upper() == "UNBOUNDED" else start,
        None if end.upper() == "UNBOUNDED" else end,
    )


def _range_endpoint_expr(
    text: str | None,
    dtype: Any,
) -> exp.Expression:
    """Build a typed endpoint expression for a RANGE literal.

    ``None`` (the parsed form of ``UNBOUNDED``) becomes ``CAST(NULL AS
    T)``; a bare value becomes ``CAST('<text>' AS T)``. We always emit
    the CAST so the resulting STRUCT's field type is uniform — without
    it DuckDB infers a different type per row's NULL handling. The
    *dtype* parameter is :class:`Any` because ``exp.DataType.Type`` is
    a runtime enum that mypy can't accept as an annotation directly.
    """
    if text is None:
        return exp.cast(exp.Null(), dtype)
    return exp.cast(exp.Literal.string(text), dtype)


def _rewrite_range_data_types(tree: exp.Expression) -> bool:
    """Rewrite ``RANGE<T>`` column / cast types to ``STRUCT<start T, end T>``.

    SQLGlot transpiles BigQuery ``RANGE<DATE>`` to DuckDB ``RANGE(DATE)``,
    which DuckDB rejects (RANGE is not a DuckDB type). This pass runs
    after :func:`_rewrite_range_literals` so any ``Cast(literal,
    RANGE<T>)`` has already been replaced wholesale with a
    ``STRUCT(...)`` expression — the literal-cast pattern is gone.
    What remains is ``DataType.RANGE`` in column-definition slots
    (``CREATE TABLE t (col RANGE<DATE>)``) and the type slot of a
    non-literal ``CAST(<expr> AS RANGE<DATE>)``.

    The new DataType is built via ``DataType.build`` so SQLGlot's BigQuery
    serializer emits a STRUCT with the canonical field names, and the
    subsequent DuckDB transpile lands the column type on the exact
    ``STRUCT("start" T, "end" T)`` shape
    :func:`bqemulator.types.range_type.detect_range_element` matches.
    """
    modified = False
    for node in list(tree.walk()):
        if not isinstance(node, exp.DataType):
            continue
        if node.this != exp.DataType.Type.RANGE:
            continue
        element_dtype = _range_element_dtype(node)
        if element_dtype is None:
            continue
        bq_elem = _RANGE_DTYPE_TO_BQ_ELEM.get(element_dtype)
        if bq_elem is None:
            continue
        replacement = exp.DataType.build(
            f"STRUCT<`{START_FIELD}` {bq_elem}, `{END_FIELD}` {bq_elem}>",
            dialect="bigquery",
        )
        node.replace(replacement)
        modified = True
    return modified


def _rewrite_range_constructor(tree: exp.Expression) -> bool:
    """Rewrite ``RANGE(a, b)`` anonymous calls into ``STRUCT(... AS …, ...)``.

    BigQuery's grammar does not have a 2-argument ``RANGE`` other than
    the RANGE-type constructor. Rewriting it here (before SQLGlot's
    DuckDB-side parse folds it into ``GenerateSeries``) lets the
    downstream pipeline keep ``RANGE(a, b)`` distinguishable from
    ``GENERATE_ARRAY(a, b)`` (also a 2-arg ``GenerateSeries`` after
    transpile).
    """
    modified = False
    for node in list(tree.walk()):
        if not isinstance(node, exp.Anonymous):
            continue
        if str(node.this).upper() != "RANGE":
            continue
        if len(node.expressions) != 2:  # noqa: PLR2004
            continue
        start_expr = node.expressions[0].copy()
        end_expr = node.expressions[1].copy()
        replacement = exp.Struct(
            expressions=[
                exp.Alias(
                    this=start_expr,
                    alias=exp.Identifier(this=START_FIELD, quoted=True),
                ),
                exp.Alias(
                    this=end_expr,
                    alias=exp.Identifier(this=END_FIELD, quoted=True),
                ),
            ],
        )
        node.replace(replacement)
        modified = True
    return modified


def _rewrite_intervals(tree: exp.Expression) -> bool:
    """Walk *tree* and replace every compound ``Interval`` node in place.

    Returns ``True`` when at least one node was replaced.
    """
    modified = False
    for node in list(tree.walk()):
        if not isinstance(node, exp.Interval):
            continue
        unit = node.args.get("unit")
        if not isinstance(unit, exp.IntervalSpan):
            continue

        literal = node.this
        if not isinstance(literal, exp.Literal):
            continue
        span_text = _interval_span_text(unit)
        if span_text is None:
            continue

        try:
            parts = parse_interval_literal(literal.this, span_text)
        except Exception:  # noqa: BLE001, S112 — defensive: parse failure → leave alone.
            continue

        # Re-emit as an additive expression that DuckDB parses cleanly.
        expanded_sql = parts_to_duckdb_expr(parts)
        try:
            replacement = sqlglot.parse_one(expanded_sql, read="duckdb")
        except sqlglot.errors.ParseError:  # pragma: no cover — defensive.
            continue
        # ``parse_one`` on an expression returns a top-level expression
        # we can drop into the same slot as ``node``. Strip outer parens
        # so the printed form re-parens cleanly inside the host SQL.
        node.replace(replacement)
        modified = True
    return modified


def _interval_span_text(span: exp.IntervalSpan) -> str | None:
    """Render an :class:`exp.IntervalSpan` back to ``"YEAR TO SECOND"`` style text."""
    start = span.this
    end = span.expression
    if start is None or end is None:
        return None
    start_name = _var_name(start)
    end_name = _var_name(end)
    if start_name is None or end_name is None:
        return None
    return f"{start_name} TO {end_name}"


def _var_name(node: exp.Expression) -> str | None:
    """Extract the unit name from a ``Var`` / ``Literal`` / ``Identifier`` node."""
    if isinstance(node, exp.Var):
        return str(node.this).upper()
    if isinstance(node, (exp.Identifier, exp.Literal)):
        return str(node.this).upper()
    return None


__all__ = ["rewrite_specialized_types"]
