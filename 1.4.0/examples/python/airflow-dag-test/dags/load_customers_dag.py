"""DAG that loads a customers table and runs an aggregate query.

Three sequential tasks:

1. ``create_dataset`` — creates the target dataset.
2. ``load_customers`` — runs an inline ``INSERT`` that seeds three rows.
3. ``count_customers`` — runs ``SELECT COUNT(*)`` and writes the result
   to an XCom-pushable destination table.

Each task uses the Google provider's
``BigQueryInsertJobOperator`` which is the recommended idiom for any
non-trivial BQ workload in Airflow (it covers DDL, DML, and queries).
"""

from __future__ import annotations

import os
from datetime import datetime

from airflow import DAG
from airflow.providers.google.cloud.operators.bigquery import (
    BigQueryInsertJobOperator,
)

PROJECT = os.environ.get("BQ_PROJECT", "bqemu-demo")
DATASET = os.environ.get("BQ_DATASET", "airflow_demo")

with DAG(
    dag_id="load_customers",
    start_date=datetime(2026, 1, 1),
    schedule=None,
    catchup=False,
    tags=["bqemulator"],
) as dag:
    create_dataset = BigQueryInsertJobOperator(
        task_id="create_dataset",
        configuration={
            "query": {
                "query": (
                    f"CREATE SCHEMA IF NOT EXISTS `{PROJECT}.{DATASET}` "
                    "OPTIONS(location='US')"
                ),
                "useLegacySql": False,
            }
        },
    )

    load_customers = BigQueryInsertJobOperator(
        task_id="load_customers",
        configuration={
            "query": {
                "query": (
                    f"CREATE OR REPLACE TABLE `{PROJECT}.{DATASET}.customers` "
                    "(id INT64, name STRING) AS "
                    "SELECT 1 AS id, 'Alice' AS name UNION ALL "
                    "SELECT 2, 'Bob' UNION ALL "
                    "SELECT 3, 'Carol'"
                ),
                "useLegacySql": False,
            }
        },
    )

    count_customers = BigQueryInsertJobOperator(
        task_id="count_customers",
        configuration={
            "query": {
                "query": (
                    f"SELECT COUNT(*) AS n FROM `{PROJECT}.{DATASET}.customers`"
                ),
                "useLegacySql": False,
            }
        },
        do_xcom_push=True,
    )

    create_dataset >> load_customers >> count_customers
