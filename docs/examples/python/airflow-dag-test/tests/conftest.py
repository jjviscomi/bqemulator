"""Test wiring: Airflow connection, env, and DAG import path."""

from __future__ import annotations

import json
import os
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dags"))


@pytest.fixture(scope="session", autouse=True)
def _airflow_home(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Use a clean AIRFLOW_HOME for the session.

    Avoids reading the user's real Airflow config, and prevents Airflow
    from picking up an already-initialized metadata DB.
    """
    home = tmp_path_factory.mktemp("airflow_home")
    previous = os.environ.get("AIRFLOW_HOME")
    os.environ["AIRFLOW_HOME"] = str(home)
    os.environ["AIRFLOW__CORE__LOAD_EXAMPLES"] = "False"
    os.environ["AIRFLOW__CORE__UNIT_TEST_MODE"] = "True"
    os.environ["AIRFLOW__CORE__DAGS_FOLDER"] = str(
        Path(__file__).resolve().parent.parent / "dags"
    )
    yield
    if previous is None:
        os.environ.pop("AIRFLOW_HOME", None)
    else:
        os.environ["AIRFLOW_HOME"] = previous


@pytest.fixture(scope="session", autouse=True)
def _emulator_env(bqemu_server) -> Iterator[None]:
    """Wire the emulator into the env vars that the BQ hook consumes."""
    rest_url = bqemu_server.rest_url
    host = rest_url.removeprefix("http://").removeprefix("https://")
    previous = os.environ.get("BIGQUERY_EMULATOR_HOST")
    os.environ["BIGQUERY_EMULATOR_HOST"] = host
    conn = {
        "conn_type": "google_cloud_platform",
        "extra": json.dumps(
            {
                "project": os.environ.get("BQ_PROJECT", "bqemu-demo"),
                "key_path": "",
                "scope": "https://www.googleapis.com/auth/bigquery",
            }
        ),
    }
    os.environ["AIRFLOW_CONN_GOOGLE_CLOUD_DEFAULT"] = (
        f"google-cloud-platform://?{conn['extra']}"
    )
    yield
    if previous is None:
        os.environ.pop("BIGQUERY_EMULATOR_HOST", None)
    else:
        os.environ["BIGQUERY_EMULATOR_HOST"] = previous


@pytest.fixture
def unique_dataset(monkeypatch: pytest.MonkeyPatch) -> str:
    """Pin a unique dataset name for the DAG run."""
    name = f"airflow_demo_{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("BQ_DATASET", name)
    return name
