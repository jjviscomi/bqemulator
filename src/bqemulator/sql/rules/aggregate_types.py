"""Translation rules that preserve BigQuery aggregate output types.

DuckDB's aggregate functions return their "natural" promoted type:
``AVG(DECIMAL)`` and ``AVG(BIGINT)`` both surface as ``DOUBLE`` even
though BigQuery preserves the operand's type for ``AVG`` over numeric
operands (``AVG(NUMERIC)`` → ``NUMERIC``, ``AVG(BIGNUMERIC)`` →
``BIGNUMERIC``; ``AVG(INT64)`` and ``AVG(FLOAT64)`` stay
``FLOAT64`` — matching DuckDB).

This module hosts the rules that detect operand-type-sensitive
aggregate shapes and rewrite them so the DuckDB output preserves the
BigQuery type tag. The rules consult ``node.type`` populated by
SQLGlot's ``annotate_types`` pass (the translator runs it when the
caller supplies a catalog-derived schema dict); when the operand's
type cannot be resolved, the rule skips — matching the legacy
emulator behaviour for queries the catalog cannot annotate.

A second concern handled here is *function-name parity* — BigQuery
aggregates that have a DuckDB primitive but under a different name and
which SQLGlot does not auto-translate. :class:`ArrayConcatAggRule`
rewrites BigQuery's ``ARRAY_CONCAT_AGG(arr [ORDER BY …])`` to DuckDB's
``flatten(array_agg(arr [ORDER BY …]))`` because DuckDB has no
``array_concat_agg`` and SQLGlot emits the BigQuery name verbatim.
``MAX_BY`` / ``MIN_BY`` are *not* rewritten here — SQLGlot already
transpiles them to DuckDB's ``arg_max`` / ``arg_min`` natively.
``STDDEV`` / ``VARIANCE`` / ``GROUPING`` reach DuckDB unchanged and
the sample-vs-population default matches BigQuery's contract (both
default to the *sample* form).

A third concern is *approximate-aggregate semantic parity* via the
``COUNT(DISTINCT x)`` rewrite — the same precedent set by
:class:`ApproxCountDistinctExactRule` in
``datetime_semantics.py``. :class:`HllCountExtractInitRule` and
:class:`HllCountMergeRule` translate the two common BigQuery HLL
patterns — ``HLL_COUNT.EXTRACT(HLL_COUNT.INIT(x))`` and
``HLL_COUNT.MERGE(sketch)`` over a subquery union of
``HLL_COUNT.INIT(x)`` legs — to ``COUNT(DISTINCT x)``. DuckDB has no
HLL sketch primitive, and re-implementing BigQuery's HLL++ binary
format is its own workstream (see ADR 0024). The cardinality
user-facing semantic is preserved; the sketch-as-persistable-BYTES
semantic is not (the two sketch-shaped surfaces — ``HLL_COUNT.INIT``
and ``HLL_COUNT.MERGE_PARTIAL`` — are pinned as XFAIL via
``docs/reference/out-of-scope.md#hll-sketch-binary-format-hll_countinit--merge_partial``).
"""

from __future__ import annotations

from sqlglot import exp

from bqemulator.sql.rules import register
from bqemulator.sql.rules._base import TranslationRule

_NUMERIC_TYPE = "DECIMAL(38, 9)"


@register
class AvgDecimalRule(TranslationRule):
    """``AVG(decimal_col)`` → ``CAST(AVG(decimal_col) AS DECIMAL(38, 9))``.

    BigQuery's contract: ``AVG(NUMERIC) → NUMERIC``. DuckDB's: always
    ``DOUBLE``. We wrap the AVG call in a cast back to ``DECIMAL(38, 9)``
    so the REST schema renderer surfaces NUMERIC and downstream
    expressions (``ROUND(AVG(numeric), n)``) preserve the DECIMAL
    output type.

    Windowed forms — ``AVG(decimal_col) OVER (…)`` — require the cast
    to wrap the *entire* windowed expression, not the inner aggregate
    (DuckDB rejects ``CAST(AVG(...) AS T) OVER (...)`` as a parse
    error). The rule detects when the AVG is the ``this`` operand of
    a :class:`exp.Window` parent and rewrites the Window node
    instead.

    The rule fires only when the AVG operand's annotated type is
    DECIMAL. AVG over INTEGER / FLOAT operands (or operands the
    annotator could not resolve) flows through unchanged so BigQuery's
    integer / float ``AVG`` contract (``→ FLOAT64``) is preserved.
    """

    name = "AVG_DECIMAL"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match ``AVG(x)`` (or ``AVG(x) OVER (…)``) over a DECIMAL operand.

        We dispatch on :class:`exp.Avg` directly and, when the AVG is
        the child of a :class:`exp.Window`, on the Window itself —
        the rewrite at the Window level lets the cast surround the
        whole ``AVG(…) OVER (…)`` expression, which is the syntactic
        form DuckDB requires.
        """
        if isinstance(node, exp.Window) and isinstance(node.this, exp.Avg):
            return _avg_operand_is_decimal(node.this)
        if not isinstance(node, exp.Avg):
            return False
        if isinstance(node.parent, exp.Window):
            # Window-parent case is handled when the Window node is
            # visited — skip the inner AVG so we don't double-wrap.
            return False
        return _avg_operand_is_decimal(node)

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Wrap the AVG (or window-AVG) in ``CAST(... AS DECIMAL(38, 9))``."""
        return exp.Cast(this=node.copy(), to=exp.DataType.build(_NUMERIC_TYPE))


def _avg_operand_is_decimal(avg_node: exp.Expression) -> bool:
    """Return True when *avg_node*'s operand is annotated as DECIMAL."""
    operand = avg_node.this if isinstance(avg_node, exp.Avg) else None
    if operand is None:
        return False
    operand_type = getattr(operand, "type", None)
    if operand_type is None:
        return False
    return _is_decimal(operand_type)


@register
class ArrayConcatAggRule(TranslationRule):
    """``ARRAY_CONCAT_AGG(arr [ORDER BY …])`` → ``flatten(array_agg(arr [ORDER BY …]))``.

    BigQuery's ``ARRAY_CONCAT_AGG`` returns the concatenation of every
    non-NULL array value across the input rows. DuckDB does not ship
    ``array_concat_agg`` (``list_concat_agg`` is also absent), but the
    same semantic falls out of ``flatten(array_agg(arr))``:

    * ``array_agg`` collects the per-row arrays into an
      array-of-arrays and skips NULL inputs by default — matching
      BigQuery's "ignores NULL input arrays" contract.
    * ``flatten`` concatenates the inner arrays into a single array
      preserving element order.

    Ordering propagates through unchanged: SQLGlot models the inner
    ``ORDER BY`` clause as ``ArrayConcatAgg(this=Order(this=col,
    expressions=[Ordered(...)]))`` and DuckDB's ``array_agg`` accepts
    the same ``ORDER BY`` shape — we copy the ``Order`` node as the
    ``array_agg`` argument verbatim.
    """

    name = "ARRAY_CONCAT_AGG"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match SQLGlot's typed ``ArrayConcatAgg`` AST node."""
        return isinstance(node, exp.ArrayConcatAgg)

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Emit ``flatten(array_agg(operand))`` preserving the ORDER BY."""
        operand = node.this
        if operand is None:
            return node
        array_agg = exp.Anonymous(
            this="array_agg",
            expressions=[operand.copy()],
        )
        return exp.Anonymous(
            this="flatten",
            expressions=[array_agg],
        )


_DECIMAL_TYPES = frozenset(
    {
        exp.DataType.Type.DECIMAL,
        exp.DataType.Type.DECIMAL32,
        exp.DataType.Type.DECIMAL64,
        exp.DataType.Type.DECIMAL128,
        exp.DataType.Type.DECIMAL256,
        exp.DataType.Type.BIGDECIMAL,
    },
)


def _is_decimal(dtype: exp.DataType) -> bool:
    """Return True when *dtype* is any DECIMAL family type."""
    return dtype.this in _DECIMAL_TYPES


@register
class DivDecimalRule(TranslationRule):
    """``<dec> / <x>`` or ``<x> / <dec>`` → ``CAST(... AS DECIMAL(38, 9))``.

    BigQuery's contract: when at least one operand of a binary ``/``
    is NUMERIC (or BIGNUMERIC), the result is NUMERIC. Mixed-type
    division coerces the non-NUMERIC operand to NUMERIC at runtime —
    so ``NUMERIC / FLOAT64`` returns NUMERIC, ``INT64 / NUMERIC``
    returns NUMERIC, etc. DuckDB instead promotes ``DECIMAL / DECIMAL``
    (and any DECIMAL-involving division) to ``DOUBLE`` for precision-
    safety, which the wire-format renderer surfaces as ``FLOAT``.

    The rule fires when SQLGlot's ``annotate_types`` pass has resolved
    either operand to a DECIMAL family type, and wraps the Div in
    ``CAST(... AS DECIMAL(38, 9))`` so the result column type matches
    BigQuery's NUMERIC contract. The downstream ``ROUND`` (when
    present) operates on DECIMAL and preserves DECIMAL via DuckDB's
    DECIMAL-typed ``ROUND`` overload.

    The rule skips when neither operand resolves to DECIMAL — pure
    FLOAT64 or INT64 division flows through unchanged so BigQuery's
    ``FLOAT64 / FLOAT64 → FLOAT64`` and ``INT64 / INT64 → FLOAT64``
    contracts (matching DuckDB's behaviour) are preserved.

    The annotation-based detection mirrors :class:`AvgDecimalRule`'s
    operand-type-sensitive design. When the catalog cannot annotate
    operand types (no schema available), the rule skips — matching
    the legacy emulator's behaviour.
    """

    name = "DIV_DECIMAL"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match a ``Div`` where at least one operand is DECIMAL-annotated.

        The check is symmetric (LHS-decimal OR RHS-decimal) because
        BigQuery's mixed-type coercion is also symmetric — the
        wider-precision type wins regardless of which side carries
        it. Operands without resolved types (no annotation) are
        treated as non-DECIMAL so the rule remains a no-op on
        un-annotated trees.
        """
        if not isinstance(node, exp.Div):
            return False
        # Avoid wrapping a Div that's already inside our own CAST —
        # the rule sets ``to=DECIMAL(38, 9)`` so a parent ``Cast``
        # with matching target is a fixed point.
        parent = node.parent
        if isinstance(parent, exp.Cast):
            target = parent.to
            if isinstance(target, exp.DataType) and _is_decimal(target):
                return False
        return _operand_is_decimal(node.this) or _operand_is_decimal(node.expression)

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Wrap the Div in ``CAST(... AS DECIMAL(38, 9))``."""
        return exp.Cast(this=node.copy(), to=exp.DataType.build(_NUMERIC_TYPE))


def _operand_is_decimal(operand: exp.Expression | None) -> bool:
    """Return True when *operand*'s annotated type is in the DECIMAL family."""
    if operand is None:
        return False
    operand_type = getattr(operand, "type", None)
    if operand_type is None:
        return False
    return _is_decimal(operand_type)


def _match_hll_count_call(node: exp.Expression, func_name: str) -> exp.Anonymous | None:
    """Return the inner ``Anonymous`` node when *node* is ``HLL_COUNT.<func_name>(...)``.

    SQLGlot models BigQuery's dotted ``HLL_COUNT.<X>(args)`` call as
    ``Dot(Identifier('HLL_COUNT'), Anonymous(this='<X>',
    expressions=[args...]))`` and preserves that shape through the
    BigQuery → DuckDB transpile (DuckDB has no HLL primitives, so
    SQLGlot has nowhere to map them — the node passes through
    unchanged). Returning the Anonymous node when matched (and
    ``None`` otherwise) lets callers do the typed `isinstance`
    narrowing once at the entry point instead of repeating the
    ``isinstance(node, exp.Dot)`` / ``isinstance(node.expression,
    exp.Anonymous)`` chain at every access.
    """
    if not isinstance(node, exp.Dot):
        return None
    left = node.this
    right = node.expression
    if not isinstance(left, exp.Identifier) or left.name.upper() != "HLL_COUNT":
        return None
    if not isinstance(right, exp.Anonymous):
        return None
    if right.this.upper() != func_name.upper():
        return None
    return right


@register
class HllCountExtractInitRule(TranslationRule):
    """``HLL_COUNT.EXTRACT(HLL_COUNT.INIT(x [, precision]))`` → ``COUNT(DISTINCT x)``.

    Detects the most common BigQuery HLL pattern — build a sketch and
    immediately extract its cardinality — and rewrites it to the exact
    ``COUNT(DISTINCT x)``. The cardinality of the extracted sketch is
    approximately equal to ``COUNT(DISTINCT x)`` (within ~1.04/√m per
    HLL's standard error); for the conformance corpus's
    small-cardinality inputs the exact and approximate values match.

    Mirrors the precedent set by
    :class:`bqemulator.sql.rules.datetime_semantics.ApproxCountDistinctExactRule`
    for ``APPROX_COUNT_DISTINCT`` (ADR 0023 §1.I): DuckDB's HLL
    primitives don't share BigQuery's small-cardinality fixup, so we
    route to the exact aggregate. See ADR 0024 for the full decision
    matrix on HLL support strategy.

    Precision argument: ``HLL_COUNT.INIT(x, P)`` accepts an optional
    precision ``P`` (BigQuery: 10-24, default 15). The precision
    affects sketch memory + accuracy but not the *cardinality* of the
    extracted result for inputs the precision's bucket count can
    represent exactly — for small-cardinality fixtures the precision
    is irrelevant and we discard it during the rewrite.

    Doesn't fire on bare ``HLL_COUNT.EXTRACT(col)`` where ``col`` is
    a column reference rather than an inline ``HLL_COUNT.INIT`` call;
    that case requires the sketch to be persisted to a table — pinned
    as XFAIL per
    ``docs/reference/out-of-scope.md#hll-sketch-binary-format-hll_countinit--merge_partial``.
    """

    name = "HLL_COUNT_EXTRACT_INIT"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match ``HLL_COUNT.EXTRACT(HLL_COUNT.INIT(x))``."""
        extract_anon = _match_hll_count_call(node, "EXTRACT")
        if extract_anon is None or len(extract_anon.expressions) != 1:
            return False
        return _match_hll_count_call(extract_anon.expressions[0], "INIT") is not None

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Emit ``COUNT(DISTINCT x)`` preserving the operand."""
        extract_anon = _match_hll_count_call(node, "EXTRACT")
        if extract_anon is None:
            return node
        init_anon = _match_hll_count_call(extract_anon.expressions[0], "INIT")
        if init_anon is None or not init_anon.expressions:
            return node
        operand = init_anon.expressions[0]
        return exp.Count(this=exp.Distinct(expressions=[operand.copy()]))


@register
class HllCountMergeRule(TranslationRule):
    """``HLL_COUNT.MERGE(col)`` → ``COUNT(DISTINCT col)`` over an inlined source subquery.

    When the ``HLL_COUNT.MERGE`` aggregate's argument is a column
    whose source — in the enclosing ``Select.from_`` subquery — is
    ``HLL_COUNT.INIT(x)`` across every leg of a (possibly UNION-joined)
    inner select, we mutate the subquery to inline the ``INIT`` calls
    (each ``HLL_COUNT.INIT(x)`` is replaced with ``x``) and rewrite
    the outer ``HLL_COUNT.MERGE(col)`` as ``COUNT(DISTINCT col)``.
    Once the rewrite completes the column references resolve to the
    raw operand values rather than sketches, and ``COUNT(DISTINCT)``
    delivers the same cardinality semantic BigQuery's
    ``HLL_COUNT.MERGE`` would.

    Doesn't fire when the source subquery's projected expression
    isn't ``HLL_COUNT.INIT(...)`` on every leg — e.g. when the
    sketch column comes from a persisted table. That pattern is
    pinned as XFAIL per
    ``docs/reference/out-of-scope.md#hll-sketch-binary-format-hll_countinit--merge_partial``.

    See ADR 0024 for the design rationale.
    """

    name = "HLL_COUNT_MERGE"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match ``HLL_COUNT.MERGE(col)`` over a UNION-of-INIT subquery."""
        merge_anon = _match_hll_count_call(node, "MERGE")
        if merge_anon is None or len(merge_anon.expressions) != 1:
            return False
        arg = merge_anon.expressions[0]
        if not isinstance(arg, exp.Column):
            return False
        legs = _hll_merge_source_legs(node)
        if legs is None:
            return False
        return all(_leg_projects_hll_init(leg) for leg in legs)

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Inline the source INIT calls and rewrite the outer MERGE to COUNT(DISTINCT)."""
        merge_anon = _match_hll_count_call(node, "MERGE")
        if merge_anon is None or len(merge_anon.expressions) != 1:
            return node
        arg = merge_anon.expressions[0]
        if not isinstance(arg, exp.Column):
            return node
        legs = _hll_merge_source_legs(node)
        if legs is None:
            return node
        for leg in legs:
            _inline_hll_init_in_leg(leg)
        return exp.Count(this=exp.Distinct(expressions=[arg.copy()]))


def _hll_merge_source_legs(merge_node: exp.Expression) -> list[exp.Select] | None:
    """Return the SELECT legs feeding the *merge_node*'s column argument.

    Walks up from *merge_node* to its enclosing :class:`exp.Select`,
    reads the ``from_`` subquery, and returns the leg-Selects of a
    UNION (or the single Select if the subquery is not a UNION).
    Returns ``None`` when no enclosing FROM subquery exists or the
    subquery shape isn't recognised.
    """
    enclosing = merge_node.parent
    while enclosing is not None and not isinstance(enclosing, exp.Select):
        enclosing = enclosing.parent
    if enclosing is None:
        return None
    from_node = enclosing.args.get("from_")
    if not isinstance(from_node, exp.From):
        return None
    sub = from_node.this
    if not isinstance(sub, exp.Subquery):
        return None
    inner = sub.this
    if isinstance(inner, exp.Union):
        legs: list[exp.Select] = []
        # Union supports n-way nesting (UNION ALL of 3+ legs nests
        # left-deep), so we walk to collect every Select leaf.
        _collect_union_legs(inner, legs)
        return legs
    if isinstance(inner, exp.Select):
        return [inner]
    return None


def _collect_union_legs(union: exp.Union, legs: list[exp.Select]) -> None:
    """Append every Select leaf of *union* to *legs* in left-to-right order."""
    left = union.this
    right = union.expression
    if isinstance(left, exp.Union):
        _collect_union_legs(left, legs)
    elif isinstance(left, exp.Select):
        legs.append(left)
    if isinstance(right, exp.Union):
        _collect_union_legs(right, legs)
    elif isinstance(right, exp.Select):
        legs.append(right)


def _leg_projects_hll_init(leg: exp.Select) -> bool:
    """Return True when *leg*'s position-0 projection is ``HLL_COUNT.INIT(...)``.

    The MERGE's column name aliases the sketch projection in the
    first leg; subsequent UNION legs need not re-alias because SQL's
    UNION uses the first leg's column names. We match by position-0
    on every leg and tolerate an Alias wrapper on the first.
    """
    if not leg.expressions:
        return False
    expr = leg.expressions[0]
    inner = expr.this if isinstance(expr, exp.Alias) else expr
    return _match_hll_count_call(inner, "INIT") is not None


def _inline_hll_init_in_leg(leg: exp.Select) -> None:
    """Replace the position-0 ``HLL_COUNT.INIT(x)`` projection with ``x``.

    Preserves any column alias on the projection so the MERGE's outer
    column reference still resolves after the rewrite.
    """
    if not leg.expressions:
        return
    expr = leg.expressions[0]
    if isinstance(expr, exp.Alias):
        init_anon = _match_hll_count_call(expr.this, "INIT")
        if init_anon is not None and init_anon.expressions:
            expr.set("this", init_anon.expressions[0].copy())
        return
    init_anon = _match_hll_count_call(expr, "INIT")
    if init_anon is not None and init_anon.expressions:
        expr.replace(init_anon.expressions[0].copy())


__all__ = [
    "ArrayConcatAggRule",
    "AvgDecimalRule",
    "HllCountExtractInitRule",
    "HllCountMergeRule",
]
