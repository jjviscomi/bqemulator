"""Rewrite BigQuery's ``_PARTITIONDATE`` / ``_PARTITIONTIME`` pseudo-columns.

BigQuery's ingestion-time-partitioned tables expose two pseudo-columns
that don't physically exist on disk:

* ``_PARTITIONDATE`` — the partition's date (UTC), as ``DATE``.
* ``_PARTITIONTIME`` — the partition's timestamp (UTC midnight of the
  partition date), as ``TIMESTAMP``.

The emulator's storage layer (DuckDB) has no ingestion-time
partitioning, so the rows aren't tagged with a partition timestamp.
The closure: rewrite references to the pseudo-columns to
``CURRENT_DATE()`` / ``CURRENT_TIMESTAMP()`` — every row "lives" in
today's partition. This matches BigQuery's behaviour for rows
inserted right now, and the conformance fixtures' filters
(``WHERE _PARTITIONDATE > DATE '1900-01-01'``) all evaluate correctly
against today's date.

The rewrite is conservative: it only touches the bare identifier
spellings (``_PARTITIONDATE`` / ``_PARTITIONTIME``), case-insensitive,
word-bounded. References inside backtick-quoted strings or string
literals are left alone — they're already not pseudo-column
references.
"""

from __future__ import annotations

import re

#: Match a bare ``_PARTITIONDATE`` or ``_PARTITIONTIME`` identifier.
#: The leading word boundary stops mid-identifier matches; the
#: trailing word boundary stops eager matches of column names that
#: happen to start with the pseudo-column name.
_PARTITION_PSEUDO_COLUMN_RE = re.compile(
    r"\b(_PARTITIONDATE|_PARTITIONTIME)\b",
    re.IGNORECASE,
)

#: BigQuery → DuckDB replacement for each pseudo-column.
_REPLACEMENTS: dict[str, str] = {
    "_PARTITIONDATE": "CURRENT_DATE()",
    "_PARTITIONTIME": "CURRENT_TIMESTAMP()",
}


def rewrite_partition_pseudo_columns(bq_sql: str) -> str:
    """Return ``bq_sql`` with the partition pseudo-columns substituted.

    Replaces every occurrence of ``_PARTITIONDATE`` /
    ``_PARTITIONTIME`` (case-insensitive, word-bounded) with the
    matching ``CURRENT_DATE()`` / ``CURRENT_TIMESTAMP()`` call.
    Returns ``bq_sql`` unchanged when no pseudo-columns appear.
    """

    def _replace(match: re.Match[str]) -> str:
        return _REPLACEMENTS[match.group(1).upper()]

    return _PARTITION_PSEUDO_COLUMN_RE.sub(_replace, bq_sql)


__all__ = ["rewrite_partition_pseudo_columns"]
