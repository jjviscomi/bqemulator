"""Tiny Flask service inside the compose stack."""

from __future__ import annotations

import os

from flask import Flask, jsonify
from google.api_core.client_options import ClientOptions
from google.auth.credentials import AnonymousCredentials
from google.cloud import bigquery

PROJECT = os.environ.get("BQ_PROJECT", "bqemu-demo")
DATASET = os.environ.get("BQ_DATASET", "full_stack_demo")
REST_URL = os.environ.get("BQEMU_REST_URL", "http://bqemulator:9050")

app = Flask(__name__)


def _client() -> bigquery.Client:
    return bigquery.Client(
        project=PROJECT,
        credentials=AnonymousCredentials(),  # type: ignore[no-untyped-call]
        client_options=ClientOptions(api_endpoint=REST_URL),
    )


@app.route("/healthz")
def healthz() -> tuple[str, int]:
    return "ok", 200


@app.route("/customers")
def customers() -> object:
    client = _client()
    try:
        rows = list(
            client.query(
                f"SELECT id, name FROM `{PROJECT}.{DATASET}.customers` ORDER BY id"
            ).result()
        )
        return jsonify([{"id": r.id, "name": r.name} for r in rows])
    finally:
        client.close()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)  # noqa: S104
