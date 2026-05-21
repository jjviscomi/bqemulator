"""REST API adapter — FastAPI.

The REST layer translates HTTP requests to domain operations. All business
logic lives in :mod:`bqemulator.catalog`, :mod:`bqemulator.jobs`, etc.; the
API layer is strictly a translation boundary.
"""

from __future__ import annotations

from bqemulator.api.app import create_app

__all__ = ["create_app"]
