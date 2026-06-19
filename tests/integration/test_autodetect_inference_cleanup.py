"""Autodetect schema inference must not leave an orphaned probe table behind.

``_infer_autodetect_schema`` creates the destination table from a zero-row
DuckDB sample, then maps the inferred columns to BigQuery fields. When DuckDB
infers a shape BigQuery cannot represent (an array of arrays), the mapping
raises *after* the table exists. These tests use a real DuckDB engine (the
project never mocks DuckDB for behaviour like this) to pin that the failure
drops the orphan, so a retry reproduces the clean ``ValidationError`` instead
of a DuckDB "table already exists" while the catalog still reports it missing.
"""

import asyncio
from types import SimpleNamespace

import pytest

from bqemulator.config import Settings
from bqemulator.domain.errors import ValidationError
from bqemulator.jobs.executor import _infer_autodetect_schema
from bqemulator.storage.engine import DuckDBEngine
from bqemulator.storage.sql_identifiers import quoted_table_ref

pytestmark = pytest.mark.integration

_FMT = "NEWLINE_DELIMITED_JSON"
_ARRAY_OF_ARRAY = '{"matrix": [[1, 2], [3, 4]]}\n{"matrix": [[5, 6], [7, 8]]}\n'
_FLAT = '{"id": 1, "name": "a"}\n{"id": 2, "name": "b"}\n'


def _table_exists(engine: DuckDBEngine, schema: str, table: str) -> bool:
    rows = engine.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
        [schema, table],
    ).fetchall()
    return rows[0][0] > 0


def _started_engine() -> DuckDBEngine:
    engine = DuckDBEngine(Settings(enable_format_extensions=False))
    asyncio.run(engine.start())
    engine.execute('CREATE SCHEMA IF NOT EXISTS "p__ds"')
    return engine


def test_inference_failure_drops_probe_table_and_retry_is_clean(tmp_path) -> None:
    """An unmappable inferred shape drops the orphan and a retry stays clean."""
    src = tmp_path / "aoa.json"
    src.write_text(_ARRAY_OF_ARRAY)
    uri = f"file://{src}"

    engine = _started_engine()
    try:
        ctx = SimpleNamespace(engine=engine)
        target_ref = quoted_table_ref("p", "ds", "t")

        with pytest.raises(ValidationError):
            _infer_autodetect_schema(ctx, target_ref, [uri], _FMT)

        assert not _table_exists(engine, "p__ds", "t"), (
            "probe table was left behind after inference failure"
        )

        # The retry must reproduce the clean ValidationError, not a DuckDB
        # CatalogException ("table already exists") from a leftover table.
        with pytest.raises(ValidationError):
            _infer_autodetect_schema(ctx, target_ref, [uri], _FMT)
    finally:
        asyncio.run(engine.stop())


def test_inference_success_still_creates_probe_table(tmp_path) -> None:
    """The happy path is unchanged: fields are returned and the table exists."""
    src = tmp_path / "flat.json"
    src.write_text(_FLAT)
    uri = f"file://{src}"

    engine = _started_engine()
    try:
        ctx = SimpleNamespace(engine=engine)
        target_ref = quoted_table_ref("p", "ds", "t")

        fields = _infer_autodetect_schema(ctx, target_ref, [uri], _FMT)

        assert [f.name for f in fields] == ["id", "name"]
        assert _table_exists(engine, "p__ds", "t"), (
            "probe table should exist after a successful inference"
        )
    finally:
        asyncio.run(engine.stop())
