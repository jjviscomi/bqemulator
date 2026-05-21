"""Unit tests for gRPC interceptor helpers."""

from __future__ import annotations

import pytest

from bqemulator.grpc_api.interceptors import _split_method, _wrap_handler

pytestmark = pytest.mark.unit


class TestSplitMethod:
    def test_standard_path(self) -> None:
        assert _split_method("/google.cloud.bigquery.v2/Query") == (
            "google.cloud.bigquery.v2",
            "Query",
        )

    def test_missing_slash_returns_raw(self) -> None:
        service, method = _split_method("weird")
        assert service == "weird"
        assert method == ""

    def test_empty_string(self) -> None:
        service, method = _split_method("")
        assert service == ""
        assert method == ""


class TestWrapHandler:
    def test_non_unary_handler_passes_through(self) -> None:
        # A mock handler with unary_unary = None — we only wrap unary-unary.
        class _FakeHandler:
            unary_unary = None
            unary_stream = None
            stream_unary = None
            stream_stream = None
            request_deserializer = None
            response_serializer = None

        handler = _FakeHandler()
        result = _wrap_handler(handler)  # type: ignore[arg-type]
        assert result is handler
