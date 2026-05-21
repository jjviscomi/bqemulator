"""E2E: G4 INFORMATION_SCHEMA virtual views via bq CLI.

Mirrors :mod:`tests.e2e.python_client.test_g4_information_schema`.
INFORMATION_SCHEMA is a SQL-level surface so the bq path is just
``bq query`` against the same expander.
"""

from __future__ import annotations

import pytest

from .bq_runner import BqRunner

pytestmark = pytest.mark.e2e


def _mk_dataset(bq_runner: BqRunner, ds_id: str) -> None:
    assert bq_runner.run("mk", "--dataset", "--location=US", ds_id).succeeded()


def _rm_dataset(bq_runner: BqRunner, ds_id: str) -> None:
    bq_runner.run("rm", "-r", "-f", "-d", ds_id)


def test_information_schema_tables_lists_base_tables(
    bq_runner: BqRunner,
) -> None:
    """``INFORMATION_SCHEMA.TABLES`` lists base tables sorted by name."""
    ds_id = "bq_cli_g4_tables"
    try:
        _mk_dataset(bq_runner, ds_id)
        for tbl in ("orders", "customers"):
            bq_runner.run("mk", "--table", f"{ds_id}.{tbl}", "id:INTEGER")

        out = bq_runner.query_json(
            f"SELECT table_name FROM `{ds_id}`.INFORMATION_SCHEMA.TABLES "
            "WHERE table_type = 'BASE TABLE' ORDER BY table_name",
        )
        assert out == [{"table_name": "customers"}, {"table_name": "orders"}]
    finally:
        _rm_dataset(bq_runner, ds_id)


def test_information_schema_columns_ordered_by_ordinal_position(
    bq_runner: BqRunner,
) -> None:
    """``INFORMATION_SCHEMA.COLUMNS`` returns columns in declared order."""
    ds_id = "bq_cli_g4_columns"
    try:
        _mk_dataset(bq_runner, ds_id)
        bq_runner.run(
            "mk",
            "--table",
            f"{ds_id}.events",
            "id:INTEGER,ts:TIMESTAMP,payload:STRING",
        )

        out = bq_runner.query_json(
            f"SELECT column_name, data_type "
            f"FROM `{ds_id}`.INFORMATION_SCHEMA.COLUMNS "
            f"WHERE table_name = 'events' ORDER BY ordinal_position",
        )
        names = [r["column_name"] for r in out]
        assert names == ["id", "ts", "payload"]
        # data_type renders as the BQ standard name in bq's JSON output.
        types = [r["data_type"] for r in out]
        assert types == ["INT64", "TIMESTAMP", "STRING"]
    finally:
        _rm_dataset(bq_runner, ds_id)
