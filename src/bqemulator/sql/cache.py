"""Query result cache.

An in-memory LRU cache that stores the ``pyarrow.Table`` result of
deterministic queries. Cache entries are keyed by ``(project_id,
normalized_sql, parameter_hash)`` and expire after the configured TTL.

Invalidation is event-driven: ``TableDataChanged`` events clear all
cache entries that reference the modified table.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import time
from typing import Any

import pyarrow as pa

from bqemulator.observability.logging_ import get_logger

_log = get_logger(__name__)

_MAX_ENTRIES = 1024


@dataclass(slots=True)
class _CacheEntry:
    table: pa.Table
    schema_fields: list[str]
    referenced_tables: frozenset[str]
    created_at: float  # monotonic time


class QueryCache:
    """In-memory, TTL-based, invalidation-aware query result cache.

    Thread / task safety: reads and writes are safe from a single
    asyncio task (which is how bqemulator's event loop calls us).
    """

    def __init__(self, ttl_seconds: int = 86400, max_entries: int = _MAX_ENTRIES) -> None:
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._store: dict[str, _CacheEntry] = {}

    @property
    def enabled(self) -> bool:
        """Return ``True`` if caching is active (TTL > 0)."""
        return self._ttl > 0

    def get(
        self,
        project_id: str,
        sql: str,
        parameters: list[Any] | None = None,
    ) -> pa.Table | None:
        """Return a cached result, or ``None`` on miss / expiry."""
        if not self.enabled:
            return None
        key = self._key(project_id, sql, parameters)
        entry = self._store.get(key)
        if entry is None:
            return None
        if time.monotonic() - entry.created_at > self._ttl:
            del self._store[key]
            return None
        return entry.table

    def put(
        self,
        project_id: str,
        sql: str,
        parameters: list[Any] | None,
        result: pa.Table,
        referenced_tables: frozenset[str] | None = None,
    ) -> None:
        """Store a query result in the cache."""
        if not self.enabled:
            return
        key = self._key(project_id, sql, parameters)

        # Evict oldest entry if at capacity.
        if len(self._store) >= self._max_entries:
            oldest_key = min(self._store, key=lambda k: self._store[k].created_at)
            del self._store[oldest_key]

        self._store[key] = _CacheEntry(
            table=result,
            schema_fields=[f.name for f in result.schema],
            referenced_tables=referenced_tables or frozenset(),
            created_at=time.monotonic(),
        )

    def invalidate_table(self, qualified_table: str) -> int:
        """Remove all cache entries that reference ``qualified_table``.

        Args:
            qualified_table: ``"project.dataset.table"`` form.

        Returns:
            Number of entries evicted.
        """
        keys_to_remove = [
            k for k, entry in self._store.items() if qualified_table in entry.referenced_tables
        ]
        for k in keys_to_remove:
            del self._store[k]
        if keys_to_remove:
            _log.debug(
                "query_cache.invalidated",
                table=qualified_table,
                evicted=len(keys_to_remove),
            )
        return len(keys_to_remove)

    def clear(self) -> None:
        """Drop all cached entries."""
        self._store.clear()

    @property
    def size(self) -> int:
        """Number of live entries."""
        return len(self._store)

    @staticmethod
    def _key(project_id: str, sql: str, parameters: list[Any] | None) -> str:
        h = hashlib.sha256()
        h.update(project_id.encode())
        h.update(sql.encode())
        if parameters:
            h.update(str(parameters).encode())
        return h.hexdigest()


__all__ = ["QueryCache"]
