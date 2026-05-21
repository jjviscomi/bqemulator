"""Translation rules for BigQuery RANGE<T> functions.

BigQuery's RANGE type is modeled in DuckDB as ``STRUCT<start T, "end" T>``
— DuckDB has no native RANGE. The rules in this module translate the
BigQuery ``RANGE_*`` family into struct-field-aware DuckDB
expressions.

Every range function is parsed by SQLGlot as an ``Anonymous`` node;
the rules dispatch on the upper-cased function name.

Functions covered (locked in
:doc:`/roadmap/phase-9-specialized-types`):

* ``RANGE(start, end)`` — constructor → ``STRUCT_PACK("start" := …, "end" := …)``.
* ``RANGE_CONTAINS(r, value)`` → ``(r."start" <= value AND value < r."end")``.
* ``RANGE_OVERLAPS(r1, r2)`` → ``(r1."start" < r2."end" AND r2."start" < r1."end")``.
* ``RANGE_INTERSECT(r1, r2)`` →
  ``CASE WHEN overlaps(r1,r2) THEN STRUCT_PACK("start" :=
  GREATEST(r1."start", r2."start"), "end" := LEAST(r1."end",
  r2."end")) END``.
* ``RANGE_START(r)`` → ``r."start"`` (the lower bound of the range).
* ``RANGE_END(r)`` → ``r."end"`` (the exclusive upper bound).
* ``GENERATE_RANGE_ARRAY(r, step)`` →
  ``LIST_TRANSFORM(GENERATE_SERIES(r."start", r."end" - step, step),
  x -> STRUCT_PACK("start" := x, "end" := x + step))``.

``RANGE_SESSIONIZE`` (a TVF) is rewritten at the *pre-translator*
stage by :mod:`bqemulator.sql.rewriter.range_sessionize` — the
``TABLE <ref>`` keyword in TVF arguments isn't accepted by SQLGlot's
BigQuery parser, so the rewrite has to happen at the source-text
level before SQLGlot ever sees the SQL. The pre-translator emits a
``RANGE(MIN, MAX)`` constructor that the
:mod:`bqemulator.sql.rewriter.specialized_types` pass picks up
(running immediately after) and converts to the canonical STRUCT
shape. No post-translate rule for ``RANGE_SESSIONIZE`` is required.
"""

from __future__ import annotations

from sqlglot import exp

from bqemulator.sql.rules import register
from bqemulator.sql.rules._base import TranslationRule
from bqemulator.types.range_type import END_FIELD, START_FIELD


def _struct_pack(start_value: exp.Expression, end_value: exp.Expression) -> exp.Expression:
    """Build a DuckDB ``STRUCT_PACK("start" := …, "end" := …)`` expression."""
    return exp.Anonymous(
        this="STRUCT_PACK",
        expressions=[
            exp.PropertyEQ(
                this=exp.Identifier(this=START_FIELD, quoted=True),
                expression=start_value,
            ),
            exp.PropertyEQ(
                this=exp.Identifier(this=END_FIELD, quoted=True),
                expression=end_value,
            ),
        ],
    )


def _struct_field(operand: exp.Expression, field: str) -> exp.Expression:
    """Build a DuckDB ``operand."field"`` access."""
    return exp.Dot(
        this=operand.copy(),
        expression=exp.Identifier(this=field, quoted=True),
    )


@register
class RangeConstructorRule(TranslationRule):
    """``RANGE(start, end)`` → STRUCT_PACK constructor.

    The pre-translator rewriter in
    :mod:`bqemulator.sql.rewriter.specialized_types` handles the
    BigQuery-side rewrite to a STRUCT literal, which SQLGlot
    transpiles to DuckDB's ``{...}`` struct form cleanly. This
    post-translator rule only catches an exceptional case: a stray
    ``Anonymous(this="RANGE")`` node — for example, if a future
    SQLGlot release parses ``RANGE(a, b)`` differently in DuckDB
    dialect, or if a downstream rewrite re-introduces the call.
    """

    name = "RANGE"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match a 2-argument ``Anonymous(this='RANGE')`` only."""
        return (
            isinstance(node, exp.Anonymous)
            and str(node.this).upper() == "RANGE"
            and len(node.expressions) == 2  # noqa: PLR2004
        )

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Emit ``STRUCT_PACK("start" := s, "end" := e)``."""
        anon = node
        return _struct_pack(anon.expressions[0].copy(), anon.expressions[1].copy())


@register
class RangeContainsRule(TranslationRule):
    """``RANGE_CONTAINS(r, value)`` → ``(r."start" <= value AND value < r."end")``."""

    name = "RANGE_CONTAINS"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match the ``RANGE_CONTAINS`` anonymous call."""
        return (
            isinstance(node, exp.Anonymous)
            and str(node.this).upper() == "RANGE_CONTAINS"
            and len(node.expressions) == 2  # noqa: PLR2004
        )

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Expand to a half-open interval predicate ``[start, end)``."""
        anon = node
        rng, value = anon.expressions[0], anon.expressions[1]
        lower = exp.LTE(this=_struct_field(rng, START_FIELD), expression=value.copy())
        upper = exp.LT(this=value.copy(), expression=_struct_field(rng, END_FIELD))
        return exp.Paren(this=exp.And(this=lower, expression=upper))


@register
class RangeOverlapsRule(TranslationRule):
    """``RANGE_OVERLAPS(r1, r2)`` → ``r1."start" < r2."end" AND r2."start" < r1."end"``."""

    name = "RANGE_OVERLAPS"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match the ``RANGE_OVERLAPS`` anonymous call."""
        return (
            isinstance(node, exp.Anonymous)
            and str(node.this).upper() == "RANGE_OVERLAPS"
            and len(node.expressions) == 2  # noqa: PLR2004
        )

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Expand to the standard half-open overlap predicate."""
        anon = node
        r1, r2 = anon.expressions[0], anon.expressions[1]
        left = exp.LT(
            this=_struct_field(r1, START_FIELD),
            expression=_struct_field(r2, END_FIELD),
        )
        right = exp.LT(
            this=_struct_field(r2, START_FIELD),
            expression=_struct_field(r1, END_FIELD),
        )
        return exp.Paren(this=exp.And(this=left, expression=right))


@register
class RangeIntersectRule(TranslationRule):
    """``RANGE_INTERSECT(r1, r2)`` → ``CASE WHEN overlaps THEN STRUCT_PACK(...) END``."""

    name = "RANGE_INTERSECT"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match the ``RANGE_INTERSECT`` anonymous call."""
        return (
            isinstance(node, exp.Anonymous)
            and str(node.this).upper() == "RANGE_INTERSECT"
            and len(node.expressions) == 2  # noqa: PLR2004
        )

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Expand to a CASE producing the intersected range or NULL."""
        anon = node
        r1, r2 = anon.expressions[0], anon.expressions[1]
        # Overlap predicate.
        overlap_left = exp.LT(
            this=_struct_field(r1, START_FIELD),
            expression=_struct_field(r2, END_FIELD),
        )
        overlap_right = exp.LT(
            this=_struct_field(r2, START_FIELD),
            expression=_struct_field(r1, END_FIELD),
        )
        overlap_pred = exp.And(this=overlap_left, expression=overlap_right)

        intersected = _struct_pack(
            exp.Anonymous(
                this="GREATEST",
                expressions=[
                    _struct_field(r1, START_FIELD),
                    _struct_field(r2, START_FIELD),
                ],
            ),
            exp.Anonymous(
                this="LEAST",
                expressions=[
                    _struct_field(r1, END_FIELD),
                    _struct_field(r2, END_FIELD),
                ],
            ),
        )
        return exp.Case(
            ifs=[exp.If(this=overlap_pred, true=intersected)],
            default=exp.Null(),
        )


@register
class RangeStartRule(TranslationRule):
    """``RANGE_START(r)`` → ``r."start"``.

    BigQuery's accessor returns the lower bound of the range. The
    STRUCT layout the pre-translator + constructor rule emit carries
    the lower bound under the ``"start"`` field name.
    """

    name = "RANGE_START"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match a 1-arg ``Anonymous(RANGE_START)`` call."""
        return (
            isinstance(node, exp.Anonymous)
            and str(node.this).upper() == "RANGE_START"
            and len(node.expressions) == 1
        )

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Emit ``r."start"``."""
        return _struct_field(node.expressions[0], START_FIELD)


@register
class RangeEndRule(TranslationRule):
    """``RANGE_END(r)`` → ``r."end"``.

    Mirrors :class:`RangeStartRule` for the exclusive upper bound.
    """

    name = "RANGE_END"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match a 1-arg ``Anonymous(RANGE_END)`` call."""
        return (
            isinstance(node, exp.Anonymous)
            and str(node.this).upper() == "RANGE_END"
            and len(node.expressions) == 1
        )

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Emit ``r."end"``."""
        return _struct_field(node.expressions[0], END_FIELD)


@register
class GenerateRangeArrayRule(TranslationRule):
    """``GENERATE_RANGE_ARRAY(r, step)`` → list of consecutive sub-ranges.

    The expansion uses DuckDB's ``range(start, end, step)`` to produce
    successive endpoints, ``LIST_TRANSFORM`` to wrap each pair in
    ``STRUCT_PACK("start" := x, "end" := LEAST(x + step, r."end"))``,
    and per-element ``CAST`` back to the original element type so
    DuckDB's promotion-to-``TIMESTAMP`` (for DATE inputs) is undone.

    ADR 0023 §1.G: BigQuery clips the trailing sub-range to the outer
    range's end (``[2024-01-07, 2024-01-08)`` for a 2-day step over a
    week-long span — *not* ``[2024-01-07, 2024-01-09)``) and preserves
    the element type. DuckDB's ``range(DATE, DATE, INTERVAL)`` returns a
    list of TIMESTAMPs and ``DATE + INTERVAL n DAY`` likewise widens to
    TIMESTAMP, so the lambda re-casts each endpoint to the original
    element type when we can recover it from the literal-RANGE Struct
    AST.
    """

    name = "GENERATE_RANGE_ARRAY"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match the ``GENERATE_RANGE_ARRAY`` anonymous call."""
        return (
            isinstance(node, exp.Anonymous)
            and str(node.this).upper() == "GENERATE_RANGE_ARRAY"
            and len(node.expressions) == 2  # noqa: PLR2004
        )

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Build the list-transform expansion."""
        anon = node
        rng, step = anon.expressions[0], anon.expressions[1]
        element_dtype = _detect_range_struct_element(rng)
        # endpoints = range(r.start, r.end, step)
        endpoints = exp.Anonymous(
            this="range",
            expressions=[
                _struct_field(rng, START_FIELD),
                _struct_field(rng, END_FIELD),
                step.copy(),
            ],
        )
        x = exp.Identifier(this="x", quoted=False)
        x_col = exp.Column(this=x.copy())
        next_endpoint = exp.Add(this=x_col.copy(), expression=step.copy())
        clipped_end = exp.Anonymous(
            this="LEAST",
            expressions=[next_endpoint, _struct_field(rng, END_FIELD)],
        )
        if element_dtype is not None:
            start_value: exp.Expression = exp.Cast(this=x_col.copy(), to=element_dtype.copy())
            end_value: exp.Expression = exp.Cast(this=clipped_end, to=element_dtype.copy())
        else:
            start_value = x_col.copy()
            end_value = clipped_end
        transform = exp.Lambda(
            this=_struct_pack(start_value, end_value),
            expressions=[exp.Column(this=x.copy())],
        )
        return exp.Anonymous(
            this="LIST_TRANSFORM",
            expressions=[endpoints, transform],
        )


def _detect_range_struct_element(node: exp.Expression) -> exp.DataType | None:
    """Pull the inner CAST target type from a literal-RANGE Struct argument.

    The pre-translator rewrites ``RANGE<T> '[start, end)'`` to
    ``STRUCT(CAST(<start> AS T) AS start, CAST(<end> AS T) AS end)``;
    SQLGlot's DuckDB transpile then emits ``{'start': CAST(...), 'end':
    CAST(...)}`` which parses back as a :class:`exp.Struct` whose
    children are :class:`exp.PropertyEQ` (``"start" := CAST(...)``).
    Returning the inner ``DataType`` lets the rule wrap the lambda
    outputs in matching casts so the BigQuery element type round-trips.
    Returns ``None`` for non-literal ranges (column references / unknown
    shapes) — the caller falls back to the un-cast lambda whose output
    follows DuckDB's natural type promotion.
    """
    if not isinstance(node, exp.Struct):
        return None
    if not node.expressions:
        return None
    for child in node.expressions:
        cast_node: exp.Expression | None = None
        if isinstance(child, exp.PropertyEQ):
            cast_node = child.expression
        elif isinstance(child, exp.Alias):
            cast_node = child.this
        if isinstance(cast_node, exp.Cast) and isinstance(cast_node.to, exp.DataType):
            return cast_node.to
    return None


__all__ = [
    "GenerateRangeArrayRule",
    "RangeConstructorRule",
    "RangeContainsRule",
    "RangeEndRule",
    "RangeIntersectRule",
    "RangeOverlapsRule",
    "RangeStartRule",
]
