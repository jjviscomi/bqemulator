"""E2E: Phase 10 admin surfaces — ``bq cp``, multi-dataset cleanup, ``bq update``.

bq's admin-shape CLI commands route through the same REST surface
the SDK suites exercise; this file pins the bq-specific command
ergonomics (copy across datasets; updating dataset description /
default_table_expiration_ms via ``bq update``).
"""

from __future__ import annotations

import pytest

from .bq_runner import BqRunner

pytestmark = pytest.mark.e2e


def _mk_dataset(bq_runner: BqRunner, ds_id: str) -> None:
    assert bq_runner.run("mk", "--dataset", "--location=US", ds_id).succeeded()


def _rm_dataset(bq_runner: BqRunner, ds_id: str) -> None:
    bq_runner.run("rm", "-r", "-f", "-d", ds_id)


def test_cross_dataset_copy(bq_runner: BqRunner) -> None:
    """``bq cp`` copies a table across two datasets in the same project."""
    src_ds = "bq_cli_admin_src"
    dst_ds = "bq_cli_admin_dst"
    src = f"{src_ds}.t"
    dst = f"{dst_ds}.t"
    try:
        _mk_dataset(bq_runner, src_ds)
        _mk_dataset(bq_runner, dst_ds)
        bq_runner.run("mk", "--table", src, "id:INTEGER")
        bq_runner.run("insert", src, input_bytes=b'{"id": 1}\n{"id": 2}\n')

        result = bq_runner.run("cp", "-f", src, dst)
        assert result.succeeded(), result.stderr
        out = bq_runner.query_json(f"SELECT id FROM `{dst}` ORDER BY id")
        assert out == [{"id": "1"}, {"id": "2"}]
    finally:
        _rm_dataset(bq_runner, src_ds)
        _rm_dataset(bq_runner, dst_ds)


def test_update_dataset_description(bq_runner: BqRunner) -> None:
    """``bq update --description`` mutates dataset metadata."""
    ds_id = "bq_cli_admin_update"
    try:
        _mk_dataset(bq_runner, ds_id)
        result = bq_runner.run(
            "update",
            "--description=updated via bq-cli e2e",
            "-d",
            ds_id,
        )
        assert result.succeeded(), result.stderr

        meta = bq_runner.run("show", "--format=json", "-d", ds_id)
        parsed = meta.json()
        assert isinstance(parsed, dict)
        assert parsed["description"] == "updated via bq-cli e2e"
    finally:
        _rm_dataset(bq_runner, ds_id)


def test_recursive_dataset_removal(bq_runner: BqRunner) -> None:
    """``bq rm -r -f -d`` removes a dataset and its tables in one call."""
    ds_id = "bq_cli_admin_rmtree"
    try:
        _mk_dataset(bq_runner, ds_id)
        for name in ("a", "b", "c"):
            bq_runner.run("mk", "--table", f"{ds_id}.{name}", "id:INTEGER")

        # `-r` enables recursive removal of contained tables.
        result = bq_runner.run("rm", "-r", "-f", "-d", ds_id)
        assert result.succeeded(), result.stderr

        # ``ls`` against the gone dataset fails or returns empty.
        result = bq_runner.run("ls", "--format=json", ds_id)
        # bq returns rc=1 with "Not found:" when the dataset is gone;
        # newer bq versions exit 0 with empty stdout. Accept either,
        # including the bare empty-JSON form ``[]``.
        if result.succeeded():
            stripped = result.stdout.strip()
            assert stripped in ("", "[]"), result.stdout
    finally:
        # Idempotent cleanup in case the test bailed early.
        _rm_dataset(bq_runner, ds_id)
