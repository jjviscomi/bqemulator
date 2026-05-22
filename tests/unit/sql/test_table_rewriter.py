"""Tests for the table-reference rewriter."""

from __future__ import annotations

import pytest

from bqemulator.sql.table_rewriter import rewrite_table_refs

pytestmark = pytest.mark.unit


class TestTwoPartReferences:
    def test_simple_dataset_table(self) -> None:
        result = rewrite_table_refs("SELECT * FROM sales.orders", "proj")
        assert '"proj__sales"' in result
        assert '"orders"' in result

    def test_multiple_tables(self) -> None:
        sql = "SELECT * FROM a.t1 JOIN b.t2 ON t1.id = t2.id"
        result = rewrite_table_refs(sql, "p")
        assert '"p__a"' in result
        assert '"p__b"' in result

    def test_project_with_hyphen_is_quoted(self) -> None:
        result = rewrite_table_refs("SELECT * FROM ds.tbl", "my-project")
        assert '"my-project__ds"' in result


class TestThreePartReferences:
    def test_project_dataset_table(self) -> None:
        result = rewrite_table_refs("SELECT * FROM proj.ds.tbl", "default")
        assert '"proj__ds"' in result
        assert '"tbl"' in result


class TestBacktickedCompoundIdentifier:
    """SQLGlot's BigQuery → DuckDB transpile turns ``\\`proj.ds\\``` into
    ``"proj.ds"`` (a single dotted identifier in the ``db`` slot).
    The Airflow ``BigQueryInsertJobOperator`` example emits this shape
    on every task. Without splitting the dotted ``db`` into
    ``(catalog, db)`` *before* validation, ``_BQ_DATASET_RE`` rejects
    the dotted name as an invalid dataset ID.
    """

    def test_compound_create_schema_collapses(self) -> None:
        sql = 'CREATE SCHEMA IF NOT EXISTS "bqemu-demo.airflow_demo_xyz"'
        result = rewrite_table_refs(sql, "default")
        assert '"bqemu-demo__airflow_demo_xyz"' in result
        # No zero-length trailing identifier from the schema-only path.
        assert '""' not in result

    def test_compound_select_collapses(self) -> None:
        sql = 'SELECT * FROM "p.d"."t"'
        result = rewrite_table_refs(sql, "default")
        assert '"p__d"' in result
        assert '"t"' in result


class TestSchemaOnlyReferences:
    """``CREATE SCHEMA proj.ds`` / ``DROP SCHEMA proj.ds`` parse as
    ``Table(catalog=proj, db=ds, this=Identifier(""))`` in SQLGlot's
    AST. The rewriter must collapse the catalog+db pair into a single
    ``"proj__ds"`` identifier with **no** trailing ``.""`` — without
    this dbt-bigquery's ``CREATE SCHEMA IF NOT EXISTS \\`proj\\`.\\`ds\\```
    hits DuckDB's parser as ``"proj__ds"."" `` and 400s with
    ``zero-length delimited identifier``.
    """

    def test_create_schema_two_part_collapses(self) -> None:
        sql = 'CREATE SCHEMA IF NOT EXISTS "bqemu-demo"."dbt_local_raw"'
        result = rewrite_table_refs(sql, "default")
        assert '"bqemu-demo__dbt_local_raw"' in result
        assert '""' not in result  # no zero-length trailing identifier

    def test_create_schema_unquoted_two_part_collapses(self) -> None:
        sql = "CREATE SCHEMA IF NOT EXISTS proj.ds"
        result = rewrite_table_refs(sql, "default")
        assert '"proj__ds"' in result
        assert '""' not in result

    def test_drop_schema_two_part_collapses(self) -> None:
        sql = "DROP SCHEMA proj.ds"
        result = rewrite_table_refs(sql, "default")
        assert '"proj__ds"' in result
        assert '""' not in result


class TestNoRewrite:
    def test_single_part_left_alone(self) -> None:
        result = rewrite_table_refs("SELECT * FROM bare_table", "p")
        assert "bare_table" in result
        assert "p__" not in result

    def test_unparseable_sql_returned_asis(self) -> None:
        garbage = "NOT VALID SQL AT ALL %%% &&&"
        result = rewrite_table_refs(garbage, "p")
        assert result == garbage


class TestReservedSchemas:
    def test_snapshots_schema_passes_through(self) -> None:
        """The reserved ``_bqemulator_snapshots`` schema is left untouched."""
        sql = 'SELECT * FROM "_bqemulator_snapshots"."s_001"'
        result = rewrite_table_refs(sql, "p")
        assert "_bqemulator_snapshots" in result
        assert "p___bqemulator_snapshots" not in result

    def test_user_dataset_with_reserved_prefix_still_rewritten(self) -> None:
        """A user dataset whose id starts with the reserved prefix is rewritten.

        Ensures the audit fix uses exact-match, not startswith.
        """
        sql = "SELECT * FROM _bqemulator_snap_dev.t"
        result = rewrite_table_refs(sql, "p")
        assert "p___bqemulator_snap_dev" in result


class TestTvfBacktickedCompoundQualifier:
    """SQLGlot's DuckDB dialect parses ``"proj.ds".tvf`` (the transpiled BigQuery
    backtick form) with ``Table(db='proj.ds')`` for TVF callsites. The rewriter
    must split the compound qualifier back into project + dataset halves so the
    resulting flat name validates cleanly under the SQL-id whitelist.
    """

    def test_compound_qualifier_in_tvf_call(self) -> None:
        # This is the post-translator shape the rewriter sees:
        # ``"test-project.ds1".items_below(4)`` (double-quoted compound
        # qualifier, single-segment routine name). The sanitiser maps
        # ``-`` → ``_h_`` and SQLGlot's ``.sql()`` uppercases the
        # function name, so the assertion checks the sanitised form.
        sql = 'SELECT * FROM "test-project.ds1".items_below(4)'
        result = rewrite_table_refs(sql, "ignored")
        assert "TEST_H_PROJECT__DS1__ITEMS_BELOW" in result

    def test_plain_tvf_call_still_works(self) -> None:
        sql = "SELECT * FROM ds.gen(3)"
        result = rewrite_table_refs(sql, "my-project")
        assert "MY_H_PROJECT__DS__GEN" in result

    def test_backticked_dotted_function_call_two_part(self) -> None:
        """`` `ds.fn`(args) `` form flattens to the catalog-qualified name."""
        sql = 'SELECT "DS.ADD_ONE"(41) AS r'
        result = rewrite_table_refs(sql, "my-project")
        assert "MY_H_PROJECT__DS__ADD_ONE" in result

    def test_backticked_dotted_function_call_three_part(self) -> None:
        """`` `proj.ds.fn`(args) `` form flattens with the explicit project."""
        sql = 'SELECT "PROJ.DS.ADD_ONE"(41) AS r'
        result = rewrite_table_refs(sql, "ignored")
        assert "PROJ__DS__ADD_ONE" in result

    def test_backticked_dotted_function_invalid_id_raises(self) -> None:
        """Invalid dataset characters surface as ``Function not found:`` ."""
        from bqemulator.domain.errors import InvalidQueryError

        sql = 'SELECT "BAD!!.fn"(1) AS r'
        with pytest.raises(InvalidQueryError) as exc:
            rewrite_table_refs(sql, "p")
        assert "Function not found" in str(exc.value)

    def test_backticked_dotted_function_four_part_passes_through(self) -> None:
        """4+-part names aren't routine refs; rewriter leaves them alone."""
        sql = 'SELECT "A.B.C.D"(1) AS r'
        result = rewrite_table_refs(sql, "p")
        assert "A.B.C.D" in result

    def test_compound_qualifier_invalid_id_raises_function_not_found(self) -> None:
        """``Dot(Identifier(bad-chars), Anonymous)`` raises ``Function not found:``."""
        from bqemulator.domain.errors import InvalidQueryError

        # Compound qualifier with disallowed characters (! is not in
        # the SQL-boundary whitelist) triggers the ValidationError
        # → InvalidQueryError("Function not found") branch.
        sql = 'SELECT "bad!!project.ds".fn(1) AS r'
        with pytest.raises(InvalidQueryError) as exc:
            rewrite_table_refs(sql, "p")
        assert "Function not found" in str(exc.value)


class TestInvalidDatasetId:
    r"""Malformed dataset IDs raise ``ValidationError`` mirroring BigQuery's wire shape.

    The conformance fixture uses BigQuery backtick-quoted form
    (\`!!bad-dataset!!.tbl\`) which the SQLGlot transpile turns into
    DuckDB double-quoted form (``"!!bad-dataset!!"."tbl"``) before the
    rewriter sees it. The tests here pass the post-transpile DuckDB
    shape directly.
    """

    def test_special_characters_raise_invalid(self) -> None:
        from bqemulator.domain.errors import ValidationError

        with pytest.raises(ValidationError) as exc:
            rewrite_table_refs('SELECT * FROM "!!bad-dataset!!"."tbl"', "proj")
        assert 'Invalid dataset ID "!!bad-dataset!!"' in str(exc.value)
        # BigQuery's wire format puts ``reason=invalid``.
        assert exc.value.bq_reason == "invalid"
        assert exc.value.http_status == 400
        assert exc.value.location == "!!bad-dataset!!.tbl"

    def test_dash_in_dataset_id_raises_invalid(self) -> None:
        # BigQuery's docs say dashes are allowed in dataset IDs but the
        # live service rejects them — the wire-side regex matches our
        # ``_DATASET_RE`` (alphanumeric + underscore only).
        from bqemulator.domain.errors import ValidationError

        with pytest.raises(ValidationError):
            rewrite_table_refs('SELECT * FROM "ds-with-dash"."tbl"', "proj")

    def test_valid_dataset_id_passes_through(self) -> None:
        result = rewrite_table_refs("SELECT * FROM good_ds.tbl", "proj")
        assert '"proj__good_ds"' in result

    def test_underscore_dataset_id_passes_through(self) -> None:
        result = rewrite_table_refs("SELECT * FROM _prefix_ds.tbl", "proj")
        assert '"proj___prefix_ds"' in result
