"""BigQuery REST API v2 route modules.

Each module corresponds to a BigQuery REST resource type. Routes are
mounted in :func:`bqemulator.api.app.create_app` under the
``/bigquery/v2`` prefix.
"""

from __future__ import annotations
