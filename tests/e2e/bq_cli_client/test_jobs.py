"""E2E: Phase 2 jobs — load / extract / copy / head / DML via bq CLI.

Mirrors :mod:`tests.e2e.python_client.test_rest_crud_rest` and the Phase 2
job surfaces (extract, copy, head). ``bq head`` exercises
``tabledata.list`` — the same REST surface ``bq insert`` writes to
on the way in.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from .bq_runner import BqRunner

pytestmark = pytest.mark.e2e


def _mk_dataset(bq_runner: BqRunner, ds_id: str) -> None:
    result = bq_runner.run("mk", "--dataset", "--location=US", ds_id)
    assert result.succeeded(), result.stderr


def _rm_dataset(bq_runner: BqRunner, ds_id: str) -> None:
    bq_runner.run("rm", "-r", "-f", "-d", ds_id)


def test_load_newline_delimited_json(
    bq_runner: BqRunner,
    tmp_path: Path,
) -> None:
    """``bq load --source_format=NEWLINE_DELIMITED_JSON`` ingests an NDJSON file."""
    ds_id = "bq_cli_jobs_load_json"
    table_fq = f"{ds_id}.events"
    src = tmp_path / "events.ndjson"
    src.write_text(
        '{"id": 1, "event": "click"}\n{"id": 2, "event": "view"}\n{"id": 3, "event": "scroll"}\n',
        encoding="utf-8",
    )
    try:
        _mk_dataset(bq_runner, ds_id)
        result = bq_runner.run(
            "load",
            "--source_format=NEWLINE_DELIMITED_JSON",
            table_fq,
            str(src),
            "id:INTEGER,event:STRING",
        )
        assert result.succeeded(), result.stderr

        out = bq_runner.query_json(f"SELECT COUNT(*) AS n FROM `{table_fq}`")
        assert out == [{"n": "3"}]
    finally:
        _rm_dataset(bq_runner, ds_id)


def test_load_csv(bq_runner: BqRunner, tmp_path: Path) -> None:
    """``bq load --source_format=CSV --skip_leading_rows=1`` ingests a header-row CSV."""
    ds_id = "bq_cli_jobs_load_csv"
    table_fq = f"{ds_id}.t"
    src = tmp_path / "t.csv"
    src.write_text("id,name\n1,alpha\n2,beta\n", encoding="utf-8")
    try:
        _mk_dataset(bq_runner, ds_id)
        result = bq_runner.run(
            "load",
            "--source_format=CSV",
            "--skip_leading_rows=1",
            table_fq,
            str(src),
            "id:INTEGER,name:STRING",
        )
        assert result.succeeded(), result.stderr
        out = bq_runner.query_json(
            f"SELECT id, name FROM `{table_fq}` ORDER BY id",
        )
        assert out == [
            {"id": "1", "name": "alpha"},
            {"id": "2", "name": "beta"},
        ]
    finally:
        _rm_dataset(bq_runner, ds_id)


def test_extract_to_local_json(
    bq_runner: BqRunner,
    bqemu_gcs_root_host: Path,
) -> None:
    """``bq extract`` writes rows to a GCS-mounted NDJSON file.

    Uses ``--destination_format=NEWLINE_DELIMITED_JSON`` so the output
    is line-parseable without an extra deserializer step. ``bq``
    refuses local-path destinations client-side (``Illegal URI``),
    so we point it at ``gs://bqemu_bq_cli/...`` and read the rendered
    file back through the GCS local-root bind mount the conftest sets
    up for the live container.
    """
    ds_id = "bq_cli_jobs_extract"
    table_fq = f"{ds_id}.export_me"
    bucket = "bqemu_bq_cli"
    object_name = "extract.ndjson"
    dest_uri = f"gs://{bucket}/{object_name}"
    host_file = bqemu_gcs_root_host / bucket / object_name
    host_file.parent.mkdir(parents=True, exist_ok=True)
    if host_file.exists():
        host_file.unlink()
    try:
        _mk_dataset(bq_runner, ds_id)
        bq_runner.run("mk", "--table", table_fq, "id:INTEGER,name:STRING")
        bq_runner.run(
            "insert",
            table_fq,
            input_bytes=b'{"id": 1, "name": "alpha"}\n{"id": 2, "name": "beta"}\n',
        )

        result = bq_runner.run(
            "extract",
            "--destination_format=NEWLINE_DELIMITED_JSON",
            table_fq,
            dest_uri,
        )
        assert result.succeeded(), result.stderr
        assert host_file.exists(), f"expected extract at {host_file}"
        lines = [
            json.loads(line)
            for line in host_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        rows = sorted(lines, key=lambda r: int(r["id"]))
        assert rows == [
            {"id": 1, "name": "alpha"},
            {"id": 2, "name": "beta"},
        ]
    finally:
        _rm_dataset(bq_runner, ds_id)


def test_copy_table(bq_runner: BqRunner) -> None:
    """``bq cp`` copies a table within a dataset."""
    ds_id = "bq_cli_jobs_cp"
    src_fq = f"{ds_id}.src"
    dst_fq = f"{ds_id}.dst"
    try:
        _mk_dataset(bq_runner, ds_id)
        bq_runner.run("mk", "--table", src_fq, "id:INTEGER")
        bq_runner.run("insert", src_fq, input_bytes=b'{"id": 7}\n{"id": 11}\n')

        result = bq_runner.run("cp", "-f", src_fq, dst_fq)
        assert result.succeeded(), result.stderr

        out = bq_runner.query_json(f"SELECT id FROM `{dst_fq}` ORDER BY id")
        assert out == [{"id": "7"}, {"id": "11"}]
    finally:
        _rm_dataset(bq_runner, ds_id)


def test_head_returns_rows(bq_runner: BqRunner) -> None:
    """``bq head -n N`` returns up to N rows via tabledata.list."""
    ds_id = "bq_cli_jobs_head"
    table_fq = f"{ds_id}.rows"
    try:
        _mk_dataset(bq_runner, ds_id)
        bq_runner.run("mk", "--table", table_fq, "id:INTEGER")
        # 5 rows, ask for 3.
        bq_runner.run(
            "insert",
            table_fq,
            input_bytes=b"".join(f'{{"id": {n}}}\n'.encode() for n in (1, 2, 3, 4, 5)),
        )

        result = bq_runner.run("head", "-n", "3", "--format=json", table_fq)
        assert result.succeeded(), result.stderr
        rows = result.json()
        assert isinstance(rows, list)
        assert len(rows) == 3
    finally:
        _rm_dataset(bq_runner, ds_id)


def test_dml_insert_update_delete(bq_runner: BqRunner) -> None:
    """DML through ``bq query``: INSERT / UPDATE / DELETE round-trip."""
    ds_id = "bq_cli_jobs_dml"
    table_fq = f"{ds_id}.items"
    try:
        _mk_dataset(bq_runner, ds_id)
        bq_runner.run("mk", "--table", table_fq, "id:INTEGER,name:STRING")

        result = bq_runner.run(
            "query",
            "--use_legacy_sql=false",
            f"INSERT INTO `{table_fq}` VALUES (1, 'a'), (2, 'b'), (3, 'c')",
        )
        assert result.succeeded(), result.stderr

        result = bq_runner.run(
            "query",
            "--use_legacy_sql=false",
            f"UPDATE `{table_fq}` SET name='ZZ' WHERE id=2",
        )
        assert result.succeeded(), result.stderr

        result = bq_runner.run(
            "query",
            "--use_legacy_sql=false",
            f"DELETE FROM `{table_fq}` WHERE id=3",
        )
        assert result.succeeded(), result.stderr

        out = bq_runner.query_json(
            f"SELECT id, name FROM `{table_fq}` ORDER BY id",
        )
        assert out == [
            {"id": "1", "name": "a"},
            {"id": "2", "name": "ZZ"},
        ]
    finally:
        _rm_dataset(bq_runner, ds_id)


def test_dry_run_returns_no_rows(bq_runner: BqRunner) -> None:
    """``bq query --dry_run`` reports schema/statement type without execution."""
    ds_id = "bq_cli_jobs_dry"
    table_fq = f"{ds_id}.t"
    try:
        _mk_dataset(bq_runner, ds_id)
        bq_runner.run("mk", "--table", table_fq, "id:INTEGER")
        bq_runner.run("insert", table_fq, input_bytes=b'{"id": 1}\n')

        # ``bq query --dry_run`` prints an info banner to stderr and
        # exits 0. The body should NOT contain row data.
        result = bq_runner.run(
            "query",
            "--use_legacy_sql=false",
            "--dry_run",
            f"SELECT id FROM `{table_fq}`",
        )
        assert result.succeeded(), result.stderr
        # bq's dry-run prints to stderr (and stdout is typically empty
        # or carries just the schema preamble); the only invariant
        # worth pinning is that a dry-run cannot leak row values.
        assert '"id": "1"' not in result.stdout
    finally:
        _rm_dataset(bq_runner, ds_id)


def test_table_delete_via_rm(bq_runner: BqRunner) -> None:
    """``bq rm -f -t <table>`` removes a single table."""
    ds_id = "bq_cli_jobs_rm"
    table_fq = f"{ds_id}.t"
    try:
        _mk_dataset(bq_runner, ds_id)
        bq_runner.run("mk", "--table", table_fq, "id:INTEGER")

        result = bq_runner.run("rm", "-f", "-t", table_fq)
        assert result.succeeded(), result.stderr

        # ``bq show`` on the removed table fails.
        result = bq_runner.run("show", "--format=json", table_fq)
        assert not result.succeeded()
    finally:
        _rm_dataset(bq_runner, ds_id)
