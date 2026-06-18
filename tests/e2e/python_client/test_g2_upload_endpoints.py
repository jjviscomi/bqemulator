"""E2E: G2 upload-host endpoints against a live container.

AGENTS.md non-negotiable: every new feature gets four-language E2E
coverage. This is the Python suite — Node / Go / Java siblings live
next to it.

The Python ``google-cloud-bigquery`` client's
``load_table_from_file(io.BytesIO(...))`` API drives the multipart /
resumable upload host. The two tests below exercise both protocols
end-to-end against the published container.
"""

from __future__ import annotations

from collections.abc import Iterator
import io

from google.api_core.client_options import ClientOptions
from google.auth.credentials import AnonymousCredentials
from google.cloud import bigquery
import pytest

pytestmark = pytest.mark.e2e


@pytest.fixture
def bq_client(bqemu_rest_url: str) -> Iterator[bigquery.Client]:
    client = bigquery.Client(
        project="e2e-g2",
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=bqemu_rest_url),
    )
    try:
        yield client
    finally:
        client.close()


def _create_dataset_and_table(client: bigquery.Client, ds_id: str, tbl_id: str) -> None:
    dataset = bigquery.Dataset(f"{client.project}.{ds_id}")
    dataset.location = "US"
    client.create_dataset(dataset, exists_ok=True)
    schema = [
        bigquery.SchemaField("id", "INT64"),
        bigquery.SchemaField("name", "STRING"),
    ]
    client.create_table(
        bigquery.Table(f"{client.project}.{ds_id}.{tbl_id}", schema=schema),
        exists_ok=True,
    )


def test_load_table_from_file_csv_multipart(bq_client: bigquery.Client) -> None:
    """``load_table_from_file`` with a small file goes through the multipart path.

    The Python client picks multipart for payloads smaller than the
    resumable threshold; this exercises the multipart/related upload
    host directly without any cross-language test framework.
    """
    ds_id = "g2_csv_multipart"
    tbl_id = "rows"
    _create_dataset_and_table(bq_client, ds_id, tbl_id)
    table_ref = f"{bq_client.project}.{ds_id}.{tbl_id}"

    csv_bytes = b"id,name\n1,alice\n2,bob\n3,carol\n"
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.CSV,
        skip_leading_rows=1,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        schema=[
            bigquery.SchemaField("id", "INTEGER"),
            bigquery.SchemaField("name", "STRING"),
        ],
    )
    job = bq_client.load_table_from_file(
        io.BytesIO(csv_bytes),
        table_ref,
        job_config=job_config,
    )
    result = job.result(timeout=60)
    assert result.state == "DONE"

    rows = list(bq_client.query(f"SELECT COUNT(*) AS n FROM `{table_ref}`").result())
    assert rows[0].n == 3


def test_load_table_from_file_ndjson_resumable(bq_client: bigquery.Client) -> None:
    """Force a resumable upload via a payload past the threshold."""
    ds_id = "g2_json_resumable"
    tbl_id = "rows"
    _create_dataset_and_table(bq_client, ds_id, tbl_id)
    table_ref = f"{bq_client.project}.{ds_id}.{tbl_id}"

    # Synthesize a payload big enough that the client picks the
    # resumable protocol (~5 MiB above the threshold; the threshold
    # is ~5 MiB in recent client versions).
    rows = "".join(f'{{"id":{i},"name":"name-{i}"}}\n' for i in range(80_000))
    ndjson_bytes = rows.encode()
    assert len(ndjson_bytes) > 1_000_000  # comfortably past any threshold

    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        schema=[
            bigquery.SchemaField("id", "INTEGER"),
            bigquery.SchemaField("name", "STRING"),
        ],
    )
    job = bq_client.load_table_from_file(
        io.BytesIO(ndjson_bytes),
        table_ref,
        job_config=job_config,
    )
    result = job.result(timeout=120)
    assert result.state == "DONE"

    out = list(bq_client.query(f"SELECT COUNT(*) AS n FROM `{table_ref}`").result())
    assert out[0].n == 80_000


def test_load_table_csv_autodetect(bq_client: bigquery.Client) -> None:
    """CSV load honoring the autodetect flag."""
    ds_id = "g2_csv_autodetect"
    tbl_id = "rows"
    # Ensure dataset exists, but NOT the table.
    dataset = bigquery.Dataset(f"{bq_client.project}.{ds_id}")
    dataset.location = "US"
    bq_client.create_dataset(dataset, exists_ok=True)
    # Delete the table if it exists from a prior run.
    from google.api_core.exceptions import NotFound
    try:
        bq_client.delete_table(f"{bq_client.project}.{ds_id}.{tbl_id}")
    except NotFound:
        pass

    table_ref = f"{bq_client.project}.{ds_id}.{tbl_id}"
    csv_bytes = b"id,name,score\n1,alice,99.5\n2,bob,88.2\n"

    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.CSV,
        skip_leading_rows=1,
        autodetect=True,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        create_disposition=bigquery.CreateDisposition.CREATE_IF_NEEDED,
    )
    job = bq_client.load_table_from_file(
        io.BytesIO(csv_bytes),
        table_ref,
        job_config=job_config,
    )
    result = job.result(timeout=60)
    assert result.state == "DONE"

    rows = list(bq_client.query(f"SELECT COUNT(*) AS n FROM `{table_ref}`").result())
    assert rows[0].n == 2

    # Assert table was actually created and inferred
    table = bq_client.get_table(table_ref)
    names = [f.name for f in table.schema]
    assert names == ["id", "name", "score"]
