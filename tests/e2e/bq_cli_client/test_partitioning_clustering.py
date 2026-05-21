"""E2E: Phase 3 partitioning + wildcard tables via bq CLI.

``bq mk --time_partitioning_field`` is the canonical CLI path for
creating partitioned tables; wildcard queries (``table_*``) are
SQL-level and route through ``bq query``.
"""

from __future__ import annotations

import pytest

from .bq_runner import BqRunner

pytestmark = pytest.mark.e2e


def _mk_dataset(bq_runner: BqRunner, ds_id: str) -> None:
    assert bq_runner.run("mk", "--dataset", "--location=US", ds_id).succeeded()


def _rm_dataset(bq_runner: BqRunner, ds_id: str) -> None:
    bq_runner.run("rm", "-r", "-f", "-d", ds_id)


def test_time_partitioned_table_round_trip(bq_runner: BqRunner) -> None:
    """``bq mk --time_partitioning_type=DAY --time_partitioning_field=ts`` round-trips."""
    ds_id = "bq_cli_partitioning_clustering_tp"
    table_fq = f"{ds_id}.events"
    try:
        _mk_dataset(bq_runner, ds_id)
        result = bq_runner.run(
            "mk",
            "--table",
            "--time_partitioning_type=DAY",
            "--time_partitioning_field=ts",
            table_fq,
            "id:INTEGER,ts:TIMESTAMP",
        )
        assert result.succeeded(), result.stderr

        # ``bq show`` confirms the partitioning metadata round-tripped.
        meta = bq_runner.run("show", "--format=json", table_fq)
        assert meta.succeeded(), meta.stderr
        parsed = meta.json()
        assert isinstance(parsed, dict)
        tp = parsed["timePartitioning"]
        assert tp["type"] == "DAY"
        assert tp["field"] == "ts"
    finally:
        _rm_dataset(bq_runner, ds_id)


def test_partition_pruning_via_query(bq_runner: BqRunner) -> None:
    """A range predicate on the partitioning column returns the matching rows."""
    ds_id = "bq_cli_partitioning_clustering_prune"
    table_fq = f"{ds_id}.daily"
    try:
        _mk_dataset(bq_runner, ds_id)
        bq_runner.run(
            "mk",
            "--table",
            "--time_partitioning_type=DAY",
            "--time_partitioning_field=ts",
            table_fq,
            "id:INTEGER,ts:TIMESTAMP",
        )
        bq_runner.run(
            "insert",
            table_fq,
            input_bytes=(
                b'{"id": 1, "ts": "2024-01-15 00:00:00 UTC"}\n'
                b'{"id": 2, "ts": "2024-01-16 00:00:00 UTC"}\n'
                b'{"id": 3, "ts": "2024-02-01 00:00:00 UTC"}\n'
            ),
        )
        out = bq_runner.query_json(
            f"SELECT id FROM `{table_fq}` "
            "WHERE DATE(ts) BETWEEN DATE '2024-01-15' AND DATE '2024-01-16' "
            "ORDER BY id",
        )
        assert out == [{"id": "1"}, {"id": "2"}]
    finally:
        _rm_dataset(bq_runner, ds_id)


def test_wildcard_query_with_table_suffix(bq_runner: BqRunner) -> None:
    """``SELECT FROM dataset.events_*`` with ``_TABLE_SUFFIX`` filter."""
    ds_id = "bq_cli_partitioning_clustering_wildcard"
    try:
        _mk_dataset(bq_runner, ds_id)
        for suffix, payload in (
            ("20240101", b'{"id": 1}\n'),
            ("20240102", b'{"id": 2}\n'),
            ("20240201", b'{"id": 3}\n'),
        ):
            tbl = f"{ds_id}.events_{suffix}"
            bq_runner.run("mk", "--table", tbl, "id:INTEGER")
            bq_runner.run("insert", tbl, input_bytes=payload)

        out = bq_runner.query_json(
            f"SELECT id FROM `{ds_id}.events_*` "
            "WHERE _TABLE_SUFFIX BETWEEN '20240101' AND '20240131' "
            "ORDER BY id",
        )
        assert out == [{"id": "1"}, {"id": "2"}]
    finally:
        _rm_dataset(bq_runner, ds_id)


def test_clustering_metadata_round_trip(bq_runner: BqRunner) -> None:
    """``bq mk --clustering_fields`` records the cluster columns in table metadata."""
    ds_id = "bq_cli_partitioning_clustering_cluster"
    table_fq = f"{ds_id}.t"
    try:
        _mk_dataset(bq_runner, ds_id)
        result = bq_runner.run(
            "mk",
            "--table",
            "--time_partitioning_type=DAY",
            "--time_partitioning_field=ts",
            "--clustering_fields=region,id",
            table_fq,
            "id:INTEGER,region:STRING,ts:TIMESTAMP",
        )
        assert result.succeeded(), result.stderr

        meta = bq_runner.run("show", "--format=json", table_fq)
        parsed = meta.json()
        assert isinstance(parsed, dict)
        assert parsed["clustering"]["fields"] == ["region", "id"]
    finally:
        _rm_dataset(bq_runner, ds_id)
