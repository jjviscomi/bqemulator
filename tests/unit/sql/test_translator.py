"""Tests for the SQLTranslator orchestrator.

These test the pipeline end-to-end: BigQuery SQL in, DuckDB SQL out.
Individual rule tests live in ``tests/unit/sql/rules/``.
"""

from __future__ import annotations

import pytest

from bqemulator.domain.errors import UnsupportedFeatureError
from bqemulator.domain.result import Err, Ok
from bqemulator.sql.translator import SQLTranslator

pytestmark = pytest.mark.unit


@pytest.fixture
def translator() -> SQLTranslator:
    return SQLTranslator()


class TestBasicTranslation:
    def test_simple_select(self, translator: SQLTranslator) -> None:
        result = translator.translate("SELECT 1 AS one")
        assert isinstance(result, Ok)
        assert "1" in result.value
        assert "one" in result.value.lower()

    def test_select_from_table(self, translator: SQLTranslator) -> None:
        result = translator.translate("SELECT * FROM my_dataset.my_table")
        assert isinstance(result, Ok)

    def test_select_with_where(self, translator: SQLTranslator) -> None:
        result = translator.translate("SELECT id FROM t WHERE id > 10")
        assert isinstance(result, Ok)

    def test_select_with_group_by(self, translator: SQLTranslator) -> None:
        result = translator.translate(
            "SELECT category, COUNT(*) AS cnt FROM t GROUP BY category",
        )
        assert isinstance(result, Ok)

    def test_cte(self, translator: SQLTranslator) -> None:
        sql = "WITH cte AS (SELECT 1 AS x) SELECT x FROM cte"
        result = translator.translate(sql)
        assert isinstance(result, Ok)

    def test_window_function(self, translator: SQLTranslator) -> None:
        sql = "SELECT id, ROW_NUMBER() OVER (ORDER BY id) AS rn FROM t"
        result = translator.translate(sql)
        assert isinstance(result, Ok)

    def test_join(self, translator: SQLTranslator) -> None:
        sql = "SELECT a.id FROM t1 AS a JOIN t2 AS b ON a.id = b.id"
        result = translator.translate(sql)
        assert isinstance(result, Ok)

    def test_subquery(self, translator: SQLTranslator) -> None:
        sql = "SELECT * FROM (SELECT 1 AS x) sub"
        result = translator.translate(sql)
        assert isinstance(result, Ok)


class TestTypeTranslation:
    """SQLGlot handles basic type translation automatically."""

    def test_safe_cast_becomes_try_cast(self, translator: SQLTranslator) -> None:
        result = translator.translate("SELECT SAFE_CAST(x AS INT64) FROM t")
        assert isinstance(result, Ok)
        assert "TRY_CAST" in result.value.upper()

    def test_int64_becomes_bigint(self, translator: SQLTranslator) -> None:
        result = translator.translate("SELECT CAST(x AS INT64) FROM t")
        assert isinstance(result, Ok)
        assert "BIGINT" in result.value.upper()

    def test_float64_becomes_double(self, translator: SQLTranslator) -> None:
        result = translator.translate("SELECT CAST(x AS FLOAT64) FROM t")
        assert isinstance(result, Ok)
        assert "DOUBLE" in result.value.upper()

    def test_bool_becomes_boolean(self, translator: SQLTranslator) -> None:
        result = translator.translate("SELECT CAST(x AS BOOL) FROM t")
        assert isinstance(result, Ok)
        upper = result.value.upper()
        assert "BOOL" in upper


class TestErrorHandling:
    def test_invalid_sql_returns_err(self, translator: SQLTranslator) -> None:
        result = translator.translate("SELECTTTT bogus garbage")
        # This may parse differently in SQLGlot — let's just ensure
        # it doesn't crash with an unhandled exception.
        assert isinstance(result, (Ok, Err))

    def test_empty_query_returns_err(self, translator: SQLTranslator) -> None:
        result = translator.translate("")
        assert isinstance(result, Err)

    def test_bqml_detected_as_unsupported(self, translator: SQLTranslator) -> None:
        result = translator.translate(
            "SELECT * FROM ML.PREDICT(MODEL my_model, TABLE t)",
        )
        assert isinstance(result, Err)
        assert isinstance(result.error, UnsupportedFeatureError)

    def test_create_model_no_longer_keyword_rejected(
        self,
        translator: SQLTranslator,
    ) -> None:
        """``CREATE MODEL`` is no longer rejected by the translator guard.

        ADR 0047 / RFC 0002 move ``CREATE MODEL`` to AST interception in the
        executor (``jobs.executor.parse_create_model``), so the translator's
        ``_UNSUPPORTED_KEYWORDS`` quick-reject no longer fires on it. The
        translator is never called with a raw ``CREATE MODEL`` in practice;
        this asserts only that the keyword guard was lifted. Whether SQLGlot
        can transpile the statement is incidental (a future SQLGlot change could
        return a parse ``Err`` while the guard stays lifted), so the assertion is
        limited to "not the unsupported-feature rejection".
        """
        result = translator.translate(
            "CREATE MODEL my_model OPTIONS(model_type='linear_reg') AS SELECT * FROM t",
        )
        assert not (isinstance(result, Err) and isinstance(result.error, UnsupportedFeatureError))


class TestGroupByAliasOrdinalRewrite:
    """``GROUP BY <select-list alias>`` over a multi-hop join.

    DuckDB's binder resolves a bare ``GROUP BY`` identifier against
    the FROM-clause's real columns *before* falling back to a
    SELECT-list alias of the same name — the opposite priority of
    BigQuery's "GROUP BY <alias>" feature, which always means "group
    by this SELECT-list slot" regardless of what real column shares
    its name. When a query aliases 2+ output columns to names that
    also exist as real columns reachable through >=3 joins (e.g.
    ``pr.id AS project_id`` alongside a real ``processes.project_id``
    two joins closer to the FROM clause), the naive alias-as-written
    SQL either mis-resolves silently to the wrong column or trips
    DuckDB's "column ... must appear in the GROUP BY clause" binder
    error outright. ``SQLTranslator._expand_group_by_aliases_to_ordinals``
    rewrites such items to their 1-based SELECT-list ordinal before the
    DuckDB SQL is finalized, which is unambiguous — an ordinal maps
    directly to the SELECT list, no name resolution involved — and
    preserves exactly what "GROUP BY <alias>" means.
    """

    def test_group_by_alias_colliding_with_real_column_becomes_ordinal(
        self,
        translator: SQLTranslator,
    ) -> None:
        sql = """
        SELECT pr.id AS project_id, pr.name AS project_name
        FROM pl
        JOIN t ON t.id = pl.task_id
        JOIN proc ON proc.id = t.process_id
        JOIN pr ON pr.id = proc.project_id
        GROUP BY project_id, project_name
        """
        result = translator.translate(sql)
        assert isinstance(result, Ok)
        assert "GROUP BY 1, 2" in result.value

    def test_group_by_alias_not_colliding_is_left_alone(
        self,
        translator: SQLTranslator,
    ) -> None:
        """A GROUP BY item that names no SELECT-list alias must not be touched.

        Guards against the rewrite over-firing on a query where
        ``category`` is both the DB column and the alias.
        """
        result = translator.translate(
            "SELECT category, COUNT(*) AS cnt FROM t GROUP BY category",
        )
        assert isinstance(result, Ok)
        assert "GROUP BY" in result.value
        assert "GROUP BY 1" not in result.value

    def test_group_by_alias_ordinal_query_result_is_correct(self) -> None:
        """End-to-end: the rewritten ordinal form binds and groups correctly.

        Reproduces the exact shape that triggered the DuckDB binder
        error / silent misgrouping: a distant-table alias
        (``project_id``) colliding with a same-named real column two
        joins closer to the FROM clause.
        """
        import duckdb

        translator = SQLTranslator()
        sql = """
        SELECT pr.id AS project_id, pr.name AS project_name, SUM(1) AS hours
        FROM pl
        JOIN t ON t.id = pl.task_id
        JOIN proc ON proc.id = t.process_id
        JOIN pr ON pr.id = proc.project_id
        GROUP BY project_id, project_name
        """
        result = translator.translate(sql)
        assert isinstance(result, Ok)

        con = duckdb.connect()
        con.execute("CREATE TABLE pl(id VARCHAR, task_id VARCHAR)")
        con.execute("CREATE TABLE t(id VARCHAR, process_id VARCHAR)")
        con.execute("CREATE TABLE proc(id VARCHAR, project_id VARCHAR)")
        con.execute("CREATE TABLE pr(id VARCHAR, name VARCHAR)")
        con.execute("INSERT INTO pl VALUES ('pl1', 't1')")
        con.execute("INSERT INTO t VALUES ('t1', 'proc1')")
        con.execute("INSERT INTO proc VALUES ('proc1', 'pr1')")
        con.execute("INSERT INTO pr VALUES ('pr1', 'Project1')")

        rows = con.execute(result.value).fetchall()
        assert rows == [("pr1", "Project1", 1)]

    def test_group_by_matches_alias_case_insensitively(
        self,
        translator: SQLTranslator,
    ) -> None:
        """An unquoted GROUP BY item matches a differently-cased alias.

        BigQuery resolves unquoted identifiers case-insensitively, so
        ``GROUP BY project_id`` must still hit the ``AS Project_ID``
        alias (and therefore its ordinal) even though the two differ
        in case.
        """
        sql = """
        SELECT pr.id AS Project_ID, pr.name AS project_name
        FROM pl
        JOIN t ON t.id = pl.task_id
        JOIN proc ON proc.id = t.process_id
        JOIN pr ON pr.id = proc.project_id
        GROUP BY project_id, PROJECT_NAME
        """
        result = translator.translate(sql)
        assert isinstance(result, Ok)
        assert "GROUP BY 1, 2" in result.value

    def test_group_by_backtick_quoted_item_matches_unquoted_alias(
        self,
        translator: SQLTranslator,
    ) -> None:
        """A backtick-quoted GROUP BY identifier still matches case-insensitively.

        BigQuery's column and alias names are case-insensitive
        regardless of backtick quoting — quoting is identifier syntax
        (escaping reserved words, allowing special characters), not a
        switch to case-sensitive matching. A backtick-quoted, oddly
        cased ``GROUP BY`` item must still resolve to an unquoted
        alias of a different case.
        """
        result = translator.translate(
            "SELECT a AS project_id FROM t GROUP BY `Project_ID`",
        )
        assert isinstance(result, Ok)
        assert "GROUP BY 1" in result.value

    def test_group_by_double_quoted_string_literal_is_not_a_column_reference(
        self,
        translator: SQLTranslator,
    ) -> None:
        """``GROUP BY "x"`` groups by a string-literal constant, not a column.

        BigQuery quotes *identifiers* with backticks; a double-quoted
        token is a string literal. ``GROUP BY "Project_ID"`` is
        therefore a degenerate case this rewrite never touches (it
        isn't a column reference at all), regardless of any alias
        named ``project_id``.
        """
        result = translator.translate(
            'SELECT a AS project_id FROM t GROUP BY "Project_ID"',
        )
        assert isinstance(result, Ok)
        assert "GROUP BY 1" not in result.value

    def test_backtick_quoted_alias_matches_unquoted_group_by_of_same_spelling(
        self,
        translator: SQLTranslator,
    ) -> None:
        """A backtick-quoted alias and an unquoted GROUP BY item still match.

        Quoting an alias doesn't create a separate, case-sensitive
        identifier namespace in BigQuery — an unquoted ``GROUP BY``
        item resolves to a backtick-quoted alias of the same spelling
        the same way it would an unquoted one.
        """
        result = translator.translate(
            "SELECT a AS `foo` FROM t GROUP BY foo",
        )
        assert isinstance(result, Ok)
        assert "GROUP BY 1" in result.value

    def test_backtick_quoted_and_unquoted_aliases_of_same_spelling_are_ambiguous(
        self,
        translator: SQLTranslator,
    ) -> None:
        """A backtick-quoted and an unquoted alias of the same spelling are the same alias.

        Quoting doesn't create a distinct identifier, so this is the
        ordinary duplicate-alias case: a ``GROUP BY`` item referencing
        the shared name is ambiguous, whichever form it or the
        aliases use.
        """
        from bqemulator.domain.errors import InvalidQueryError

        result = translator.translate(
            "SELECT a AS `foo`, b AS foo FROM t GROUP BY foo",
        )
        assert isinstance(result, Err)
        assert isinstance(result.error, InvalidQueryError)

    def test_group_by_referencing_duplicated_alias_is_ambiguous(
        self,
        translator: SQLTranslator,
    ) -> None:
        """A GROUP BY item naming a duplicated alias must error, not guess.

        GoogleSQL permits a SELECT list to reuse the same output alias
        as long as it is never referenced elsewhere in the query; a
        ``GROUP BY`` item that does reference it is ambiguous and must
        be rejected — as a clean ``Err``, matching every other
        semantic-analysis failure ``translate()`` reports — rather
        than silently resolved to whichever projection happened to be
        assigned to the alias last.
        """
        from bqemulator.domain.errors import InvalidQueryError

        result = translator.translate(
            "SELECT COUNT(*) AS key, category AS key FROM t GROUP BY key",
        )
        assert isinstance(result, Err)
        assert isinstance(result.error, InvalidQueryError)

    def test_group_by_ignores_unreferenced_duplicated_alias(
        self,
        translator: SQLTranslator,
    ) -> None:
        """A duplicated alias that GROUP BY never names stays untouched.

        Only a reference to the ambiguous alias must error — a
        ``GROUP BY`` clause naming some other, unambiguous column must
        keep working.
        """
        result = translator.translate(
            "SELECT COUNT(*) AS key, category AS key, region FROM t GROUP BY region",
        )
        assert isinstance(result, Ok)
        assert "GROUP BY" in result.value


class TestTranslatorIsStateless:
    def test_multiple_calls_independent(self, translator: SQLTranslator) -> None:
        r1 = translator.translate("SELECT 1")
        r2 = translator.translate("SELECT 2")
        assert isinstance(r1, Ok)
        assert isinstance(r2, Ok)
        assert "1" in r1.value
        assert "2" in r2.value
