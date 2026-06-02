"""E2E: Phase 6 routines + scripting against a live container.

Exercises the Phase 6 ship criterion in a single end-to-end pass
through the official ``google-cloud-bigquery`` Python client:

- Create a dataset.
- Insert a SQL scalar UDF, a JavaScript UDF, and a table-valued function.
- Read INFORMATION_SCHEMA.ROUTINES and verify all three show up.
- Run a scripting query that uses DECLARE, SET, IF, LOOP, EXCEPTION WHEN,
  a SQL UDF, a JS UDF, and the TVF in a single statement.
- Confirm the computed answer matches what real BigQuery returns.
"""

from __future__ import annotations

from collections.abc import Iterator

from google.api_core.client_options import ClientOptions
from google.auth.credentials import AnonymousCredentials
from google.cloud import bigquery
import pytest

pytestmark = pytest.mark.e2e


@pytest.fixture
def bq_client(bqemu_rest_url: str) -> Iterator[bigquery.Client]:
    """BigQuery Python client bound to the emulator REST endpoint."""
    client = bigquery.Client(
        project="e2e-routines_scripting",
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=bqemu_rest_url),
    )
    try:
        yield client
    finally:
        client.close()


def _insert_routine(
    client: bigquery.Client,
    dataset_id: str,
    routine_id: str,
    *,
    routine_type: str,
    language: str,
    definition_body: str,
    arguments: list[bigquery.RoutineArgument] | None = None,
    return_type_kind: str | None = None,
) -> None:
    ref = bigquery.RoutineReference.from_string(
        f"{client.project}.{dataset_id}.{routine_id}",
    )
    routine = bigquery.Routine(ref)
    routine.type_ = routine_type
    routine.language = language
    routine.body = definition_body
    if arguments is not None:
        routine.arguments = arguments
    if return_type_kind is not None:
        routine.return_type = bigquery.StandardSqlDataType(
            type_kind=getattr(bigquery.StandardSqlTypeNames, return_type_kind),
        )
    client.create_routine(routine, exists_ok=True)


def test_routines_scripting_ship_criterion(bq_client: bigquery.Client) -> None:
    """Phase 6 ship criterion — end to end through the Python client."""
    ds_id = "routines_scripting_ds"
    dataset = bigquery.Dataset(f"{bq_client.project}.{ds_id}")
    dataset.location = "US"

    try:
        bq_client.create_dataset(dataset, exists_ok=True)

        # SQL scalar UDF
        _insert_routine(
            bq_client,
            ds_id,
            "sql_inc",
            routine_type="SCALAR_FUNCTION",
            language="SQL",
            definition_body="x + 1",
            arguments=[
                bigquery.RoutineArgument(
                    name="x",
                    data_type=bigquery.StandardSqlDataType(
                        type_kind=bigquery.StandardSqlTypeNames.INT64
                    ),
                ),
            ],
            return_type_kind="INT64",
        )

        # JavaScript UDF
        _insert_routine(
            bq_client,
            ds_id,
            "js_double",
            routine_type="SCALAR_FUNCTION",
            language="JAVASCRIPT",
            definition_body="return x * 2;",
            arguments=[
                bigquery.RoutineArgument(
                    name="x",
                    data_type=bigquery.StandardSqlDataType(
                        type_kind=bigquery.StandardSqlTypeNames.INT64
                    ),
                ),
            ],
            return_type_kind="INT64",
        )

        # Table-valued function
        _insert_routine(
            bq_client,
            ds_id,
            "one_to_n",
            routine_type="TABLE_VALUED_FUNCTION",
            language="SQL",
            definition_body=("SELECT i AS value FROM UNNEST(GENERATE_ARRAY(1, n)) AS i"),
            arguments=[
                bigquery.RoutineArgument(
                    name="n",
                    data_type=bigquery.StandardSqlDataType(
                        type_kind=bigquery.StandardSqlTypeNames.INT64
                    ),
                ),
            ],
        )

        # INFORMATION_SCHEMA.ROUTINES lists all three.
        job = bq_client.query(
            f"SELECT routine_name FROM `{bq_client.project}`.{ds_id}"
            ".INFORMATION_SCHEMA.ROUTINES ORDER BY routine_name",
        )
        names = [row["routine_name"] for row in job.result()]
        assert names == ["js_double", "one_to_n", "sql_inc"]

        # Ship-criterion script: DECLARE + SET + IF + LOOP + EXCEPTION WHEN,
        # calling SQL UDF + JS UDF + TVF in combination.
        script = f"""
DECLARE n INT64 DEFAULT 3;
DECLARE total INT64 DEFAULT 0;
BEGIN
  FOR row IN (SELECT value FROM {ds_id}.one_to_n(n)) DO
    SET total = total + {ds_id}.js_double({ds_id}.sql_inc(row.value));
  END FOR;
EXCEPTION WHEN ERROR THEN
  SET total = -1;
END;
IF total > 0 THEN
  SELECT total AS answer;
ELSE
  SELECT -1 AS answer;
END IF;
"""
        job = bq_client.query(script)
        result = list(job.result())
        # 1→2→4, 2→3→6, 3→4→8 ; total = 18
        assert result[0]["answer"] == 18

    finally:
        bq_client.delete_dataset(
            f"{bq_client.project}.{ds_id}",
            delete_contents=True,
            not_found_ok=True,
        )


def test_routines_scripting_dml_flow(bq_client: bigquery.Client) -> None:
    """Phase 6 DML verification — INSERT/UPDATE/DELETE/TRUNCATE against a live container."""
    ds_id = "routines_scripting_dml"
    tbl_id = "items"
    dataset = bigquery.Dataset(f"{bq_client.project}.{ds_id}")
    dataset.location = "US"
    try:
        bq_client.create_dataset(dataset, exists_ok=True)
        table = bigquery.Table(
            f"{bq_client.project}.{ds_id}.{tbl_id}",
            schema=[
                bigquery.SchemaField("id", "INT64", mode="REQUIRED"),
                bigquery.SchemaField("name", "STRING"),
            ],
        )
        bq_client.create_table(table, exists_ok=True)

        bq_client.query(
            f"INSERT INTO `{bq_client.project}.{ds_id}.{tbl_id}` VALUES "
            "(1, 'a'), (2, 'b'), (3, 'c')",
        ).result()
        bq_client.query(
            f"UPDATE `{bq_client.project}.{ds_id}.{tbl_id}` SET name='zz' WHERE id=1",
        ).result()
        bq_client.query(
            f"DELETE FROM `{bq_client.project}.{ds_id}.{tbl_id}` WHERE id=2",
        ).result()
        rows = list(
            bq_client.query(
                f"SELECT id, name FROM `{bq_client.project}.{ds_id}.{tbl_id}` ORDER BY id",
            ).result(),
        )
        assert [dict(r) for r in rows] == [
            {"id": 1, "name": "zz"},
            {"id": 3, "name": "c"},
        ]

        bq_client.query(
            f"TRUNCATE TABLE `{bq_client.project}.{ds_id}.{tbl_id}`",
        ).result()
        remaining = list(
            bq_client.query(
                f"SELECT COUNT(*) AS n FROM `{bq_client.project}.{ds_id}.{tbl_id}`",
            ).result(),
        )
        assert remaining[0]["n"] == 0
    finally:
        bq_client.delete_dataset(
            f"{bq_client.project}.{ds_id}",
            delete_contents=True,
            not_found_ok=True,
        )


def test_scripted_create_schema_is_listed(bq_client: bigquery.Client) -> None:
    """A ``CREATE SCHEMA`` inside a multi-statement script registers the dataset.

    A single-statement ``CREATE SCHEMA`` takes the executor fast path;
    the trailing ``SELECT`` tips this job into the scripting interpreter,
    whose DDL-sync hook must register the dataset so it surfaces via
    ``datasets.list`` and ``datasets.get``, matching real BigQuery.
    """
    ds_id = "scripted_created_schema_ds"
    fqdn = f"{bq_client.project}.{ds_id}"
    try:
        # Guard against a stale dataset left by an earlier interrupted run.
        bq_client.delete_dataset(fqdn, delete_contents=True, not_found_ok=True)

        bq_client.query(f"CREATE SCHEMA `{ds_id}`;\nSELECT 1 AS n;").result()

        listed = {ds.dataset_id for ds in bq_client.list_datasets()}
        assert ds_id in listed
        assert bq_client.get_dataset(fqdn).dataset_id == ds_id
    finally:
        bq_client.delete_dataset(fqdn, delete_contents=True, not_found_ok=True)
