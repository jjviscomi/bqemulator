"""Unit tests for the job executor's utility functions."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pyarrow as pa
import pytest

if TYPE_CHECKING:
    from pathlib import Path

    from bqemulator.config import Settings

from bqemulator.jobs.executor import (
    _arrow_type_to_bq_type,
    _is_missing_extension_error,
    _resolve_uri,
    build_response_schema,
)

pytestmark = pytest.mark.unit


class TestArrowTypeToBqType:
    @pytest.mark.parametrize(
        ("arrow_type", "expected"),
        [
            (pa.int64(), "INTEGER"),
            (pa.float64(), "FLOAT"),
            (pa.bool_(), "BOOLEAN"),
            (pa.string(), "STRING"),
            (pa.timestamp("us", tz="UTC"), "TIMESTAMP"),
            (pa.timestamp("us"), "DATETIME"),
            (pa.date32(), "DATE"),
            (pa.time64("us"), "TIME"),
            (pa.decimal128(38, 9), "NUMERIC"),
            (pa.binary(), "BYTES"),
            (pa.struct([pa.field("x", pa.int64())]), "RECORD"),
            # LIST types unwrap to the element's BigQuery type (the
            # REPEATED mode is encoded by build_response_schema).
            (pa.list_(pa.int64()), "INTEGER"),
            (pa.list_(pa.struct([pa.field("x", pa.int64())])), "RECORD"),
        ],
    )
    def test_mapping(self, arrow_type: pa.DataType, expected: str) -> None:
        assert _arrow_type_to_bq_type(arrow_type) == expected


class TestBuildResponseSchema:
    def test_multi_column(self) -> None:
        schema = pa.schema([pa.field("a", pa.int64()), pa.field("b", pa.string())])
        fields = build_response_schema(schema)
        assert len(fields) == 2
        assert fields[0] == {"name": "a", "type": "INTEGER", "mode": "NULLABLE"}

    def test_repeated_column_mode(self) -> None:
        """REPEATED columns carry mode=REPEATED and the element type (ADR 0023 §1.A)."""
        schema = pa.schema([pa.field("arr", pa.list_(pa.int64()))])
        fields = build_response_schema(schema)
        assert fields == [{"name": "arr", "type": "INTEGER", "mode": "REPEATED"}]


class TestResolveUri:
    def test_bare_path(self) -> None:
        class FakeCtx:
            class settings:  # noqa: N801
                gcs_local_root = None

        assert _resolve_uri("/data/file.csv", FakeCtx()) == "/data/file.csv"

    def test_file_protocol(self) -> None:
        class FakeCtx:
            class settings:  # noqa: N801
                gcs_local_root = None

        assert _resolve_uri("file:///data/file.csv", FakeCtx()) == "/data/file.csv"

    def test_gs_protocol_with_root(self, tmp_path: object) -> None:
        from pathlib import Path

        class FakeCtx:
            class settings:  # noqa: N801
                gcs_local_root = Path(str(tmp_path))

        result = _resolve_uri("gs://bucket/path/file.csv", FakeCtx())
        assert "bucket" in result
        assert "file.csv" in result

    def test_gs_protocol_without_root_raises(self) -> None:
        from bqemulator.domain.errors import InvalidQueryError

        class FakeCtx:
            class settings:  # noqa: N801
                gcs_local_root = None

        with pytest.raises(InvalidQueryError, match="GCS_LOCAL_ROOT"):
            _resolve_uri("gs://bucket/path", FakeCtx())


class TestIsMissingExtensionError:
    """Unit coverage for the G1 missing-extension classifier.

    Locks in the substring contract: DuckDB's canonical wording is
    ``Catalog Error: Table Function with name "<X>" is not in the
    catalog, but it exists in the <ext> extension`` (and ``Copy
    Function`` for COPY). The classifier must match that and only
    that — every other DuckDB error (missing file, schema mismatch)
    must fall through unchanged so error_mapper can translate it.
    """

    def test_matches_canonical_missing_table_function(self) -> None:
        exc = RuntimeError(
            'Catalog Error: Table Function with name "read_avro" is not '
            "in the catalog, but it exists in the avro extension. Please "
            "INSTALL avro; LOAD avro;",
        )
        assert _is_missing_extension_error(exc, "read_avro") is True

    def test_matches_canonical_missing_copy_function(self) -> None:
        exc = RuntimeError(
            'Catalog Error: Copy Function with name "avro" is not in the '
            "catalog, but it exists in the avro extension.",
        )
        assert _is_missing_extension_error(exc, "avro") is True

    def test_does_not_match_file_not_found(self) -> None:
        exc = RuntimeError(
            'IO Error: No files found that match the pattern "/tmp/x.avro"\n'
            "\nLINE 1: SELECT * FROM read_avro('/tmp/x.avro')\n",
        )
        # Echo of "read_avro" in the SQL line must NOT trigger the
        # classifier — file-not-found is a client error, not an
        # extension-unavailability error.
        assert _is_missing_extension_error(exc, "read_avro") is False

    def test_does_not_match_unrelated_catalog_error(self) -> None:
        exc = RuntimeError(
            'Catalog Error: Table with name "tbl" does not exist',
        )
        assert _is_missing_extension_error(exc, "read_avro") is False

    def test_does_not_match_function_name_mismatch(self) -> None:
        exc = RuntimeError(
            'Catalog Error: Table Function with name "read_csv" is not '
            "in the catalog, but it exists in the csv extension.",
        )
        # Looking for read_avro; this is a different function.
        assert _is_missing_extension_error(exc, "read_avro") is False


class TestRowAccessPolicyDdlDetection:
    """``CREATE / DROP ROW ACCESS POLICY`` regex detector + dispatch."""

    def test_create_rap_regex_matches(self) -> None:
        from bqemulator.jobs.executor import _RAP_CREATE_RE

        sql = (
            "CREATE ROW ACCESS POLICY eu_only ON `proj.ds.tbl` "
            "GRANT TO ('user:eu@example.com') "
            "FILTER USING (region = 'EU')"
        )
        match = _RAP_CREATE_RE.match(sql)
        assert match is not None
        assert match.group("policy") == "eu_only"
        assert match.group("filter") == "region = 'EU'"

    def test_drop_rap_regex_matches(self) -> None:
        from bqemulator.jobs.executor import _RAP_DROP_RE

        match = _RAP_DROP_RE.match("DROP ROW ACCESS POLICY p1 ON `ds.t`")
        assert match is not None
        assert match.group("policy") == "p1"

    def test_drop_rap_if_exists_matches(self) -> None:
        from bqemulator.jobs.executor import _RAP_DROP_RE

        match = _RAP_DROP_RE.match(
            "DROP ROW ACCESS POLICY IF EXISTS p1 ON `ds.t`",
        )
        assert match is not None
        assert match.group("policy") == "p1"

    def test_create_rap_if_not_exists_matches(self) -> None:
        from bqemulator.jobs.executor import _RAP_CREATE_RE

        match = _RAP_CREATE_RE.match(
            "CREATE ROW ACCESS POLICY IF NOT EXISTS eu ON proj.ds.tbl "
            "GRANT TO ('user:eu@example.com') FILTER USING (region = 'EU')",
        )
        assert match is not None
        assert match.group("policy") == "eu"

    def test_create_rap_filter_only_matches_with_null_grantees(self) -> None:
        """No ``GRANT TO`` clause → the ``grantees`` group is ``None``."""
        from bqemulator.jobs.executor import _RAP_CREATE_RE

        match = _RAP_CREATE_RE.match(
            "CREATE ROW ACCESS POLICY eu ON proj.ds.tbl FILTER USING (region = 'EU')",
        )
        assert match is not None
        assert match.group("policy") == "eu"
        assert match.group("grantees") is None
        assert match.group("filter") == "region = 'EU'"

    def test_create_rap_per_component_backticks_match(self) -> None:
        from bqemulator.jobs.executor import _RAP_CREATE_RE

        match = _RAP_CREATE_RE.match(
            "CREATE ROW ACCESS POLICY `eu` ON `proj`.`ds`.`tbl` "
            "GRANT TO ('user:eu@example.com') FILTER USING (region = 'EU')",
        )
        assert match is not None
        assert match.group("policy") == "eu"
        assert match.group("table") == "`proj`.`ds`.`tbl`"

    def test_create_rap_hyphenated_project_matches(self) -> None:
        from bqemulator.jobs.executor import _RAP_CREATE_RE

        match = _RAP_CREATE_RE.match(
            "CREATE ROW ACCESS POLICY eu ON `my-proj.ds.tbl` "
            "GRANT TO ('user:eu@example.com') FILTER USING (region = 'EU')",
        )
        assert match is not None
        assert match.group("table") == "`my-proj.ds.tbl`"

    def test_unrelated_ddl_does_not_match(self) -> None:
        from bqemulator.jobs.executor import _RAP_CREATE_RE, _RAP_DROP_RE

        assert _RAP_CREATE_RE.match("CREATE TABLE t (id INT64)") is None
        assert _RAP_DROP_RE.match("DROP TABLE t") is None


class TestClassifyRowAccessPolicy:
    """``classify_statement_type`` recognises RAP DDL via the regexes."""

    def test_create_rap_classifies(self) -> None:
        from bqemulator.jobs.executor import classify_statement_type

        assert (
            classify_statement_type(
                "CREATE ROW ACCESS POLICY eu ON proj.ds.tbl "
                "GRANT TO ('user:eu@example.com') FILTER USING (region = 'EU')",
            )
            == "CREATE_ROW_ACCESS_POLICY"
        )

    def test_create_rap_if_not_exists_classifies(self) -> None:
        from bqemulator.jobs.executor import classify_statement_type

        assert (
            classify_statement_type(
                "CREATE ROW ACCESS POLICY IF NOT EXISTS eu ON proj.ds.tbl "
                "FILTER USING (region = 'EU')",
            )
            == "CREATE_ROW_ACCESS_POLICY"
        )

    def test_drop_rap_classifies_as_drop(self) -> None:
        """Regression: DROP must not be mislabelled ``CREATE_ROW_ACCESS_POLICY``."""
        from bqemulator.jobs.executor import classify_statement_type

        assert (
            classify_statement_type("DROP ROW ACCESS POLICY p1 ON proj.ds.tbl")
            == "DROP_ROW_ACCESS_POLICY"
        )

    def test_create_rap_trailing_semicolon_and_whitespace(self) -> None:
        """The statement terminator + surrounding whitespace are normalised."""
        from bqemulator.jobs.executor import classify_statement_type

        assert (
            classify_statement_type(
                "  CREATE ROW ACCESS POLICY eu ON proj.ds.tbl FILTER USING (region = 'EU') ;  ",
            )
            == "CREATE_ROW_ACCESS_POLICY"
        )


class TestResolveTableParts:
    """Backticked-or-bare table reference parsing."""

    def test_three_part_with_backticks(self) -> None:
        from bqemulator.jobs.executor import _resolve_table_parts

        proj, ds, tbl = _resolve_table_parts("`proj.ds.tbl`", "default-proj")
        assert (proj, ds, tbl) == ("proj", "ds", "tbl")

    def test_two_part_inherits_default_project(self) -> None:
        from bqemulator.jobs.executor import _resolve_table_parts

        proj, ds, tbl = _resolve_table_parts("`ds.tbl`", "default-proj")
        assert (proj, ds, tbl) == ("default-proj", "ds", "tbl")

    def test_bare_no_backticks(self) -> None:
        from bqemulator.jobs.executor import _resolve_table_parts

        proj, ds, tbl = _resolve_table_parts("proj.ds.tbl", "default-proj")
        assert (proj, ds, tbl) == ("proj", "ds", "tbl")

    def test_single_part_raises(self) -> None:
        from bqemulator.domain.errors import InvalidQueryError
        from bqemulator.jobs.executor import _resolve_table_parts

        with pytest.raises(InvalidQueryError):
            _resolve_table_parts("`tbl`", "default-proj")


class TestCopyJobValidation:
    """Edge cases of ``execute_copy_job`` that don't need a full integration setup."""

    @pytest.mark.asyncio
    async def test_restore_requires_both_endpoints(self) -> None:
        """``operationType=RESTORE`` rejects empty source or destination."""
        from unittest.mock import Mock

        from bqemulator.domain.errors import InvalidQueryError
        from bqemulator.jobs.executor import execute_copy_job

        # Build a minimal context shape; the validation happens before any
        # catalog or engine call so a Mock suffices.
        ctx = Mock()
        with pytest.raises(InvalidQueryError, match="RESTORE requires"):
            await execute_copy_job(
                "p",
                "j1",
                {
                    "copy": {
                        "operationType": "RESTORE",
                        "sourceTable": {"datasetId": "ds", "tableId": ""},
                        "destinationTable": {"datasetId": "ds", "tableId": "dst"},
                    },
                },
                ctx,
            )


class TestMaybeCreateLoadDestination:
    """``_maybe_create_load_destination`` is a no-op when no schema is supplied."""

    def test_no_schema_is_noop(self) -> None:
        from unittest.mock import Mock

        from bqemulator.jobs.executor import _maybe_create_load_destination

        ctx = Mock()
        # The helper should return without touching the catalog or engine
        # because ``load.schema.fields`` is empty (autodetect path).
        _maybe_create_load_destination(
            dest_project="p",
            dest_dataset="ds",
            dest_table_id="t",
            load_config={},
            now=None,
            ctx=ctx,
        )
        ctx.catalog.get_dataset.assert_not_called()
        ctx.catalog.create_table.assert_not_called()

    def test_missing_dataset_raises(self) -> None:
        """If the destination dataset doesn't exist, raise notFound."""
        from datetime import UTC, datetime
        from unittest.mock import Mock

        from bqemulator.domain.errors import NotFoundError
        from bqemulator.jobs.executor import _maybe_create_load_destination

        ctx = Mock()
        ctx.catalog.get_dataset.return_value = None
        with pytest.raises(NotFoundError):
            _maybe_create_load_destination(
                dest_project="p",
                dest_dataset="missing-ds",
                dest_table_id="t",
                load_config={
                    "schema": {
                        "fields": [{"name": "id", "type": "INTEGER"}],
                    },
                },
                now=datetime.now(UTC),
                ctx=ctx,
            )


class TestCopyTableIntoDestination:
    """``_copy_table_into_destination`` raises notFound on missing entities."""

    @pytest.mark.asyncio
    async def test_missing_source_table_raises(self) -> None:
        from unittest.mock import Mock

        from bqemulator.domain.errors import NotFoundError
        from bqemulator.jobs.executor import _copy_table_into_destination

        ctx = Mock()
        ctx.catalog.get_table.return_value = None
        with pytest.raises(NotFoundError):
            await _copy_table_into_destination(
                src_proj="p",
                src_ds="ds",
                src_table_id="missing",
                dst_proj="p",
                dst_ds="ds",
                dst_table_id="t",
                write_disposition="WRITE_APPEND",
                create_if_needed=True,
                ctx=ctx,
            )

    @pytest.mark.asyncio
    async def test_missing_destination_dataset_raises(self) -> None:
        from unittest.mock import Mock

        from bqemulator.domain.errors import NotFoundError
        from bqemulator.jobs.executor import _copy_table_into_destination

        ctx = Mock()
        # Source table exists; destination dataset does not.
        ctx.catalog.get_table.return_value = object()
        ctx.catalog.get_dataset.return_value = None
        with pytest.raises(NotFoundError):
            await _copy_table_into_destination(
                src_proj="p",
                src_ds="ds",
                src_table_id="src",
                dst_proj="p",
                dst_ds="missing-ds",
                dst_table_id="t",
                write_disposition="WRITE_APPEND",
                create_if_needed=True,
                ctx=ctx,
            )


class TestMaybeCreateLoadDestinationAutodetect:
    def test_infer_autodetect_schema_real_duckdb(
        self, ephemeral_settings: Settings, tmp_path: Path
    ) -> None:
        import asyncio
        from unittest.mock import Mock

        from bqemulator.jobs.executor import _infer_autodetect_schema
        from bqemulator.storage.engine import DuckDBEngine

        csv_file = tmp_path / "data.csv"
        csv_file.write_text("id,name\n1,alice\n2,bob\n")

        engine = DuckDBEngine(ephemeral_settings)
        asyncio.run(engine.start())

        try:
            ctx = Mock()
            ctx.engine = engine
            ctx.settings = ephemeral_settings

            schema = _infer_autodetect_schema(
                ctx=ctx,
                target_ref="tbl",
                source_uris=[f"file://{csv_file}"],
                fmt="CSV",
            )

            assert len(schema) == 2
            assert schema[0].name == "id"
            assert schema[0].type == "INT64"
            assert schema[1].name == "name"
            assert schema[1].type == "STRING"

            json_file = tmp_path / "data.json"
            json_file.write_text('{"id": 1, "score": 99.5}\n{"id": 2, "score": 88.2}\n')

            schema2 = _infer_autodetect_schema(
                ctx=ctx,
                target_ref="tbl2",
                source_uris=[f"file://{json_file}"],
                fmt="NEWLINE_DELIMITED_JSON",
            )

            assert len(schema2) == 2
            assert schema2[0].name == "id"
            assert schema2[0].type == "INT64"
            assert schema2[1].name == "score"
            assert schema2[1].type == "FLOAT64"

        finally:
            asyncio.run(engine.stop())

    def test_autodetect_json_calls_read_json_auto(self) -> None:
        from unittest.mock import Mock

        from bqemulator.jobs.executor import _maybe_create_load_destination

        ctx = Mock()
        ctx.catalog.get_dataset.return_value = Mock()

        # Mock the DESCRIBE response (column_name, column_type, null, key, default, extra)
        ctx.engine.execute.return_value.fetchall.return_value = [
            ("id", "BIGINT", "YES", "PRI", "NULL", ""),
            ("name", "VARCHAR", "YES", "", "NULL", ""),
        ]

        _maybe_create_load_destination(
            dest_project="p",
            dest_dataset="ds",
            dest_table_id="t",
            load_config={
                "autodetect": True,
                "sourceFormat": "NEWLINE_DELIMITED_JSON",
                "sourceUris": ["file:///tmp/test.json"],
            },
            now="2026-06-15T00:00:00Z",
            ctx=ctx,
        )

        execute_calls = ctx.engine.execute.call_args_list
        assert (
            execute_calls[0][0][0]
            == 'CREATE TABLE "p__ds"."t" AS SELECT * FROM read_json_auto(?) LIMIT 0'
        )
        assert execute_calls[1][0][0] == 'DESCRIBE "p__ds"."t"'

        added_meta = ctx.catalog.create_table.call_args[0][0]
        assert len(added_meta.schema_.fields) == 2
        assert added_meta.schema_.fields[0].name == "id"
        assert added_meta.schema_.fields[0].type == "INT64"

    def test_autodetect_csv_calls_read_csv_auto(self) -> None:
        from unittest.mock import Mock

        from bqemulator.jobs.executor import _maybe_create_load_destination

        ctx = Mock()
        ctx.catalog.get_dataset.return_value = Mock()
        ctx.engine.execute.return_value.fetchall.return_value = []

        _maybe_create_load_destination(
            dest_project="p",
            dest_dataset="ds",
            dest_table_id="t",
            load_config={
                "autodetect": True,
                "sourceFormat": "CSV",
                "sourceUris": ["file:///tmp/test.csv"],
            },
            now="2026-06-15T00:00:00Z",
            ctx=ctx,
        )

        execute_calls = ctx.engine.execute.call_args_list
        assert (
            execute_calls[0][0][0]
            == 'CREATE TABLE "p__ds"."t" AS SELECT * FROM read_csv_auto(?) LIMIT 0'
        )

    def test_autodetect_unsupported_format_returns(self) -> None:
        from unittest.mock import Mock

        from bqemulator.jobs.executor import _maybe_create_load_destination

        ctx = Mock()
        ctx.catalog.get_dataset.return_value = Mock()

        _maybe_create_load_destination(
            dest_project="p",
            dest_dataset="ds",
            dest_table_id="t",
            load_config={
                "autodetect": True,
                "sourceFormat": "AVRO",
                "sourceUris": ["file:///tmp/test.avro"],
            },
            now="2026-06-15T00:00:00Z",
            ctx=ctx,
        )

        ctx.engine.execute.assert_not_called()
