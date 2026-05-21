"""Tests for the aggregate-type and aggregate-name translation rules.

Three distinct concerns live in :mod:`bqemulator.sql.rules.aggregate_types`:

* :class:`AvgDecimalRule` consults SQLGlot's ``annotate_types`` output to
  decide whether to wrap ``AVG(x)`` in a ``CAST(... AS DECIMAL(38, 9))`` —
  DuckDB's ``AVG(DECIMAL)`` returns ``DOUBLE`` while BigQuery preserves
  ``NUMERIC``.
* :class:`ArrayConcatAggRule` rewrites BigQuery's ``ARRAY_CONCAT_AGG``
  to DuckDB's ``flatten(array_agg(...))`` because DuckDB has no
  ``array_concat_agg`` primitive and SQLGlot emits the BigQuery name
  verbatim.
* :class:`HllCountExtractInitRule` and :class:`HllCountMergeRule`
  translate the two common BigQuery HLL patterns to ``COUNT(DISTINCT x)``
  (the cardinality-preserving rewrite documented in ADR 0024). DuckDB
  has no HLL primitives and BigQuery's HLL++ sketch BYTES format is
  undocumented; the cardinality user-facing semantic is preserved via
  the exact-aggregate equivalence, while the sketch-as-persistable-BYTES
  semantic is pinned as XFAIL.
"""

from __future__ import annotations

from decimal import Decimal

import duckdb
import pytest

from bqemulator.domain.result import Ok
from bqemulator.sql.translator import SQLTranslator

pytestmark = pytest.mark.unit


@pytest.fixture
def t() -> SQLTranslator:
    return SQLTranslator()


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    return duckdb.connect()


class TestAvgDecimalRule:
    """``AVG(decimal_col)`` → ``CAST(AVG(decimal_col) AS DECIMAL(38, 9))``."""

    def test_wraps_when_operand_is_decimal(self, t: SQLTranslator) -> None:
        schema = {"orders": {"amount": "DECIMAL(38, 9)", "order_id": "BIGINT"}}
        result = t.translate(
            "SELECT AVG(amount) AS avg_amount FROM `test-project.bqemu.orders`",
            schema=schema,
        )
        assert isinstance(result, Ok)
        upper = result.value.upper()
        assert "CAST(AVG" in upper
        assert "DECIMAL(38, 9)" in upper

    def test_preserves_int_avg(self, t: SQLTranslator) -> None:
        # AVG over INTEGER stays DOUBLE — the rule must not fire.
        schema = {"nums": {"n": "BIGINT"}}
        result = t.translate(
            "SELECT AVG(n) AS x FROM `test-project.bqemu.nums`",
            schema=schema,
        )
        assert isinstance(result, Ok)
        assert "CAST(AVG" not in result.value.upper()

    def test_preserves_float_avg(self, t: SQLTranslator) -> None:
        # AVG over FLOAT stays DOUBLE — no cast.
        schema = {"vals": {"v": "DOUBLE"}}
        result = t.translate(
            "SELECT AVG(v) AS x FROM `test-project.bqemu.vals`",
            schema=schema,
        )
        assert isinstance(result, Ok)
        assert "CAST(AVG" not in result.value.upper()

    def test_skips_without_schema(self, t: SQLTranslator) -> None:
        # No schema → annotate_types can't resolve the column → rule
        # skips. (Falls back to legacy emulator behaviour.)
        result = t.translate("SELECT AVG(amount) FROM `test-project.bqemu.orders`")
        assert isinstance(result, Ok)
        assert "CAST(AVG" not in result.value.upper()

    def test_executes_cleanly_against_duckdb(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        # End-to-end: catalog-typed DECIMAL column, AVG wrapped, DuckDB
        # returns DECIMAL.
        con.execute('CREATE SCHEMA "test-project__bqemu"')
        con.execute(
            'CREATE TABLE "test-project__bqemu"."orders" (amount DECIMAL(38, 9))',
        )
        con.execute(
            'INSERT INTO "test-project__bqemu"."orders" VALUES (100.00), (200.50), (300.75)',
        )
        schema = {"orders": {"amount": "DECIMAL(38, 9)"}}
        # The translator's output references the BigQuery-style
        # back-tick form; we substitute the DuckDB-style ref by hand
        # so the assertion focuses on the AVG cast.
        result = t.translate(
            "SELECT AVG(amount) AS x FROM `test-project.bqemu.orders`",
            schema=schema,
        )
        assert isinstance(result, Ok)
        duckdb_sql = result.value.replace(
            '"test-project"."bqemu"."orders"',
            '"test-project__bqemu"."orders"',
        )
        row = con.execute(duckdb_sql).fetchone()
        assert row[0] == Decimal("200.416666667")
        desc = con.execute(duckdb_sql).description
        assert "DECIMAL(38,9)" in str(desc[0][1])

    def test_window_avg_wrapped(self, t: SQLTranslator) -> None:
        schema = {"events": {"user_id": "BIGINT", "amount": "DECIMAL(38, 9)"}}
        result = t.translate(
            "SELECT user_id, AVG(amount) OVER (PARTITION BY user_id) AS a "
            "FROM `test-project.bqemu.events`",
            schema=schema,
        )
        assert isinstance(result, Ok)
        upper = result.value.upper()
        assert "CAST(AVG" in upper
        assert "DECIMAL(38, 9)" in upper

    def test_nested_round_avg_wrapped(self, t: SQLTranslator) -> None:
        # ``ROUND(AVG(decimal_col), n)`` — once AVG is wrapped in
        # CAST AS DECIMAL, ROUND's input is DECIMAL and its output
        # remains DECIMAL.
        schema = {"lineitem": {"l_quantity": "DECIMAL(38, 9)"}}
        result = t.translate(
            "SELECT ROUND(AVG(l_quantity), 4) AS x FROM `test-project.bqemu.lineitem`",
            schema=schema,
        )
        assert isinstance(result, Ok)
        upper = result.value.upper()
        assert "CAST(AVG" in upper


class TestDivDecimalRule:
    """``Div`` with DECIMAL operand → ``CAST(... AS DECIMAL(38, 9))``.

    DuckDB's ``DECIMAL / DECIMAL`` promotes to ``DOUBLE`` for
    precision-safety; BigQuery's ``NUMERIC / NUMERIC`` preserves
    ``NUMERIC``. The rule wraps the Div node so the result column
    surfaces as NUMERIC in the wire format.
    """

    def test_wraps_decimal_div_decimal(self, t: SQLTranslator) -> None:
        # SUM(DECIMAL) / SUM(DECIMAL) — both operands annotated
        # DECIMAL → rule fires and wraps the Div.
        schema = {"lineitem": {"l_extendedprice": "DECIMAL(38, 9)", "l_discount": "DECIMAL(38, 9)"}}
        result = t.translate(
            "SELECT SUM(l_extendedprice) / SUM(l_discount) AS r FROM `test-project.bqemu.lineitem`",
            schema=schema,
        )
        assert isinstance(result, Ok)
        upper = result.value.upper()
        # Look for the CAST wrap form. SQLGlot may render the cast
        # either as ``CAST(... AS DECIMAL(38, 9))`` or
        # ``...::DECIMAL(38, 9)``; check for the type marker.
        assert "DECIMAL(38, 9)" in upper

    def test_wraps_decimal_div_float_literal(self, t: SQLTranslator) -> None:
        # SUM(DECIMAL) / 7.0 — RHS literal is DOUBLE per SQLGlot's
        # annotator, but BigQuery coerces the literal to NUMERIC at
        # runtime (see Q17). The rule fires because at least one
        # operand is DECIMAL.
        schema = {"lineitem": {"l_extendedprice": "DECIMAL(38, 9)"}}
        result = t.translate(
            "SELECT SUM(l_extendedprice) / 7.0 AS r FROM `test-project.bqemu.lineitem`",
            schema=schema,
        )
        assert isinstance(result, Ok)
        assert "DECIMAL(38, 9)" in result.value.upper()

    def test_wraps_float_literal_times_decimal_div_decimal(self, t: SQLTranslator) -> None:
        # 100.00 * SUM(DECIMAL) / SUM(DECIMAL) — outer Div has both
        # operands annotated DECIMAL (the Mul propagates DECIMAL from
        # the SUM); rule fires on the Div.
        schema = {"lineitem": {"l_extendedprice": "DECIMAL(38, 9)", "l_discount": "DECIMAL(38, 9)"}}
        result = t.translate(
            "SELECT ROUND(100.00 * SUM(l_extendedprice * (1 - l_discount)) "
            "/ SUM(l_extendedprice * (1 - l_discount)), 4) AS r "
            "FROM `test-project.bqemu.lineitem`",
            schema=schema,
        )
        assert isinstance(result, Ok)
        assert "DECIMAL(38, 9)" in result.value.upper()

    def test_preserves_int_div(self, t: SQLTranslator) -> None:
        # INT / INT → DOUBLE in BQ and DuckDB. Neither operand is
        # DECIMAL → rule must not fire.
        schema = {"nums": {"a": "BIGINT", "b": "BIGINT"}}
        result = t.translate(
            "SELECT a / b AS r FROM `test-project.bqemu.nums`",
            schema=schema,
        )
        assert isinstance(result, Ok)
        # CAST AS DECIMAL must NOT be emitted around the Div. Other
        # casts elsewhere in the SQL are fine — we check there's no
        # ``CAST(... / ... AS DECIMAL(38, 9))`` shape.
        upper = result.value.upper()
        assert "DECIMAL(38, 9)" not in upper

    def test_preserves_float_div_float(self, t: SQLTranslator) -> None:
        # FLOAT / FLOAT → FLOAT in BQ; rule must not fire.
        schema = {"vals": {"x": "DOUBLE", "y": "DOUBLE"}}
        result = t.translate(
            "SELECT x / y AS r FROM `test-project.bqemu.vals`",
            schema=schema,
        )
        assert isinstance(result, Ok)
        assert "DECIMAL(38, 9)" not in result.value.upper()

    def test_skips_without_schema(self, t: SQLTranslator) -> None:
        # No schema → annotate_types can't resolve → rule skips.
        result = t.translate(
            "SELECT amount / qty FROM `test-project.bqemu.orders`",
        )
        assert isinstance(result, Ok)
        assert "DECIMAL(38, 9)" not in result.value.upper()

    def test_preserves_math_csc_float_pattern(self, t: SQLTranslator) -> None:
        # ``ROUND(1 / SIN(1), 6)`` (math_csc fixture). The Div's
        # operands are INT and DOUBLE; neither is DECIMAL; the rule
        # must not fire so the result stays FLOAT (matching BQ).
        result = t.translate("SELECT ROUND(1 / SIN(1), 6) AS x")
        assert isinstance(result, Ok)
        upper = result.value.upper()
        assert "DECIMAL(38, 9)" not in upper

    def test_executes_cleanly_against_duckdb(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        # End-to-end: catalog-typed DECIMAL columns, Div wrapped,
        # DuckDB returns DECIMAL.
        con.execute('CREATE SCHEMA "test-project__bqemu"')
        con.execute(
            'CREATE TABLE "test-project__bqemu"."lineitem" '
            "(l_extendedprice DECIMAL(38, 9), l_discount DECIMAL(38, 9))",
        )
        con.execute(
            'INSERT INTO "test-project__bqemu"."lineitem" VALUES (100.00, 0.05), (200.00, 0.10)',
        )
        schema = {"lineitem": {"l_extendedprice": "DECIMAL(38, 9)", "l_discount": "DECIMAL(38, 9)"}}
        result = t.translate(
            "SELECT SUM(l_extendedprice) / SUM(l_discount) AS r FROM `test-project.bqemu.lineitem`",
            schema=schema,
        )
        assert isinstance(result, Ok)
        duckdb_sql = result.value.replace(
            '"test-project"."bqemu"."lineitem"',
            '"test-project__bqemu"."lineitem"',
        )
        desc = con.execute(duckdb_sql).description
        assert "DECIMAL" in str(desc[0][1]).upper()


class TestArrayConcatAggRule:
    """``ARRAY_CONCAT_AGG`` → ``flatten(array_agg(...))``."""

    def test_basic_rewrites_to_flatten_array_agg(self, t: SQLTranslator) -> None:
        # The ``array_concat_agg`` name must disappear from the output
        # SQL (DuckDB has no such function); ``flatten`` + ``array_agg``
        # must both appear.
        result = t.translate(
            "SELECT ARRAY_CONCAT_AGG(arr) AS combined FROM `test-project.bqemu.t`",
        )
        assert isinstance(result, Ok)
        out = result.value.upper()
        assert "ARRAY_CONCAT_AGG" not in out
        assert "FLATTEN" in out
        assert "ARRAY_AGG" in out

    def test_executes_against_duckdb(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        con.execute('CREATE SCHEMA "test-project__bqemu"')
        con.execute(
            'CREATE TABLE "test-project__bqemu"."arrs" (id INTEGER, arr INTEGER[])',
        )
        con.execute(
            'INSERT INTO "test-project__bqemu"."arrs" VALUES (1, [1, 2]), (2, [3, 4]), (3, [5, 6])',
        )
        result = t.translate(
            "SELECT ARRAY_CONCAT_AGG(arr ORDER BY id) AS combined FROM `test-project.bqemu.arrs`",
        )
        assert isinstance(result, Ok)
        duckdb_sql = result.value.replace(
            '"test-project"."bqemu"."arrs"',
            '"test-project__bqemu"."arrs"',
        )
        row = con.execute(duckdb_sql).fetchone()
        assert row == ([1, 2, 3, 4, 5, 6],)

    def test_preserves_order_by_clause(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        # Inner ``ORDER BY id DESC`` must round-trip through the rewrite
        # so the flattened array reflects the requested ordering.
        con.execute('CREATE SCHEMA "test-project__bqemu"')
        con.execute(
            'CREATE TABLE "test-project__bqemu"."arrs" (id INTEGER, arr INTEGER[])',
        )
        con.execute(
            'INSERT INTO "test-project__bqemu"."arrs" VALUES (1, [1, 2]), (2, [3, 4]), (3, [5, 6])',
        )
        result = t.translate(
            "SELECT ARRAY_CONCAT_AGG(arr ORDER BY id DESC) AS combined "
            "FROM `test-project.bqemu.arrs`",
        )
        assert isinstance(result, Ok)
        duckdb_sql = result.value.replace(
            '"test-project"."bqemu"."arrs"',
            '"test-project__bqemu"."arrs"',
        )
        row = con.execute(duckdb_sql).fetchone()
        assert row == ([5, 6, 3, 4, 1, 2],)

    def test_skips_unrelated_aggregates(self, t: SQLTranslator) -> None:
        # Regression guard — the rule must NOT fire on ``ARRAY_AGG`` or
        # ``ANY_VALUE`` calls that happen to live near
        # ``ARRAY_CONCAT_AGG`` in the AST.
        result = t.translate(
            "SELECT ARRAY_AGG(x) AS a, ANY_VALUE(x) AS b FROM `test-project.bqemu.t`",
        )
        assert isinstance(result, Ok)
        upper = result.value.upper()
        # The output should not introduce ``FLATTEN`` for unrelated aggs.
        assert "FLATTEN" not in upper

    def test_ignores_null_input_arrays(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        # BigQuery's ``ARRAY_CONCAT_AGG`` ignores NULL input arrays;
        # DuckDB's ``array_agg`` skips NULL inputs by default so the
        # ``flatten(array_agg(...))`` rewrite inherits the same contract.
        con.execute('CREATE SCHEMA "test-project__bqemu"')
        con.execute(
            'CREATE TABLE "test-project__bqemu"."arrs" (id INTEGER, arr INTEGER[])',
        )
        con.execute(
            'INSERT INTO "test-project__bqemu"."arrs" VALUES '
            "(1, [10, 20]), (2, NULL), (3, [30, 40]), (4, NULL), (5, [50])",
        )
        result = t.translate(
            "SELECT ARRAY_CONCAT_AGG(arr ORDER BY id) AS combined FROM `test-project.bqemu.arrs`",
        )
        assert isinstance(result, Ok)
        duckdb_sql = result.value.replace(
            '"test-project"."bqemu"."arrs"',
            '"test-project__bqemu"."arrs"',
        )
        row = con.execute(duckdb_sql).fetchone()
        assert row == ([10, 20, 30, 40, 50],)


class TestHllCountExtractInitRule:
    """``HLL_COUNT.EXTRACT(HLL_COUNT.INIT(x))`` → ``COUNT(DISTINCT x)``."""

    def test_rewrites_to_count_distinct(self, t: SQLTranslator) -> None:
        # The HLL_COUNT.* names must disappear from the output SQL;
        # ``COUNT(DISTINCT ...)`` takes their place.
        result = t.translate(
            "SELECT HLL_COUNT.EXTRACT(HLL_COUNT.INIT(n)) AS c FROM UNNEST([1, 2, 3]) AS n",
        )
        assert isinstance(result, Ok)
        upper = result.value.upper()
        assert "HLL_COUNT" not in upper
        assert "COUNT(DISTINCT" in upper

    def test_executes_against_duckdb(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        # End-to-end: 10 distinct INT64 values → cardinality 10.
        result = t.translate(
            "SELECT HLL_COUNT.EXTRACT(HLL_COUNT.INIT(n)) AS c "
            "FROM UNNEST([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]) AS n",
        )
        assert isinstance(result, Ok)
        row = con.execute(result.value).fetchone()
        assert row == (10,)

    def test_preserves_init_precision_arg(self, t: SQLTranslator) -> None:
        # ``HLL_COUNT.INIT(x, P)`` carries an optional precision arg.
        # The rewrite discards the precision (it doesn't affect the
        # *cardinality* of the result — only sketch memory/accuracy)
        # and emits the same ``COUNT(DISTINCT x)`` form.
        result = t.translate(
            "SELECT HLL_COUNT.EXTRACT(HLL_COUNT.INIT(n, 15)) AS c FROM UNNEST([1, 2, 3]) AS n",
        )
        assert isinstance(result, Ok)
        upper = result.value.upper()
        assert "HLL_COUNT" not in upper
        assert "COUNT(DISTINCT" in upper

    def test_skips_bare_init_call(self, t: SQLTranslator) -> None:
        # ``HLL_COUNT.INIT(n)`` standalone (no enclosing EXTRACT) must
        # not be rewritten — DuckDB will reject the function, surfacing
        # as ``InvalidQueryError``, and the XFAIL fixtures handle this.
        result = t.translate(
            "SELECT TO_HEX(HLL_COUNT.INIT(n)) AS sketch FROM UNNEST([1, 2, 3]) AS n",
        )
        assert isinstance(result, Ok)
        upper = result.value.upper()
        assert "HLL_COUNT.INIT" in upper
        assert "COUNT(DISTINCT" not in upper

    def test_skips_unrelated_count_distinct(self, t: SQLTranslator) -> None:
        # Regression guard: the rule must not fire on a plain
        # ``COUNT(DISTINCT x)`` (which is exactly what the rewrite
        # produces) — otherwise the post-order walk would loop.
        result = t.translate(
            "SELECT COUNT(DISTINCT n) AS c FROM UNNEST([1, 2, 3]) AS n",
        )
        assert isinstance(result, Ok)
        upper = result.value.upper()
        assert "HLL_COUNT" not in upper
        # The shape stays ``COUNT(DISTINCT n)``; the rule is a no-op here.
        assert "COUNT(DISTINCT" in upper


class TestHllCountMergeRule:
    """``HLL_COUNT.MERGE(sketch)`` over inline ``HLL_COUNT.INIT`` legs."""

    def test_rewrites_merge_of_subquery_of_init(self, t: SQLTranslator) -> None:
        # The HLL_COUNT.* names must disappear from both the outer
        # aggregate and the inner subquery legs; the inner legs project
        # the raw operand and the outer aggregate becomes
        # ``COUNT(DISTINCT sketch)``.
        result = t.translate(
            "SELECT HLL_COUNT.MERGE(sketch) AS c FROM ("
            "  SELECT HLL_COUNT.INIT(n) AS sketch FROM UNNEST([1, 2, 3]) AS n"
            "  UNION ALL"
            "  SELECT HLL_COUNT.INIT(n) FROM UNNEST([4, 5, 6]) AS n"
            ")",
        )
        assert isinstance(result, Ok)
        upper = result.value.upper()
        assert "HLL_COUNT" not in upper
        assert "COUNT(DISTINCT" in upper

    def test_executes_against_duckdb(
        self, t: SQLTranslator, con: duckdb.DuckDBPyConnection
    ) -> None:
        # End-to-end: two unioned legs of 3 distinct INT64 values each →
        # merged cardinality 6.
        result = t.translate(
            "SELECT HLL_COUNT.MERGE(sketch) AS c FROM ("
            "  SELECT HLL_COUNT.INIT(n) AS sketch FROM UNNEST([1, 2, 3]) AS n"
            "  UNION ALL"
            "  SELECT HLL_COUNT.INIT(n) FROM UNNEST([4, 5, 6]) AS n"
            ")",
        )
        assert isinstance(result, Ok)
        row = con.execute(result.value).fetchone()
        assert row == (6,)

    def test_skips_bare_merge_partial(self, t: SQLTranslator) -> None:
        # ``HLL_COUNT.MERGE_PARTIAL`` returns BYTES — it is NOT one of
        # the patterns the rule targets. Even when wrapped around an
        # inline ``HLL_COUNT.INIT`` subquery, the outer call stays
        # unchanged (DuckDB will reject ``HLL_COUNT.MERGE_PARTIAL``
        # → ``InvalidQueryError``, surfacing as XFAIL per the
        # ``out-of-scope.md`` HLL sketch binary format section).
        result = t.translate(
            "SELECT TO_HEX(HLL_COUNT.MERGE_PARTIAL(sketch)) AS merged FROM ("
            "  SELECT HLL_COUNT.INIT(n) AS sketch FROM UNNEST([1, 2, 3]) AS n"
            "  UNION ALL"
            "  SELECT HLL_COUNT.INIT(n) FROM UNNEST([4, 5, 6]) AS n"
            ")",
        )
        assert isinstance(result, Ok)
        upper = result.value.upper()
        assert "HLL_COUNT.MERGE_PARTIAL" in upper
        # The MERGE rule does not fire — no COUNT(DISTINCT) is emitted
        # for the outer aggregate. The bare INIT inside the subquery
        # also stays untouched because the trigger is the outer MERGE
        # rule, not a standalone INIT rewrite.
        assert "HLL_COUNT.INIT" in upper

    def test_skips_merge_of_persisted_sketch(self, t: SQLTranslator) -> None:
        # When the sketch column comes from a persisted table rather
        # than an inline ``HLL_COUNT.INIT`` subquery, the rule must
        # not fire — that case is pinned as XFAIL per the
        # ``out-of-scope.md`` HLL sketch binary format section.
        result = t.translate(
            "SELECT HLL_COUNT.MERGE(sketch) AS c FROM `test-project.bqemu.sketches`",
        )
        assert isinstance(result, Ok)
        upper = result.value.upper()
        assert "HLL_COUNT.MERGE" in upper
        assert "COUNT(DISTINCT" not in upper
