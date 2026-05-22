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

    # Airflow 2.8+ requires an initialised metadata DB before
    # ``dag.test()`` can persist task-instance rows. Importing
    # ``initdb`` here (rather than shelling out to the ``airflow``
    # CLI) keeps the fixture self-contained.
    from airflow.utils.db import initdb

    initdb()

    yield
    if previous is None:
        os.environ.pop("AIRFLOW_HOME", None)
    else:
        os.environ["AIRFLOW_HOME"] = previous


@pytest.fixture(scope="session", autouse=True)
def _emulator_env(bqemu_server) -> Iterator[None]:
    """Wire the emulator into the env vars that the BQ hook consumes.

    ``BigQueryHook`` resolves credentials via ``google.auth.default()``
    and then forwards them to ``google.cloud.bigquery.Client``. A real
    service-account keyfile gets through ADC but the client then does
    a JWT grant against ``oauth2.googleapis.com/token`` on the first
    API call — which fails with ``invalid_grant`` for a synthetic SA.
    bqemulator doesn't validate auth, so the cleanest workaround is
    to make ``google.auth.default()`` hand back
    ``AnonymousCredentials`` for the duration of the session. Airflow
    then propagates those into the BQ client and no token exchange
    ever happens.
    """
    import google.auth
    import google.auth._default
    import google.auth.credentials

    project = os.environ.get("BQ_PROJECT", "bqemu-demo")
    anon = google.auth.credentials.AnonymousCredentials()

    def _emu_default(scopes=None, request=None, quota_project_id=None,
                     default_scopes=None):  # noqa: ANN001
        return anon, project

    rest_url = bqemu_server.rest_url
    # The Airflow Google provider forwards ``BIGQUERY_EMULATOR_HOST``
    # verbatim into ``client_options.api_endpoint``; without the
    # scheme, ``requests`` aborts with
    # ``No connection adapters were found for '127.0.0.1:PORT/...'``.
    # Set the env var with ``http://`` already in place.
    previous_emu = os.environ.get("BIGQUERY_EMULATOR_HOST")
    previous_default = google.auth.default
    previous_internal_default = google.auth._default.default
    os.environ["BIGQUERY_EMULATOR_HOST"] = rest_url
    google.auth.default = _emu_default
    google.auth._default.default = _emu_default
    conn = {
        "conn_type": "google_cloud_platform",
        "extra": json.dumps(
            {
                "project": project,
                "key_path": "",
                "scope": "https://www.googleapis.com/auth/bigquery",
            }
        ),
    }
    os.environ["AIRFLOW_CONN_GOOGLE_CLOUD_DEFAULT"] = (
        f"google-cloud-platform://?{conn['extra']}"
    )
    yield
    google.auth.default = previous_default
    google.auth._default.default = previous_internal_default
    if previous_emu is None:
        os.environ.pop("BIGQUERY_EMULATOR_HOST", None)
    else:
        os.environ["BIGQUERY_EMULATOR_HOST"] = previous_emu


@pytest.fixture
def unique_dataset(monkeypatch: pytest.MonkeyPatch) -> str:
    """Pin a unique dataset name for the DAG run."""
    name = f"airflow_demo_{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("BQ_DATASET", name)
    return name
