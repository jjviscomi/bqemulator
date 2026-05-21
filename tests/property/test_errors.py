"""Property tests for domain-error rendering.

Invariants:

* Every DomainError subclass renders a valid BigQuery ErrorProto shape:
  the top-level dict has exactly one key ("error"), the inner dict has
  the four required keys, and "errors" is a non-empty list.
* The HTTP status in the rendered payload matches the class attribute.
* Round-tripping an arbitrary message through the error reconstructs
  the message on the rendered output.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st
import pytest

from bqemulator.domain.errors import (
    AlreadyExistsError,
    DomainError,
    InternalError,
    InvalidQueryError,
    NotFoundError,
    PermissionDeniedError,
    QuotaExceededError,
    UnsupportedFeatureError,
    ValidationError,
)

pytestmark = pytest.mark.property

_ERROR_CLASSES: list[type[DomainError]] = [
    InvalidQueryError,
    ValidationError,
    NotFoundError,
    AlreadyExistsError,
    PermissionDeniedError,
    QuotaExceededError,
    UnsupportedFeatureError,
    InternalError,
]


@given(
    error_cls=st.sampled_from(_ERROR_CLASSES),
    message=st.text(min_size=1, max_size=200),
)
def test_render_shape_is_stable(
    error_cls: type[DomainError],
    message: str,
) -> None:
    err = error_cls(message)
    payload = err.to_bigquery_error()

    # Exactly one top-level key, named "error".
    assert set(payload.keys()) == {"error"}

    inner = payload["error"]
    assert {"code", "message", "errors", "status"} <= inner.keys()
    assert isinstance(inner["errors"], list)
    assert len(inner["errors"]) >= 1

    # HTTP status matches class attribute.
    assert inner["code"] == error_cls.http_status
    assert inner["status"] == error_cls.grpc_status_name

    # Each error detail has required keys.
    for detail in inner["errors"]:
        assert {"domain", "reason", "message"} <= detail.keys()


@given(message=st.text(min_size=1, max_size=200))
def test_message_round_trips(message: str) -> None:
    err = InvalidQueryError(message)
    rendered = err.to_bigquery_error()
    assert rendered["error"]["message"] == message
