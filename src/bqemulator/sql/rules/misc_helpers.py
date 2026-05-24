"""Translation rules for miscellaneous GoogleSQL builtins.

Functions covered:

* ``IEEE_DIVIDE(a, b)`` — IEEE-754 division returning ``±Inf`` / ``NaN``
  instead of raising on zero. Emits
  ``CAST(a AS DOUBLE) / CAST(b AS DOUBLE)`` so DuckDB's natural float
  arithmetic produces ``±Inf`` for zero divisors and ``NaN`` for
  ``0/0``. DuckDB does not raise on float division by zero.

* ``FARM_FINGERPRINT(s)`` — BigQuery's FarmHash ``Fingerprint64``.
  DuckDB ships no native FarmHash; routes through the Python helper
  ``bqemu_farm_fingerprint`` registered in
  :mod:`bqemulator.sql.builtin_udfs`. The helper emits a deterministic
  64-bit signed hash derived from SHA-256; bit-patterns are not
  expected to match real BigQuery, so dependent fixtures cascade to
  ADR 0023 §1.I (bit-exact mismatch).

* ``RANGE_BUCKET(point, boundaries)`` — returns the count of
  boundaries ≤ ``point``. Emits
  ``len(list_filter(boundaries, x -> x <= point))`` which mirrors the
  semantic for the half-open ``[10, 20) → bucket 1`` example.

* ``APPROX_TOP_SUM(value, weight, k)`` — BigQuery returns an array of
  ``{value, sum}`` STRUCT records ordered by weighted sum descending.
  DuckDB ships only ``approx_top_k(value, k)`` (no weight). Rewrites
  ``APPROX_TOP_SUM(value, weight, k)`` to ``approx_top_k(value, k)``;
  dependent fixtures cascade to ADR 0023 §1.I for the ranking
  difference.
"""

from __future__ import annotations

from sqlglot import exp

from bqemulator.sql.rules import register
from bqemulator.sql.rules._base import TranslationRule


def _anon(name: str, *args: exp.Expression) -> exp.Anonymous:
    """Build an anonymous DuckDB-side function call with copied args."""
    return exp.Anonymous(this=name, expressions=[arg.copy() for arg in args])


@register
class IeeeDivideRule(TranslationRule):
    """``IEEE_DIVIDE(a, b)`` → ``CAST(a AS DOUBLE) / CAST(b AS DOUBLE)``.

    Both operands must end up as ``DOUBLE`` so DuckDB's IEEE-754
    division kicks in (yielding ``±Inf`` / ``NaN`` rather than raising
    on a zero divisor) and the result's Arrow type is ``float64``,
    matching the BigQuery wire-format expectation.
    """

    name = "IEEE_DIVIDE"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match ``Anonymous(IEEE_DIVIDE)`` calls with two operands."""
        return (
            isinstance(node, exp.Anonymous)
            and str(node.this).upper() == "IEEE_DIVIDE"
            and len(node.expressions) == 2  # noqa: PLR2004
        )

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Emit ``CAST(a AS DOUBLE) / CAST(b AS DOUBLE)``."""
        a, b = node.expressions
        return exp.Div(
            this=exp.Cast(this=a.copy(), to=exp.DataType.build("DOUBLE")),
            expression=exp.Cast(this=b.copy(), to=exp.DataType.build("DOUBLE")),
        )


@register
class FarmFingerprintRule(TranslationRule):
    """``FARM_FINGERPRINT(s)`` → ``bqemu_farm_fingerprint(s)``.

    Bit-pattern compatibility with real BigQuery is *not* guaranteed —
    the helper uses a SHA-256-derived hash. Dependent fixtures
    cascade to ADR 0023 §1.I (bit-exact value mismatch).
    """

    name = "FARM_FINGERPRINT"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match the typed ``FarmFingerprint`` node."""
        return type(node).__name__ == "FarmFingerprint"

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Emit ``bqemu_farm_fingerprint(arg)``.

        SQLGlot's typed ``FarmFingerprint`` node carries its single
        argument in ``expressions`` rather than ``this`` — match the
        layout :func:`exp.FarmFingerprint` actually uses.
        """
        if not node.expressions:
            return node
        return _anon("bqemu_farm_fingerprint", node.expressions[0])


@register
class RangeBucketRule(TranslationRule):
    """``RANGE_BUCKET(point, boundaries)`` → ``len(list_filter(boundaries, x -> x <= point))``.

    Mirrors BigQuery's contract: returns the number of boundary
    entries that are less than or equal to *point*. The DuckDB
    expression evaluates the same predicate over the boundaries array
    and returns the count. BigQuery returns ``NULL`` when either
    *point* or *boundaries* is ``NULL``; DuckDB's bare ``list_filter``
    over a ``NULL`` point would emit ``0`` (every ``x <= NULL``
    predicate evaluates to ``NULL`` → falsy → filtered out, leaving a
    0-length array), masking the NULL-propagation semantic. The
    rewrite wraps the result in a ``CASE`` that returns ``NULL`` when
    either input is ``NULL``.
    """

    name = "RANGE_BUCKET"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match the typed ``RangeBucket`` node."""
        return type(node).__name__ == "RangeBucket"

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Emit a NULL-guarded ``len(list_filter(...))`` CASE expression."""
        point = node.this
        boundaries = node.expression
        x = exp.Column(this=exp.Identifier(this="x", quoted=False))
        lambda_expr = exp.Lambda(
            this=exp.LTE(this=x, expression=point.copy()),
            expressions=[exp.Column(this=exp.Identifier(this="x", quoted=False))],
        )
        happy_branch = _anon("len", _anon("list_filter", boundaries, lambda_expr))
        # NULL propagation guard: BigQuery returns NULL for
        # RANGE_BUCKET(NULL, …) and RANGE_BUCKET(…, NULL); DuckDB's bare
        # list_filter emits 0 in both cases.
        null_guard = exp.Or(
            this=exp.Is(this=point.copy(), expression=exp.Null()),
            expression=exp.Is(this=boundaries.copy(), expression=exp.Null()),
        )
        return exp.Case(
            ifs=[exp.If(this=null_guard, true=exp.Null())],
            default=happy_branch,
        )


@register
class SignFloatTypeRule(TranslationRule):
    """``SIGN(<float_arg>)`` → NaN-aware FLOAT64 wrapper.

    BigQuery's ``SIGN(x)`` returns the same type as ``x`` (``INT64 →
    INT64``, ``FLOAT64 → FLOAT64``, ``NUMERIC → NUMERIC``) and
    propagates ``NaN`` (``SIGN(NaN) = NaN``). DuckDB's ``sign(x)``
    always returns ``TINYINT`` and emits ``0`` for ``NaN`` inputs. For
    FLOAT64-typed arguments — explicit ``CAST(... AS FLOAT64)``,
    FLOAT64 literals (``±Infinity`` / ``NaN`` string-cast), and
    FLOAT64 columns — the rewrite emits ``CASE WHEN isnan(arg) THEN
    arg ELSE CAST(SIGN(arg) AS DOUBLE) END`` so the result column
    surfaces as FLOAT and ``NaN`` propagates. Detection is
    conservative: the rule fires only when the immediate argument is
    a ``CAST`` whose target type contains ``FLOAT`` or ``DOUBLE``;
    other FLOAT64-bearing shapes (column references, NUMERIC inputs)
    fall back to DuckDB's TINYINT result.
    """

    name = "SIGN_FLOAT_TYPE"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match ``Sign`` over a CAST to FLOAT64 / DOUBLE."""
        if type(node).__name__ != "Sign":
            return False
        arg = node.this
        if not isinstance(arg, exp.Cast):
            return False
        target = arg.to
        target_str = str(target).upper() if target is not None else ""
        if "FLOAT" not in target_str and "DOUBLE" not in target_str:
            return False
        # Only fire once: if the parent is already a CASE we built
        # around this Sign node, the wrapper is in place and we should
        # not re-wrap. The CASE wrapper has the Sign in its default
        # branch, so detect it via the parent's structure.
        parent = node.parent
        if isinstance(parent, exp.Cast):
            grandparent = parent.parent
            if isinstance(grandparent, exp.Case):
                return False
        return True

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Emit ``CASE WHEN isnan(arg) THEN arg ELSE CAST(SIGN(arg) AS DOUBLE) END``.

        The arg is the inner CAST expression (``CAST(... AS FLOAT64)``)
        we matched in ``applies_to``. NaN propagation uses DuckDB's
        ``isnan`` builtin so the rewritten expression stays in pure
        SQL with no helper UDFs.
        """
        arg = node.this
        nan_branch = exp.If(
            this=_anon("isnan", arg.copy()),
            true=arg.copy(),
        )
        default_branch = exp.Cast(this=node.copy(), to=exp.DataType.build("DOUBLE"))
        return exp.Case(ifs=[nan_branch], default=default_branch)


@register
class CountIfEmptyZeroRule(TranslationRule):
    """``COUNTIF(p)`` → ``COALESCE(COUNTIF(p), 0)`` (wrapped only when needed).

    BigQuery's ``COUNTIF`` returns ``0`` for an empty input (the
    aggregate never sees a row, but the grouping-key-less
    ``SELECT COUNTIF(...) FROM empty_t`` still emits one row with
    ``0``). DuckDB's transpiled equivalent — typically
    ``COUNT(*) FILTER (WHERE p)`` or ``SUM(CASE WHEN p THEN 1 END)``
    via SQLGlot — emits ``NULL`` for the same shape. Wrapping the
    typed ``CountIf`` node in ``COALESCE(..., 0)`` recovers
    BigQuery's "always-INT64, never NULL" contract; on non-empty
    sources the ``COALESCE`` is a no-op.
    """

    name = "COUNTIF_EMPTY_ZERO"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match the typed ``CountIf`` node — unless already wrapped."""
        if type(node).__name__ != "CountIf":
            return False
        parent = node.parent
        already_wrapped = (
            isinstance(parent, exp.Coalesce)
            and parent.expressions
            and parent.expressions[0] is node
        )
        return not already_wrapped

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Emit ``COALESCE(<original_countif>, 0)``."""
        return exp.Coalesce(this=node.copy(), expressions=[exp.Literal.number(0)])


@register
class ApproxTopSumRule(TranslationRule):
    """``APPROX_TOP_SUM(value, weight, k)`` → ``approx_top_k(value, k)``.

    DuckDB has no weighted equivalent. The non-weighted top-k
    stand-in preserves the BigQuery output shape (``ARRAY<value>``).
    Callers that only inspect the array length recover the expected
    cardinality; callers that depend on the ranking weight see
    BigQuery's weighted ordering replaced by DuckDB's unweighted one.
    """

    name = "APPROX_TOP_SUM"

    def applies_to(self, node: exp.Expression) -> bool:
        """Match the typed ``ApproxTopSum`` node."""
        return type(node).__name__ == "ApproxTopSum"

    def rewrite(self, node: exp.Expression) -> exp.Expression:
        """Emit ``approx_top_k(value, k)``."""
        value = node.this
        count = node.args.get("count")
        if count is None:  # defensive — BQ always passes a count.
            return node
        return _anon("approx_top_k", value, count)


__all__ = [
    "ApproxTopSumRule",
    "CountIfEmptyZeroRule",
    "FarmFingerprintRule",
    "IeeeDivideRule",
    "RangeBucketRule",
    "SignFloatTypeRule",
]
