"""Run the load_customers DAG offline against bqemulator."""

from __future__ import annotations

import importlib
import os

import pytest


def _get_dag(unique_dataset: str):
    """(Re)import the DAG module with the per-test dataset env applied."""
    if "load_customers_dag" in list(globals()):
        return globals()["load_customers_dag"].dag
    import load_customers_dag

    importlib.reload(load_customers_dag)
    return load_customers_dag.dag


def test_dag_parses(unique_dataset: str) -> None:
    dag = _get_dag(unique_dataset)
    assert dag.dag_id == "load_customers"
    assert {t.task_id for t in dag.tasks} == {
        "create_dataset",
        "load_customers",
        "count_customers",
    }


def test_dag_runs_against_emulator(unique_dataset: str) -> None:
    """Execute each task in order; assert COUNT(*) returns 3."""
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery

    dag = _get_dag(unique_dataset)
    project = os.environ["BQ_PROJECT"] = os.environ.get("BQ_PROJECT", "bqemu-demo")

    dag.test()

    rest = f"http://{os.environ['BIGQUERY_EMULATOR_HOST']}"
    client = bigquery.Client(
        project=project,
        credentials=AnonymousCredentials(),  # type: ignore[no-untyped-call]
        client_options=ClientOptions(api_endpoint=rest),
    )
    rows = list(
        client.query(
            f"SELECT COUNT(*) AS n FROM `{project}.{unique_dataset}.customers`"
        ).result()
    )
    assert rows[0].n == 3
