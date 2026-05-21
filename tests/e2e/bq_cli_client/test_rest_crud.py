"""E2E: Phase 1 REST CRUD + query via the bq CLI.

Mirrors :mod:`tests.e2e.python_client.test_rest_crud_rest` but drives
the lifecycle through ``bq mk`` / ``bq insert`` / ``bq query`` /
``bq rm`` subprocesses. The Python suite proves the REST surface
speaks the SDK protocol; this suite proves the same surface speaks
the ``bq`` CLI's protocol (different request shapes, different
JSON output formats, different error renderings).
"""

from __future__ import annotations

import pytest

from .bq_runner import BqRunner

pytestmark = pytest.mark.e2e


def _mk_dataset(bq_runner: BqRunner, ds_id: str) -> None:
    result = bq_runner.run("mk", "--dataset", "--location=US", ds_id)
    # ``mk`` is idempotent-friendly when ``-f`` is set; without ``-f``
    # it fails on already-exists. Tests pre-clean via teardown so
    # they always create a fresh dataset.
    assert result.succeeded(), result.stderr


def _rm_dataset(bq_runner: BqRunner, ds_id: str) -> None:
    bq_runner.run("rm", "-r", "-f", "-d", ds_id)


def test_dataset_table_insert_query(bq_runner: BqRunner) -> None:
    """Full dataset/table/insert/query loop via bq CLI subprocesses."""
    ds_id = "bq_cli_rest_crud_basic"
    table_fq = f"{ds_id}.customers"
    try:
        _mk_dataset(bq_runner, ds_id)

        # ``bq mk --table`` with an inline schema string.
        result = bq_runner.run(
            "mk",
            "--table",
            table_fq,
            "id:INTEGER,name:STRING,email:STRING",
        )
        assert result.succeeded(), result.stderr

        # ``bq insert`` reads NDJSON from stdin.
        rows = (
            b'{"id": 1, "name": "Alice", "email": "a@x.test"}\n'
            b'{"id": 2, "name": "Bob",   "email": "b@x.test"}\n'
            b'{"id": 3, "name": "Carol", "email": "c@x.test"}\n'
        )
        result = bq_runner.run("insert", table_fq, input_bytes=rows)
        assert result.succeeded(), result.stderr

        # ``bq query --format=json`` returns rows as a JSON list.
        out = bq_runner.query_json(f"SELECT COUNT(*) AS n FROM `{table_fq}`")
        assert out == [{"n": "3"}]

        # Verify row content.
        out = bq_runner.query_json(
            f"SELECT id, name FROM `{table_fq}` WHERE id = 2",
        )
        assert out == [{"id": "2", "name": "Bob"}]
    finally:
        _rm_dataset(bq_runner, ds_id)


def test_show_dataset_and_table(bq_runner: BqRunner) -> None:
    """``bq show --format=json`` renders dataset + table metadata."""
    ds_id = "bq_cli_rest_crud_show"
    table_fq = f"{ds_id}.t"
    try:
        _mk_dataset(bq_runner, ds_id)
        bq_runner.run("mk", "--table", table_fq, "id:INTEGER")

        ds_meta = bq_runner.run("show", "--format=json", "-d", ds_id)
        assert ds_meta.succeeded(), ds_meta.stderr
        parsed = ds_meta.json()
        assert isinstance(parsed, dict)
        assert parsed["datasetReference"]["datasetId"] == ds_id

        tbl_meta = bq_runner.run("show", "--format=json", table_fq)
        assert tbl_meta.succeeded(), tbl_meta.stderr
        parsed = tbl_meta.json()
        assert isinstance(parsed, dict)
        assert parsed["tableReference"]["tableId"] == "t"
        # Schema field shape matches BQ's standard table representation.
        fields = parsed["schema"]["fields"]
        assert [f["name"] for f in fields] == ["id"]
    finally:
        _rm_dataset(bq_runner, ds_id)


def test_list_datasets_and_tables(bq_runner: BqRunner) -> None:
    """``bq ls`` enumerates the project's datasets + a dataset's tables."""
    ds_id = "bq_cli_rest_crud_ls"
    table_fq = f"{ds_id}.t"
    try:
        _mk_dataset(bq_runner, ds_id)
        bq_runner.run("mk", "--table", table_fq, "id:INTEGER")

        # Project-level listing surfaces the dataset.
        result = bq_runner.run("ls", "--format=json")
        assert result.succeeded(), result.stderr
        rows = result.json()
        assert isinstance(rows, list)
        seen = {r["datasetReference"]["datasetId"] for r in rows}
        assert ds_id in seen

        # Dataset-level listing surfaces the table.
        result = bq_runner.run("ls", "--format=json", ds_id)
        assert result.succeeded(), result.stderr
        rows = result.json()
        assert isinstance(rows, list)
        seen = {r["tableReference"]["tableId"] for r in rows}
        assert seen == {"t"}
    finally:
        _rm_dataset(bq_runner, ds_id)


def test_parameterised_query(bq_runner: BqRunner) -> None:
    """``bq query --parameter`` binds a typed scalar parameter."""
    ds_id = "bq_cli_rest_crud_param"
    table_fq = f"{ds_id}.numbers"
    try:
        _mk_dataset(bq_runner, ds_id)
        bq_runner.run("mk", "--table", table_fq, "id:INTEGER,name:STRING")
        bq_runner.run(
            "insert",
            table_fq,
            input_bytes=(
                b'{"id": 1, "name": "one"}\n{"id": 2, "name": "two"}\n{"id": 3, "name": "three"}\n'
            ),
        )

        result = bq_runner.run(
            "query",
            "--use_legacy_sql=false",
            "--format=json",
            "--parameter=id:INT64:2",
            f"SELECT name FROM `{table_fq}` WHERE id = @id",
        )
        assert result.succeeded(), result.stderr
        rows = result.json()
        assert rows == [{"name": "two"}]
    finally:
        _rm_dataset(bq_runner, ds_id)
