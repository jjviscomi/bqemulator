"""Tests for the query result cache."""

from __future__ import annotations

import pyarrow as pa
import pytest

from bqemulator.sql.cache import QueryCache

pytestmark = pytest.mark.unit


@pytest.fixture
def cache() -> QueryCache:
    return QueryCache(ttl_seconds=3600)


@pytest.fixture
def sample_table() -> pa.Table:
    return pa.table({"x": [1, 2, 3]})


class TestCacheBasics:
    def test_miss_returns_none(self, cache: QueryCache) -> None:
        assert cache.get("p", "SELECT 1") is None

    def test_put_then_get(self, cache: QueryCache, sample_table: pa.Table) -> None:
        cache.put("p", "SELECT 1", None, sample_table)
        result = cache.get("p", "SELECT 1")
        assert result is not None
        assert result.num_rows == 3

    def test_different_sql_misses(self, cache: QueryCache, sample_table: pa.Table) -> None:
        cache.put("p", "SELECT 1", None, sample_table)
        assert cache.get("p", "SELECT 2") is None

    def test_different_project_misses(self, cache: QueryCache, sample_table: pa.Table) -> None:
        cache.put("p1", "SELECT 1", None, sample_table)
        assert cache.get("p2", "SELECT 1") is None

    def test_different_params_misses(self, cache: QueryCache, sample_table: pa.Table) -> None:
        cache.put("p", "SELECT ?", [1], sample_table)
        assert cache.get("p", "SELECT ?", [2]) is None


class TestInvalidation:
    def test_invalidate_evicts_matching_entries(
        self,
        cache: QueryCache,
        sample_table: pa.Table,
    ) -> None:
        cache.put(
            "p",
            "SELECT * FROM sales.orders",
            None,
            sample_table,
            referenced_tables=frozenset({"p.sales.orders"}),
        )
        evicted = cache.invalidate_table("p.sales.orders")
        assert evicted == 1
        assert cache.get("p", "SELECT * FROM sales.orders") is None

    def test_invalidation_spares_unrelated(
        self,
        cache: QueryCache,
        sample_table: pa.Table,
    ) -> None:
        cache.put("p", "q1", None, sample_table, referenced_tables=frozenset({"p.a.t1"}))
        cache.put("p", "q2", None, sample_table, referenced_tables=frozenset({"p.a.t2"}))
        cache.invalidate_table("p.a.t1")
        assert cache.get("p", "q1") is None
        assert cache.get("p", "q2") is not None

    def test_clear(self, cache: QueryCache, sample_table: pa.Table) -> None:
        cache.put("p", "q1", None, sample_table)
        cache.put("p", "q2", None, sample_table)
        cache.clear()
        assert cache.size == 0


class TestDisabled:
    def test_zero_ttl_disables(self, sample_table: pa.Table) -> None:
        cache = QueryCache(ttl_seconds=0)
        assert not cache.enabled
        cache.put("p", "SELECT 1", None, sample_table)
        assert cache.get("p", "SELECT 1") is None


class TestEviction:
    def test_max_entries_evicts_oldest(self, sample_table: pa.Table) -> None:
        cache = QueryCache(ttl_seconds=3600, max_entries=2)
        cache.put("p", "q1", None, sample_table)
        cache.put("p", "q2", None, sample_table)
        cache.put("p", "q3", None, sample_table)  # should evict q1
        assert cache.size == 2
        assert cache.get("p", "q1") is None
        assert cache.get("p", "q3") is not None
