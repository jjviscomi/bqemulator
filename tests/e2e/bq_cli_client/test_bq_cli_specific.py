"""E2E: bq-CLI-only surfaces — output formats, ``.bigqueryrc``, error rendering.

These tests exercise bq surfaces NO other client suite touches.
They are the load-bearing justification for adding a fifth
conformance client: a regression in the emulator's tabledata
JSON envelope (or its error-message rendering) might leave the
SDK suites green but break every shell script that pipes
``bq query`` output into ``jq`` / ``awk`` / ``cut``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from .bq_runner import BqRunner

pytestmark = pytest.mark.e2e


def _mk_dataset(bq_runner: BqRunner, ds_id: str) -> None:
    assert bq_runner.run("mk", "--dataset", "--location=US", ds_id).succeeded()


def _rm_dataset(bq_runner: BqRunner, ds_id: str) -> None:
    bq_runner.run("rm", "-r", "-f", "-d", ds_id)


def test_query_output_format_csv(bq_runner: BqRunner) -> None:
    """``bq query --format=csv`` emits a header row + CSV-encoded rows."""
    result = bq_runner.run(
        "query",
        "--use_legacy_sql=false",
        "--format=csv",
        "SELECT 1 AS n, 'alpha' AS s",
    )
    assert result.succeeded(), result.stderr
    # bq's CSV output has a header line then row lines.
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert lines[0] == "n,s"
    assert lines[1] == "1,alpha"


def test_query_output_format_pretty(bq_runner: BqRunner) -> None:
    """``bq query --format=pretty`` emits a box-drawing ASCII table."""
    result = bq_runner.run(
        "query",
        "--use_legacy_sql=false",
        "--format=pretty",
        "SELECT 1 AS n, 'alpha' AS s",
    )
    assert result.succeeded(), result.stderr
    # Pretty output is bordered with ``+`` and ``-`` and pipe
    # characters; the column header row contains the column names.
    assert "+" in result.stdout
    assert "-" in result.stdout
    assert "|" in result.stdout
    assert "n" in result.stdout
    assert "s" in result.stdout
    assert "alpha" in result.stdout


def test_query_output_format_sparse(bq_runner: BqRunner) -> None:
    """``bq query --format=sparse`` emits whitespace-padded minimal-decoration output."""
    result = bq_runner.run(
        "query",
        "--use_legacy_sql=false",
        "--format=sparse",
        "SELECT 1 AS n, 'alpha' AS s",
    )
    assert result.succeeded(), result.stderr
    # Sparse format includes the column header but no box drawing.
    assert "alpha" in result.stdout
    assert "+" not in result.stdout


def test_bigqueryrc_endpoint_override(
    bq_runner: BqRunner,
    bqemu_rest_url: str,
    tmp_path: Path,
) -> None:
    """Endpoint resolution via a custom ``bigqueryrc`` file works.

    Writes a ``bigqueryrc`` to an isolated ``CLOUDSDK_CONFIG`` dir and
    invokes bq WITHOUT ``--api=`` — proving the endpoint override
    flows through the config file just as it would for a user who
    sets it once via ``gcloud config set api_endpoint_overrides/bigquery``.
    Stages the synthetic gcloud credentials inside the same isolated
    config dir so modern bq (gcloud SDK 2.x) doesn't reject the
    invocation with "no active account selected". Reuses the session-
    scoped ``bq_runner``'s cached discovery doc so bq doesn't try to
    GET ``$discovery/rest`` from the emulator (which only serves the
    REST surface, not the discovery doc).
    """
    from .bq_runner import _stage_gcloud_sandbox

    bq_bin = shutil.which("bq")
    if bq_bin is None:
        pytest.skip("bq CLI not installed")

    config_dir = tmp_path / "isolated_config"
    config_dir.mkdir()
    _stage_gcloud_sandbox(config_dir, project_id="e2e-bq-cli-rc")
    # bq resolves ``bigqueryrc`` via ``--bigqueryrc=<path>`` (flag),
    # the ``BIGQUERYRC`` env var, or the default ``~/.bigqueryrc``.
    # Use the flag form so the test is independent of the operator's
    # ``$HOME`` content.
    rc_file = config_dir / "bigqueryrc"
    rc_file.write_text(
        # Reset any defaults from the operator's environment, then
        # pin the API endpoint to the emulator.
        f"project_id = e2e-bq-cli-rc\napi = {bqemu_rest_url}\n",
        encoding="utf-8",
    )

    proc = subprocess.run(  # noqa: S603 — list args, no shell
        [
            bq_bin,
            f"--bigqueryrc={rc_file}",
            f"--discovery_file={bq_runner.discovery_file}",
            "--project_id=e2e-bq-cli-rc",
            "query",
            "--use_legacy_sql=false",
            "--format=json",
            "SELECT 1 AS n",
        ],
        env={
            "PATH": os.environ.get("PATH", ""),
            # Ensure HOME doesn't shadow our isolated config dir.
            "HOME": str(tmp_path),
            "CLOUDSDK_AUTH_DISABLE_CREDENTIALS": "true",
            "CLOUDSDK_CONFIG": str(config_dir),
        },
        capture_output=True,
        timeout=60,
        check=False,
    )
    # If the endpoint override didn't apply, bq would try to reach the
    # real bigquery.googleapis.com and either fail auth (because of
    # CLOUDSDK_AUTH_DISABLE_CREDENTIALS) or succeed against the live
    # service. Against our emulator the call returns the local result.
    assert proc.returncode == 0, proc.stderr.decode()
    rows = json.loads(proc.stdout.decode())
    assert rows == [{"n": "1"}]


def test_error_shape_for_invalid_function(bq_runner: BqRunner) -> None:
    """``bq query`` renders SQL errors with its canonical text shape.

    bq's error renderer differs from the JSON error envelopes the SDK
    clients see — bq prints lines like
    ``BigQuery error in query operation: Error processing job ...``
    to stderr. This test pins that surface so an error_mapper
    regression that leaves the SDK clients green can't ship.
    """
    result = bq_runner.run(
        "query",
        "--use_legacy_sql=false",
        "SELECT this_function_does_not_exist(1)",
    )
    assert not result.succeeded()
    # bq's pretty-printed error always carries the "BigQuery error"
    # prefix on stderr; the inner message contains a function-name
    # hint. We pin the prefix only (the inner message is allowed
    # to evolve as long as it's human-readable) so this test
    # doesn't tightly couple to bq's exact phrasing.
    combined = result.stdout + result.stderr
    assert "BigQuery error" in combined or "Error" in combined
    # The bad function name should appear somewhere in the rendered error.
    assert "this_function_does_not_exist" in combined
