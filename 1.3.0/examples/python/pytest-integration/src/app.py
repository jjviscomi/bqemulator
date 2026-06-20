"""Tiny Flask service that reads customers from BigQuery.

Production-shaped: knows nothing about the emulator. The
``bigquery.Client`` is supplied via Flask's ``config`` dict so tests can
inject an emulator-backed client without monkeypatching.
"""

from __future__ import annotations

from typing import Any

from flask import Flask, jsonify
from google.cloud import bigquery


def create_app(client: bigquery.Client, *, dataset: str = "demo") -> Flask:
    """Build the Flask app bound to a specific ``bigquery.Client``.

    Args:
        client: A ready-to-use BigQuery client. The app does not own its
            lifecycle.
        dataset: Dataset that contains the ``customers`` table.
    """
    app = Flask(__name__)
    app.config["BIGQUERY_CLIENT"] = client
    app.config["BIGQUERY_DATASET"] = dataset

    @app.route("/customers")
    def list_customers() -> Any:
        bq = app.config["BIGQUERY_CLIENT"]
        ds = app.config["BIGQUERY_DATASET"]
        project = bq.project
        sql = f"SELECT id, name FROM `{project}.{ds}.customers` ORDER BY id"
        rows = [dict(row) for row in bq.query(sql).result()]
        return jsonify(rows)

    @app.route("/healthz")
    def healthz() -> tuple[str, int]:
        return "ok", 200

    return app
