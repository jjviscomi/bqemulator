"""Unit + in-process tests for the ``EXPORT DATA`` statement (RFC 0001 / ADR 0043).

Covers the parser/option-validation surface (pure functions) and the
end-to-end write path against a real DuckDB engine (no mocking, per the
project's testing contract): every export format, CSV header/delimiter,
per-format compression, ``overwrite`` semantics, single-file vs. wildcard
sharding, empty results, scripted ``EXPORT DATA``, the error paths, and a
regression check that the refactored extract job still writes files.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from sqlglot import exp

from bqemulator.api.dependencies import AppContext
from bqemulator.catalog.memory_repository import MemoryCatalogRepository
from bqemulator.catalog.models import DatasetMeta
from bqemulator.config import PersistenceMode, Settings
from bqemulator.domain.clock import FrozenClock
from bqemulator.domain.errors import InvalidQueryError, UnsupportedFeatureError
from bqemulator.domain.events import EventBus
from bqemulator.jobs.executor import (
    JOB_RESULTS,
    _build_copy_clause,
    _copy_relation_to_file,
    _extract_export_options,
    _normalize_compression,
    _normalize_export_format,
    _opt_literal_bool,
    _opt_literal_str,
    _parse_export_properties,
    _resolve_export_compression,
    _resolve_field_delimiter,
    _validate_export_option_scope,
    classify_statement_type,
    execute_extract_job,
    execute_query_job,
    parse_export_data,
)
from bqemulator.observability.metrics import MetricsRegistry
from bqemulator.row_access.policy import RowAccessPolicyManager
from bqemulator.storage.engine import DuckDBEngine
from bqemulator.udf.runtime import UDFRegistry
from bqemulator.versioning.snapshots import SnapshotManager

pytestmark = pytest.mark.unit

NOW = datetime(2026, 5, 16, tzinfo=UTC)


def _settings(gcs_root: Path | None, **overrides: object) -> Settings:
    """Ephemeral settings rooted at ``gcs_root`` for ``gs://`` resolution."""
    base: dict[str, object] = {
        "persistence_mode": PersistenceMode.EPHEMERAL,
        "rest_port": 0,
        "grpc_port": 0,
        "gcs_local_root": gcs_root,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@asynccontextmanager
async def _export_ctx(settings: Settings) -> AsyncIterator[AppContext]:
    """Yield an in-process ``AppContext`` with dataset ``p.ds`` registered."""
    engine = DuckDBEngine(settings)
    await engine.start()
    try:
        catalog = MemoryCatalogRepository()
        catalog.create_dataset(
            DatasetMeta(
                project_id="p",
                dataset_id="ds",
                creation_time=NOW,
                last_modified_time=NOW,
                etag="e",
            ),
        )
        engine.execute('CREATE SCHEMA IF NOT EXISTS "p__ds"')
        yield AppContext(
            settings=settings,
            clock=FrozenClock(NOW),
            engine=engine,
            catalog=catalog,
            metrics=MetricsRegistry(),
            events=EventBus(),
            udf_registry=UDFRegistry(settings),
            snapshots=SnapshotManager(
                engine=engine,
                catalog=MemoryCatalogRepository(),
                clock=FrozenClock(NOW),
                events=EventBus(),
                retention_days=7,
            ),
            row_access=RowAccessPolicyManager(catalog=catalog, clock=FrozenClock(NOW)),
        )
    finally:
        await engine.stop()


def _resolved(ctx: AppContext, *parts: str) -> Path:
    """Build the local path a ``gs://`` URI resolves to under the shim root."""
    root = ctx.settings.gcs_local_root
    assert root is not None
    return Path(root, *parts)


class TestClassifyAndParse:
    """Statement classification + OPTIONS parsing/validation (pure)."""

    @pytest.mark.parametrize(
        "sql",
        [
            "EXPORT DATA OPTIONS(uri='gs://b/o/*.csv', format='CSV') AS SELECT 1 AS a",
            "export data options(uri='gs://b/o.parquet', format='PARQUET') as select 1 as a",
            "EXPORT DATA OPTIONS(uri='gs://b/o/*.avro', format='AVRO') AS SELECT 1 AS a",
        ],
    )
    def test_classify_export_data(self, sql: str) -> None:
        """Every EXPORT DATA spelling classifies as ``EXPORT_DATA``."""
        assert classify_statement_type(sql) == "EXPORT_DATA"

    def test_non_export_returns_none(self) -> None:
        """A non-EXPORT statement is not parsed as an export request."""
        assert parse_export_data("SELECT 1 AS a") is None

    def test_inner_select_and_options_extracted(self) -> None:
        """The inner SELECT text and OPTIONS round-trip out of the AST."""
        req = parse_export_data(
            "EXPORT DATA OPTIONS(uri='gs://b/o/*.parquet', format='PARQUET', "
            "compression='SNAPPY', overwrite=true) AS SELECT 1 AS a, 'x' AS b",
        )
        assert req is not None
        assert req.select_sql == "SELECT 1 AS a, 'x' AS b"
        assert req.options.uri == "gs://b/o/*.parquet"
        assert req.options.format == "PARQUET"
        assert req.options.compression == "SNAPPY"
        assert req.options.overwrite is True

    def test_json_alias_normalized(self) -> None:
        """``format='JSON'`` normalizes to the canonical NEWLINE_DELIMITED_JSON."""
        req = parse_export_data(
            "EXPORT DATA OPTIONS(uri='gs://b/o.json', format='JSON') AS SELECT 1 AS a",
        )
        assert req is not None
        assert req.options.format == "NEWLINE_DELIMITED_JSON"

    def test_field_delimiter_tab_alias(self) -> None:
        """``field_delimiter='tab'`` resolves to a literal tab character."""
        req = parse_export_data(
            "EXPORT DATA OPTIONS(uri='gs://b/o.csv', format='CSV', field_delimiter='tab') "
            "AS SELECT 1 AS a",
        )
        assert req is not None
        assert req.options.field_delimiter == "\t"

    def test_default_format_is_csv(self) -> None:
        """Omitting ``format`` defaults to CSV with a header."""
        req = parse_export_data("EXPORT DATA OPTIONS(uri='gs://b/o.csv') AS SELECT 1 AS a")
        assert req is not None
        assert req.options.format == "CSV"
        assert req.options.header is True

    def test_with_connection_rejected(self) -> None:
        """External-sink exports (WITH CONNECTION) are out of scope."""
        with pytest.raises(UnsupportedFeatureError, match="WITH CONNECTION"):
            parse_export_data(
                "EXPORT DATA WITH CONNECTION `p.us.c` OPTIONS(uri='gs://b/*.csv') AS SELECT 1 AS a",
            )

    def test_orc_rejected(self) -> None:
        """ORC is rejected as an invalid ``format`` value, matching BigQuery.

        BigQuery does not export ORC, but it rejects ``format='ORC'`` the
        same way as any unrecognised value — ``invalidQuery`` / HTTP 400, not
        a 501 unsupported-feature error. The exact message + ``location`` are
        pinned by the ``export_orc_rejected`` conformance baseline.
        """
        with pytest.raises(InvalidQueryError, match="'ORC' is not a valid value"):
            parse_export_data(
                "EXPORT DATA OPTIONS(uri='gs://b/*.orc', format='ORC') AS SELECT 1 AS a",
            )

    @pytest.mark.parametrize(
        ("sql", "match"),
        [
            ("EXPORT DATA OPTIONS(format='CSV') AS SELECT 1 AS a", "uri"),
            (
                "EXPORT DATA OPTIONS(uri='gs://b/*.parquet', format='PARQUET', header=true) "
                "AS SELECT 1 AS a",
                "header",
            ),
            (
                "EXPORT DATA OPTIONS(uri='gs://b/*.csv', format='CSV', compression='ZSTD') "
                "AS SELECT 1 AS a",
                "compression",
            ),
            (
                "EXPORT DATA OPTIONS(uri='gs://b/*.csv', nope=1) AS SELECT 1 AS a",
                "Unknown EXPORT DATA option",
            ),
        ],
    )
    def test_invalid_options_rejected(self, sql: str, match: str) -> None:
        """Missing uri, format/option mismatch, bad compression, unknown option all error."""
        with pytest.raises(InvalidQueryError, match=match):
            parse_export_data(sql)


class TestExportEndToEnd:
    """Write path against a real DuckDB engine and the ``gs://`` shim."""

    async def test_csv_single_file_no_wildcard(self, tmp_path: Path) -> None:
        """A wildcard-free CSV URI writes one file with a header + rows."""
        async with _export_ctx(_settings(tmp_path)) as ctx:
            meta = await execute_query_job(
                "p",
                "j-csv",
                "EXPORT DATA OPTIONS(uri='gs://bkt/out/data.csv', format='CSV') "
                "AS SELECT 1 AS a, 'x' AS b",
                None,
                ctx,
            )
        query_stats = meta.statistics["query"]
        assert query_stats["statementType"] == "EXPORT_DATA"
        assert query_stats["totalRows"] == "0"
        # exportDataStatistics mirrors BigQuery's job resource (pinned by the
        # http_corpus/jobs/export_csv_query_job conformance baseline): the
        # written-file + exported-row counts as int64-strings, plus the
        # sibling totalPartitionsProcessed / transferredBytes fields.
        assert query_stats["exportDataStatistics"] == {"fileCount": "1", "rowCount": "1"}
        assert query_stats["totalPartitionsProcessed"] == "0"
        assert query_stats["transferredBytes"] == "0"
        assert JOB_RESULTS["j-csv"].num_rows == 0
        out = _resolved(ctx, "bkt", "out", "data.csv")
        assert out.exists()
        lines = out.read_text().strip().splitlines()
        assert lines[0] == "a,b"
        assert lines[1] == "1,x"

    async def test_csv_wildcard_single_shard_naming(self, tmp_path: Path) -> None:
        """A wildcard URI under the size limit yields one ``000000000000`` shard."""
        async with _export_ctx(_settings(tmp_path)) as ctx:
            await execute_query_job(
                "p",
                "j-wild",
                "EXPORT DATA OPTIONS(uri='gs://bkt/out/data_*.csv', format='CSV') AS SELECT 1 AS a",
                None,
                ctx,
            )
            shard = _resolved(ctx, "bkt", "out", "data_000000000000.csv")
            assert shard.exists()

    async def test_csv_header_false_and_custom_delimiter(self, tmp_path: Path) -> None:
        """``header=false`` and a custom ``field_delimiter`` are honoured."""
        async with _export_ctx(_settings(tmp_path)) as ctx:
            await execute_query_job(
                "p",
                "j-pipe",
                "EXPORT DATA OPTIONS(uri='gs://bkt/p.csv', format='CSV', header=false, "
                "field_delimiter='|') AS SELECT 1 AS a, 2 AS b",
                None,
                ctx,
            )
            text = _resolved(ctx, "bkt", "p.csv").read_text().strip()
        assert text == "1|2"

    async def test_json_export(self, tmp_path: Path) -> None:
        """NEWLINE_DELIMITED_JSON writes parseable JSON rows."""
        async with _export_ctx(_settings(tmp_path)) as ctx:
            await execute_query_job(
                "p",
                "j-json",
                "EXPORT DATA OPTIONS(uri='gs://bkt/o.json', format='NEWLINE_DELIMITED_JSON') "
                "AS SELECT 7 AS a, 'y' AS b",
                None,
                ctx,
            )
            text = _resolved(ctx, "bkt", "o.json").read_text().strip()
        row = json.loads(text.splitlines()[0])
        assert row == {"a": 7, "b": "y"}

    async def test_parquet_export_roundtrip(self, tmp_path: Path) -> None:
        """PARQUET output reads back with the exported rows."""
        async with _export_ctx(_settings(tmp_path)) as ctx:
            await execute_query_job(
                "p",
                "j-pq",
                "EXPORT DATA OPTIONS(uri='gs://bkt/o.parquet', format='PARQUET', "
                "compression='ZSTD') AS SELECT 5 AS a, 'z' AS b",
                None,
                ctx,
            )
            table = pq.read_table(_resolved(ctx, "bkt", "o.parquet"))
        assert table.to_pylist() == [{"a": 5, "b": "z"}]

    async def test_avro_export_or_skip(self, tmp_path: Path) -> None:
        """AVRO output reads back, or skips when the DuckDB extension is unavailable."""
        async with _export_ctx(_settings(tmp_path)) as ctx:
            try:
                await execute_query_job(
                    "p",
                    "j-avro",
                    "EXPORT DATA OPTIONS(uri='gs://bkt/o.avro', format='AVRO') AS SELECT 9 AS a",
                    None,
                    ctx,
                )
            except UnsupportedFeatureError:
                pytest.skip("DuckDB avro extension unavailable in this environment")
            path = _resolved(ctx, "bkt", "o.avro")
            assert path.exists()
            rows = ctx.engine.execute(f"SELECT a FROM read_avro('{path}')").fetchall()
        assert rows == [(9,)]

    async def test_empty_result_writes_one_file(self, tmp_path: Path) -> None:
        """An empty result still writes a single (header-only) shard."""
        async with _export_ctx(_settings(tmp_path)) as ctx:
            await execute_query_job(
                "p",
                "j-empty",
                "EXPORT DATA OPTIONS(uri='gs://bkt/empty_*.csv', format='CSV') "
                "AS SELECT 1 AS a LIMIT 0",
                None,
                ctx,
            )
            shard = _resolved(ctx, "bkt", "empty_000000000000.csv")
            assert shard.exists()
            assert shard.read_text().strip() == "a"

    async def test_overwrite_true_replaces_existing(self, tmp_path: Path) -> None:
        """``overwrite=true`` rewrites an existing destination."""
        async with _export_ctx(_settings(tmp_path)) as ctx:
            for value in (1, 2):
                await execute_query_job(
                    "p",
                    f"j-ow-{value}",
                    f"EXPORT DATA OPTIONS(uri='gs://bkt/ow.csv', format='CSV', overwrite=true) "
                    f"AS SELECT {value} AS a",
                    None,
                    ctx,
                )
            assert _resolved(ctx, "bkt", "ow.csv").read_text().strip().splitlines()[1] == "2"

    async def test_overwrite_false_existing_errors(self, tmp_path: Path) -> None:
        """``overwrite=false`` (default) refuses to clobber an existing file."""
        async with _export_ctx(_settings(tmp_path)) as ctx:
            sql = "EXPORT DATA OPTIONS(uri='gs://bkt/no.csv', format='CSV') AS SELECT 1 AS a"
            await execute_query_job("p", "j-ow-a", sql, None, ctx)
            with pytest.raises(InvalidQueryError, match="already exists"):
                await execute_query_job("p", "j-ow-b", sql, None, ctx)

    async def test_gs_uri_without_root_errors(self) -> None:
        """A ``gs://`` URI with no configured root is rejected."""
        async with _export_ctx(_settings(None)) as ctx:
            with pytest.raises(InvalidQueryError, match="BQEMU_GCS_LOCAL_ROOT"):
                await execute_query_job(
                    "p",
                    "j-noroot",
                    "EXPORT DATA OPTIONS(uri='gs://bkt/x.csv', format='CSV') AS SELECT 1 AS a",
                    None,
                    ctx,
                )

    async def test_too_many_wildcards_errors(self, tmp_path: Path) -> None:
        """At most one ``*`` wildcard is allowed in the URI."""
        async with _export_ctx(_settings(tmp_path)) as ctx:
            with pytest.raises(InvalidQueryError, match="one '\\*' wildcard"):
                await execute_query_job(
                    "p",
                    "j-2star",
                    "EXPORT DATA OPTIONS(uri='gs://bkt/*/d_*.csv', format='CSV') AS SELECT 1 AS a",
                    None,
                    ctx,
                )

    async def test_gs_uri_path_traversal_rejected(self, tmp_path: Path) -> None:
        """A gs:// uri using ``..`` to escape BQEMU_GCS_LOCAL_ROOT is rejected."""
        async with _export_ctx(_settings(tmp_path)) as ctx:
            with pytest.raises(InvalidQueryError, match="escapes the configured"):
                await execute_query_job(
                    "p",
                    "j-traversal",
                    "EXPORT DATA OPTIONS("
                    "uri='gs://bkt/../../../../etc/pwn.csv', format='CSV', overwrite=true) "
                    "AS SELECT 1 AS a",
                    None,
                    ctx,
                )

    async def test_non_gcs_uri_rejected(self, tmp_path: Path) -> None:
        """A non-gs:// export uri (file:// / bare path) is rejected before any write."""
        async with _export_ctx(_settings(tmp_path)) as ctx:
            with pytest.raises(InvalidQueryError, match="must be a Cloud Storage URI"):
                await execute_query_job(
                    "p",
                    "j-fileuri",
                    "EXPORT DATA OPTIONS(uri='file:///tmp/pwn.csv', format='CSV') AS SELECT 1 AS a",
                    None,
                    ctx,
                )


class TestSharding:
    """Real size-based multi-file sharding (low threshold) and its guards."""

    async def test_multi_file_sharding_preserves_order(self, tmp_path: Path) -> None:
        """A tiny threshold shards 100 ordered rows across files that recombine in order."""
        async with _export_ctx(_settings(tmp_path, export_shard_threshold_bytes=64)) as ctx:
            ctx.engine.execute('CREATE TABLE "p__ds"."nums" AS SELECT range AS n FROM range(100)')
            await execute_query_job(
                "p",
                "j-shard",
                "EXPORT DATA OPTIONS(uri='gs://bkt/n_*.parquet', format='PARQUET') "
                "AS SELECT n FROM `p.ds.nums` ORDER BY n",
                None,
                ctx,
            )
            shards = sorted(_resolved(ctx, "bkt").glob("n_*.parquet"))
            assert len(shards) > 1
            recombined: list[int] = []
            for shard in shards:
                recombined.extend(row["n"] for row in pq.read_table(shard).to_pylist())
        assert recombined == list(range(100))

    async def test_no_wildcard_oversize_errors(self, tmp_path: Path) -> None:
        """A wildcard-free URI whose output exceeds the threshold is rejected."""
        async with _export_ctx(_settings(tmp_path, export_shard_threshold_bytes=8)) as ctx:
            ctx.engine.execute('CREATE TABLE "p__ds"."big" AS SELECT range AS n FROM range(100)')
            with pytest.raises(InvalidQueryError, match="single-file size limit"):
                await execute_query_job(
                    "p",
                    "j-big",
                    "EXPORT DATA OPTIONS(uri='gs://bkt/big.parquet', format='PARQUET') "
                    "AS SELECT n FROM `p.ds.big`",
                    None,
                    ctx,
                )

    async def test_overwrite_false_preflights_all_shards(self, tmp_path: Path) -> None:
        """overwrite=false errors before writing any file when a later shard exists."""
        async with _export_ctx(_settings(tmp_path, export_shard_threshold_bytes=64)) as ctx:
            ctx.engine.execute('CREATE TABLE "p__ds"."nums" AS SELECT range AS n FROM range(100)')
            # Pre-create the second shard so the preflight collides on it.
            _resolved(ctx, "bkt").mkdir(parents=True, exist_ok=True)
            _resolved(ctx, "bkt", "n_000000000001.parquet").write_bytes(b"stale")
            with pytest.raises(InvalidQueryError, match="already exists"):
                await execute_query_job(
                    "p",
                    "j-preflight",
                    "EXPORT DATA OPTIONS(uri='gs://bkt/n_*.parquet', format='PARQUET') "
                    "AS SELECT n FROM `p.ds.nums` ORDER BY n",
                    None,
                    ctx,
                )
            # No partial export: the first shard must not have been written.
            assert not _resolved(ctx, "bkt", "n_000000000000.parquet").exists()


class TestScriptedExport:
    """``EXPORT DATA`` inside a multi-statement script (the interpreter path)."""

    async def test_export_inside_script(self, tmp_path: Path) -> None:
        """A scripted EXPORT DATA writes its file via the shared helper."""
        async with _export_ctx(_settings(tmp_path)) as ctx:
            await execute_query_job(
                "p",
                "j-script",
                "BEGIN\n"
                "  EXPORT DATA OPTIONS(uri='gs://bkt/s.csv', format='CSV') AS SELECT 42 AS a;\n"
                "END",
                None,
                ctx,
            )
            out = _resolved(ctx, "bkt", "s.csv")
            assert out.exists()
            assert out.read_text().strip().splitlines()[1] == "42"


class TestExtractRegression:
    """The refactored extract job still writes files (shared writer)."""

    @pytest.mark.parametrize("fmt", ["CSV", "PARQUET", "NEWLINE_DELIMITED_JSON"])
    async def test_extract_job_still_writes(self, tmp_path: Path, fmt: str) -> None:
        """``execute_extract_job`` writes each format after the writer refactor."""
        async with _export_ctx(_settings(tmp_path)) as ctx:
            ctx.engine.execute('CREATE TABLE "p__ds"."src" AS SELECT 1 AS a, 2 AS b')
            # The extract job (unlike EXPORT DATA) does not create parent
            # directories — the e2e GCS-root fixture pre-stages bucket dirs.
            _resolved(ctx, "bkt").mkdir(parents=True, exist_ok=True)
            ext = {"CSV": "csv", "PARQUET": "parquet", "NEWLINE_DELIMITED_JSON": "json"}[fmt]
            await execute_extract_job(
                "p",
                "j-ext",
                {
                    "extract": {
                        "sourceTable": {"projectId": "p", "datasetId": "ds", "tableId": "src"},
                        "destinationUris": [f"gs://bkt/ext.{ext}"],
                        "destinationFormat": fmt,
                    },
                },
                ctx,
            )
            assert _resolved(ctx, "bkt", f"ext.{ext}").exists()

    async def test_extract_unknown_format_errors(self, tmp_path: Path) -> None:
        """An unknown extract format still raises (preserved behaviour)."""
        async with _export_ctx(_settings(tmp_path)) as ctx:
            ctx.engine.execute('CREATE TABLE "p__ds"."src2" AS SELECT 1 AS a')
            with pytest.raises(InvalidQueryError, match="Unknown destination format"):
                await execute_extract_job(
                    "p",
                    "j-bad",
                    {
                        "extract": {
                            "sourceTable": {"projectId": "p", "datasetId": "ds", "tableId": "src2"},
                            "destinationUris": ["gs://bkt/x.bin"],
                            "destinationFormat": "BINARY",
                        },
                    },
                    ctx,
                )


class _FakeMissingAvroEngine:
    """Engine stub whose COPY raises the DuckDB 'extension missing' error."""

    def execute(self, sql: str) -> None:  # noqa: ARG002 — signature parity
        raise RuntimeError(
            'Copy Function with name "avro" is not in the catalog, '
            "but it exists in the avro extension",
        )


class _FakeCtx:
    """Minimal ctx exposing only the engine ``_copy_relation_to_file`` needs."""

    engine = _FakeMissingAvroEngine()


class _FakeGenericErrorEngine:
    """Engine stub whose COPY fails for a reason unrelated to the avro extension."""

    def execute(self, sql: str) -> None:  # noqa: ARG002 — signature parity
        raise RuntimeError("disk full")


class _FakeGenericCtx:
    """ctx whose engine raises a non-extension error."""

    engine = _FakeGenericErrorEngine()


class TestHelperBranches:
    """Direct coverage of the validation / clause-building helper branches."""

    def test_copy_clause_compression_per_format(self) -> None:
        """CSV and JSON clauses include the COMPRESSION token when set."""
        csv = _build_copy_clause("CSV", header=True, field_delimiter=None, compression="GZIP")
        assert "COMPRESSION gzip" in csv
        js = _build_copy_clause(
            "NEWLINE_DELIMITED_JSON",
            header=True,
            field_delimiter=None,
            compression="GZIP",
        )
        assert js == "FORMAT JSON, COMPRESSION gzip"

    def test_copy_clause_unknown_format_raises(self) -> None:
        """An unrecognised format in the clause builder is rejected."""
        with pytest.raises(InvalidQueryError, match="Unknown destination format"):
            _build_copy_clause("BOGUS", header=True, field_delimiter=None, compression=None)

    def test_normalize_compression_variants(self) -> None:
        """A codec lower-cases for DuckDB; ``NONE`` and unset both collapse to ``None``."""
        assert _normalize_compression("GZIP") == "gzip"
        assert _normalize_compression("none") is None
        assert _normalize_compression(None) is None

    def test_validate_option_scope_csv_only_on_non_csv(self) -> None:
        """A CSV-only option supplied for a non-CSV format is rejected."""
        with pytest.raises(InvalidQueryError, match="only valid for FORMAT CSV"):
            _validate_export_option_scope({"field_delimiter": object()}, "PARQUET")

    def test_resolve_export_compression_paths(self) -> None:
        """Absent compression is ``None``; a codec outside the format allow-list errors."""
        assert _resolve_export_compression({}, "CSV") is None
        with pytest.raises(InvalidQueryError, match="is not valid for FORMAT CSV"):
            _resolve_export_compression(
                {"compression": exp.Literal.string("SNAPPY")},
                "CSV",
            )

    def test_parse_export_properties_unknown_option(self) -> None:
        """An unrecognised OPTIONS key is rejected by the property splitter."""
        props = exp.Properties(
            expressions=[
                exp.Property(this=exp.Var(this="bogus"), value=exp.Literal.string("x")),
            ],
        )
        with pytest.raises(InvalidQueryError, match="Unknown EXPORT DATA option: bogus"):
            _parse_export_properties(props)

    def test_copy_relation_avro_extension_missing(self) -> None:
        """A missing ``avro`` extension surfaces as UnsupportedFeatureError."""
        with pytest.raises(UnsupportedFeatureError, match="avro"):
            _copy_relation_to_file("SELECT 1", "/tmp/x.avro", "AVRO", _FakeCtx())  # type: ignore[arg-type]

    def test_copy_relation_avro_other_error_propagates(self) -> None:
        """A non-extension AVRO write failure bubbles up unchanged."""
        with pytest.raises(RuntimeError, match="disk full"):
            _copy_relation_to_file("SELECT 1", "/tmp/x.avro", "AVRO", _FakeGenericCtx())  # type: ignore[arg-type]

    def test_opt_literal_str_rejects_non_string(self) -> None:
        """A non-string option node is rejected rather than silently coerced."""
        with pytest.raises(InvalidQueryError):
            _opt_literal_str(exp.Var(this="abc"))
        with pytest.raises(InvalidQueryError):
            _opt_literal_str(exp.Literal(this="1", is_string=False))

    def test_opt_literal_bool_variants(self) -> None:
        """Boolean literals coerce; numeric / NULL option values are rejected."""
        assert _opt_literal_bool(exp.Boolean(this=True)) is True
        assert _opt_literal_bool(exp.Literal(this="true", is_string=True)) is True
        assert _opt_literal_bool(exp.Literal(this="false", is_string=True)) is False
        with pytest.raises(InvalidQueryError):
            _opt_literal_bool(exp.Null())
        with pytest.raises(InvalidQueryError):
            _opt_literal_bool(exp.Literal(this="1", is_string=False))

    def test_non_string_uri_rejected(self) -> None:
        """A numeric uri value is rejected, not coerced to the string '1'."""
        with pytest.raises(InvalidQueryError):
            parse_export_data("EXPORT DATA OPTIONS(uri=1) AS SELECT 1 AS a")

    def test_non_bool_overwrite_rejected(self) -> None:
        """overwrite=NULL is rejected, not silently treated as truthy."""
        with pytest.raises(InvalidQueryError):
            parse_export_data(
                "EXPORT DATA OPTIONS(uri='gs://b/o.csv', overwrite=NULL) AS SELECT 1 AS a",
            )

    @pytest.mark.parametrize("bad", ["ab", "'", '"', "\\"])
    def test_field_delimiter_invalid(self, bad: str) -> None:
        """Multi-char and quote/escape delimiters are rejected."""
        with pytest.raises(InvalidQueryError):
            _resolve_field_delimiter(bad)

    def test_normalize_format_unknown_raises(self) -> None:
        """Any unrecognised ``format`` value errors with BigQuery's wording + location.

        ORC and every other non-export value take this same path — an invalid
        ``format`` OPTIONS value with ``location='query'`` (pinned by the
        ``export_orc_rejected`` conformance baseline).
        """
        with pytest.raises(InvalidQueryError, match="'XML' is not a valid value") as exc_info:
            _normalize_export_format("XML")
        assert exc_info.value.location == "query"

    def test_extract_options_requires_properties(self) -> None:
        """A missing OPTIONS list is rejected."""
        with pytest.raises(InvalidQueryError, match="OPTIONS"):
            _extract_export_options(None)

    def test_format_as_generic_property(self) -> None:
        """``format`` supplied as a generic Property (not FileFormatProperty) is read."""
        props = exp.Properties(
            expressions=[
                exp.Property(this=exp.Var(this="format"), value=exp.Literal.string("CSV")),
                exp.Property(this=exp.Var(this="uri"), value=exp.Literal.string("gs://b/o.csv")),
            ],
        )
        options = _extract_export_options(props)
        assert options.format == "CSV"
        assert options.uri == "gs://b/o.csv"

    def test_empty_uri_rejected(self) -> None:
        """An empty ``uri`` string is rejected with BigQuery's wording.

        BigQuery uses one message for both the missing and the empty case —
        "Option 'uri' is missing or empty." (pinned by the
        ``export_missing_uri`` conformance baseline).
        """
        with pytest.raises(InvalidQueryError, match="missing or empty"):
            parse_export_data("EXPORT DATA OPTIONS(uri='') AS SELECT 1 AS a")

    def test_use_avro_logical_types_on_non_avro_rejected(self) -> None:
        """``use_avro_logical_types`` outside AVRO is rejected."""
        with pytest.raises(InvalidQueryError, match="use_avro_logical_types"):
            parse_export_data(
                "EXPORT DATA OPTIONS(uri='gs://b/o.csv', use_avro_logical_types=true) "
                "AS SELECT 1 AS a",
            )

    def test_unparseable_export_returns_none(self) -> None:
        """A statement that matches the gate but fails to parse is not an export."""
        assert parse_export_data("EXPORT DATA OPTIONS(uri=") is None

    def test_export_without_query_is_handled(self) -> None:
        """EXPORT DATA with no ``AS query`` either errors cleanly or is not an export."""
        try:
            result = parse_export_data("EXPORT DATA OPTIONS(uri='gs://b/o.csv')")
        except InvalidQueryError:
            return
        assert result is None

    async def test_csv_compression_roundtrips(self, tmp_path: Path) -> None:
        """A GZIP-compressed CSV export writes a non-empty gzip file."""
        async with _export_ctx(_settings(tmp_path)) as ctx:
            await execute_query_job(
                "p",
                "j-gz",
                "EXPORT DATA OPTIONS(uri='gs://bkt/c.csv', format='CSV', compression='GZIP') "
                "AS SELECT 1 AS a",
                None,
                ctx,
            )
            out = _resolved(ctx, "bkt", "c.csv")
            assert out.exists()
            assert out.read_bytes()[:2] == b"\x1f\x8b"  # gzip magic
