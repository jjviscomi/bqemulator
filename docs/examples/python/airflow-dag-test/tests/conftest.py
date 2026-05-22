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


def _mint_fake_service_account_key(path: Path, project: str) -> None:
    """Write a syntactically valid service-account keyfile.

    Airflow's ``BigQueryHook`` resolves credentials (via
    ``google.auth.default()``) before ``BIGQUERY_EMULATOR_HOST`` can
    short-circuit anything. Without ADC the hook raises
    ``DefaultCredentialsError`` at the first attribute lookup. Synthesise
    a real RSA-PEM keyfile here so the parser is satisfied; the
    emulator never calls Google's token endpoint, so the key stays
    local.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    path.write_text(
        json.dumps(
            {
                "type": "service_account",
                "project_id": project,
                "private_key_id": "bqemu-fake-key",
                "private_key": pem,
                "client_email": f"bqemu-fake@{project}.iam.gserviceaccount.com",
                "client_id": "1",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": (
                    "https://www.googleapis.com/oauth2/v1/certs"
                ),
            }
        )
    )


@pytest.fixture(scope="session", autouse=True)
def _emulator_env(
    bqemu_server,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[None]:
    """Wire the emulator into the env vars that the BQ hook consumes."""
    rest_url = bqemu_server.rest_url
    host = rest_url.removeprefix("http://").removeprefix("https://")
    project = os.environ.get("BQ_PROJECT", "bqemu-demo")
    key_path = tmp_path_factory.mktemp("gcp_keys") / "fake-sa.json"
    _mint_fake_service_account_key(key_path, project)

    previous_emu = os.environ.get("BIGQUERY_EMULATOR_HOST")
    previous_adc = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    os.environ["BIGQUERY_EMULATOR_HOST"] = host
    # ADC fallback so ``google.auth.default()`` succeeds — the
    # connection's ``key_path`` covers the Airflow hook path, this
    # covers anything that bypasses the connection layer.
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(key_path)
    conn = {
        "conn_type": "google_cloud_platform",
        "extra": json.dumps(
            {
                "project": project,
                "key_path": str(key_path),
                "scope": "https://www.googleapis.com/auth/bigquery",
            }
        ),
    }
    os.environ["AIRFLOW_CONN_GOOGLE_CLOUD_DEFAULT"] = (
        f"google-cloud-platform://?{conn['extra']}"
    )
    yield
    if previous_emu is None:
        os.environ.pop("BIGQUERY_EMULATOR_HOST", None)
    else:
        os.environ["BIGQUERY_EMULATOR_HOST"] = previous_emu
    if previous_adc is None:
        os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
    else:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = previous_adc


@pytest.fixture
def unique_dataset(monkeypatch: pytest.MonkeyPatch) -> str:
    """Pin a unique dataset name for the DAG run."""
    name = f"airflow_demo_{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("BQ_DATASET", name)
    return name
