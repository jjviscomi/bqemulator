"""E2E: ``EXPORT DATA`` → Cloud Storage (CSV) via Google's ``bq`` CLI.

The ``bq`` CLI is the fifth conformance client (see
:mod:`tests.e2e.bq_cli_client.bq_runner`). It shells out to the
operator's ``bq`` binary against the live container started by the
shared ``bqemu_container`` fixture, so it reuses the session GCS root
mount: the exported file the executor writes inside the container is
visible on the host at ``bqemu_gcs_root_host``.

``EXPORT DATA`` runs as a query job; the wildcard ``uri`` expands its
``*`` to a 12-digit zero-padded shard counter, so
``export_bq_cli/*.csv`` becomes ``export_bq_cli/000000000000.csv`` for
the single-shard case.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from .bq_runner import BqRunner

pytestmark = pytest.mark.e2e

_BUCKET = "g1-e2e"


def test_export_data_to_csv(
    bq_runner: BqRunner,
    bqemu_gcs_root_host: Path,
) -> None:
    """``bq query 'EXPORT DATA …'`` writes a sharded CSV under the mount."""
    ds_id = "bq_cli_export_csv"
    project = bq_runner.project_id
    src = f"`{project}.{ds_id}.src`"

    mk = bq_runner.run("mk", "--dataset", "--location=US", ds_id)
    assert mk.succeeded(), mk.stderr
    try:
        create = bq_runner.run(
            "query",
            "--use_legacy_sql=false",
            f"CREATE TABLE {src} (id INT64, name STRING)",
        )
        assert create.succeeded(), create.stderr

        insert = bq_runner.run(
            "query",
            "--use_legacy_sql=false",
            f"INSERT INTO {src} (id, name) VALUES (1, 'alpha'), (2, 'beta'), (3, 'gamma')",
        )
        assert insert.succeeded(), insert.stderr

        export = bq_runner.run(
            "query",
            "--use_legacy_sql=false",
            f"EXPORT DATA OPTIONS ("
            f"uri = 'gs://{_BUCKET}/export_bq_cli/*.csv', "
            f"format = 'CSV', "
            f"overwrite = true) AS "
            f"SELECT id, name FROM {src} ORDER BY id",
        )
        assert export.succeeded(), export.stderr

        shard = bqemu_gcs_root_host / _BUCKET / "export_bq_cli" / "000000000000.csv"
        assert shard.exists(), f"expected export shard at {shard}"
        with shard.open(newline="") as fh:
            csv_rows = list(csv.reader(fh))
        assert csv_rows[0] == ["id", "name"]
        assert csv_rows[1:] == [["1", "alpha"], ["2", "beta"], ["3", "gamma"]]
    finally:
        bq_runner.run("rm", "-r", "-f", "-d", ds_id)
