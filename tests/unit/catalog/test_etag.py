"""Tests for ETag generation."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bqemulator.catalog.etag import generate_etag

pytestmark = pytest.mark.unit


class TestGenerateEtag:
    def test_returns_quoted_string(self) -> None:
        etag = generate_etag("p", "d", "t", "2026-04-15")
        assert etag.startswith('"')
        assert etag.endswith('"')

    def test_deterministic(self) -> None:
        a = generate_etag("p", "d", "t")
        b = generate_etag("p", "d", "t")
        assert a == b

    def test_changes_on_different_input(self) -> None:
        a = generate_etag("p", "d", "t", "2026-04-15T00:00:00")
        b = generate_etag("p", "d", "t", "2026-04-15T00:00:01")
        assert a != b

    def test_accepts_datetime(self) -> None:
        etag = generate_etag("p", "d", datetime(2026, 4, 15, tzinfo=UTC))
        assert len(etag) > 4

    def test_accepts_ints(self) -> None:
        etag = generate_etag("p", 42, "t")
        assert '"' in etag

    def test_length(self) -> None:
        etag = generate_etag("p", "d", "t")
        # 16 hex chars + 2 quotes = 18
        assert len(etag) == 18
