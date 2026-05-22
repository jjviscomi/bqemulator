"""PySpark reads from bqemulator via the Storage Read API (Arrow).

CI runs this file via ``make test``. It:

1. Starts an in-process bqemulator with REST + gRPC on random ports.
2. Seeds a 5-row ``customers`` table via the standard
   ``google-cloud-bigquery`` client (REST path).
3. Reads those rows back via ``google-cloud-bigquery-storage``
   (Storage Read API, gRPC, Arrow output).
4. Builds a Spark DataFrame from the Arrow record batches.
5. Runs an aggregate and asserts the count matches.
"""

from __future__ import annotations

import os

import pyarrow as pa
from google.api_core.client_options import ClientOptions
from google.auth.credentials import AnonymousCredentials
from google.cloud import bigquery, bigquery_storage_v1
from google.cloud.bigquery_storage_v1.types import DataFormat, ReadSession
from pyspark.sql import SparkSession

from bqemulator.config import PersistenceMode, Settings
from bqemulator.testing._thread_runner import ThreadedEmulator


PROJECT = "bqemu-spark"
DATASET = "spark_demo"
TABLE = "customers"


def _seed(rest_url: str) -> None:
    client = bigquery.Client(
        project=PROJECT,
        credentials=AnonymousCredentials(),  # type: ignore[no-untyped-call]
        client_options=ClientOptions(api_endpoint=rest_url),
    )
    try:
        dataset = bigquery.Dataset(f"{PROJECT}.{DATASET}")
        dataset.location = "US"
        client.create_dataset(dataset, exists_ok=True)
        table = bigquery.Table(
            f"{PROJECT}.{DATASET}.{TABLE}",
            schema=[
                bigquery.SchemaField("id", "INTEGER"),
                bigquery.SchemaField("name", "STRING"),
                bigquery.SchemaField("score", "FLOAT"),
            ],
        )
        client.create_table(table, exists_ok=True)
        errors = client.insert_rows_json(
            f"{PROJECT}.{DATASET}.{TABLE}",
            [
                {"id": 1, "name": "Alice", "score": 9.1},
                {"id": 2, "name": "Bob", "score": 7.8},
                {"id": 3, "name": "Carol", "score": 8.6},
                {"id": 4, "name": "Dan", "score": 6.4},
                {"id": 5, "name": "Eve", "score": 9.9},
            ],
        )
        assert not errors, errors
    finally:
        client.close()


def _read_arrow_via_storage(grpc_endpoint: str) -> pa.Table:
    storage = bigquery_storage_v1.BigQueryReadClient(
        credentials=AnonymousCredentials(),  # type: ignore[no-untyped-call]
        client_options=ClientOptions(api_endpoint=grpc_endpoint),
    )
    try:
        read_session = ReadSession(
            table=f"projects/{PROJECT}/datasets/{DATASET}/tables/{TABLE}",
            data_format=DataFormat.ARROW,
        )
        session = storage.create_read_session(
            parent=f"projects/{PROJECT}",
            read_session=read_session,
            max_stream_count=1,
        )
        stream = session.streams[0].name
        reader = storage.read_rows(stream)
        return reader.to_arrow(session)
    finally:
        storage.transport.close()


def main() -> None:
    settings = Settings(
        persistence_mode=PersistenceMode.EPHEMERAL,
        rest_port=0,
        grpc_port=0,
    )
    runner = ThreadedEmulator(settings)
    runner.start()
    try:
        _seed(runner.server.rest_url)

        # NOTE: bigquery_storage_v1 expects a host:port, not a URL, for
        # the gRPC endpoint.
        arrow_table = _read_arrow_via_storage(runner.server.grpc_endpoint)
        assert arrow_table.num_rows == 5

        spark = (
            SparkSession.builder.master("local[*]")
            .appName("bqemu-pyspark-example")
            .config("spark.sql.shuffle.partitions", "1")
            .getOrCreate()
        )
        try:
            df = spark.createDataFrame(arrow_table.to_pandas())
            count = df.count()
            avg = df.selectExpr("AVG(score) AS avg_score").collect()[0]["avg_score"]
            assert count == 5, f"expected 5 rows, got {count}"
            assert abs(avg - 8.36) < 0.01, f"unexpected avg_score: {avg}"
            print(f"OK: PySpark read {count} rows from bqemulator (avg_score={avg:.2f})")
        finally:
            spark.stop()
    finally:
        runner.stop()


if __name__ == "__main__":
    # PySpark is sensitive to PYSPARK_PYTHON; default to the running interpreter.
    os.environ.setdefault("PYSPARK_PYTHON", os.environ.get("PYTHON", "python"))
    main()
