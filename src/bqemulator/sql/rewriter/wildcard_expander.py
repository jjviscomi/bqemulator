"""Wildcard table expander.

Rewrites BigQuery wildcard-table references like ``FROM dataset.events_*``
into a ``UNION ALL`` of all matching tables, with ``_TABLE_SUFFIX``
injected as a literal column per source table.

When the query's WHERE clause references ``_TABLE_SUFFIX`` with an
equality, range, or ``IN (...)`` predicate, the predicate is evaluated
on the suffix list *before* building the UNION ALL, so only matching
tables appear in the expansion. This matches BigQuery's cost model
(only scanned tables bill bytes) and is essential for workloads that
query date-sharded tables across large windows.

The rewriter engages on every wildcard reference in the query and on
all qualifier shapes (1-/2-/3-part, with or without backticks; whole-
reference or per-segment backticked), so self-joins and project-
qualified references resolve consistently with bare ones.

This rewriter operates on the ORIGINAL BigQuery SQL (before SQLGlot
transpilation) because SQLGlot strips the ``*`` during AST parsing.
"""

from __future__ import annotations

from collections.abc import Callable
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bqemulator.catalog.repository import CatalogRepository


# Match a BigQuery wildcard table reference in a ``FROM`` or ``JOIN``
# clause. The pattern recognises every qualifier shape BigQuery
# accepts and the conformance corpus exercises:
#
#   FROM events_*                           (1-part — needs no rewrite)
#   FROM dataset.events_*                   (2-part bare)
#   FROM `dataset.events_*`                 (2-part, whole-ref backticked)
#   FROM `dataset`.`events_*`               (2-part, per-segment backticked)
#   FROM project.dataset.events_*           (3-part bare)
#   FROM `project.dataset.events_*`         (3-part, whole-ref backticked)
#   FROM `project`.`dataset`.`events_*`     (3-part, per-segment backticked)
#
# The optional ``project`` / ``dataset`` segments are wrapped so the
# ``project`` group is only populated when a ``dataset`` segment also
# matches — otherwise a 2-part ``dataset.events_*`` would
# pathologically capture the dataset as a project. The trailing
# ``asalias`` group captures any explicit ``AS <name>`` alias the
# author supplied so the rewriter can preserve it (avoiding
# double-aliasing the synthetic subquery).
# BigQuery project IDs permit hyphens (``test-project``,
# ``my-org-1234``); dataset and table identifiers do not. The
# ``project`` group therefore widens ``\w`` to include ``-``.
_WILDCARD_PATTERN = re.compile(
    r"(?P<kw>FROM|JOIN)\s+"
    r"(?:(?:`?(?P<project>[\w-]+)`?\.)?`?(?P<dataset>\w+)`?\.)?"
    r"`?(?P<prefix>\w+)\*`?"
    r"(?P<asalias>\s+AS\s+\w+)?",
    flags=re.IGNORECASE,
)


def expand_wildcard_tables(
    bq_sql: str,
    project_id: str,
    catalog: CatalogRepository,
) -> str:
    """Expand every wildcard table reference in ``bq_sql``.

    Returns the SQL unchanged when no wildcard reference is present
    or every reference is bare 1-part (``FROM events_*``) with no
    dataset qualifier.
    """

    def _replace(match: re.Match[str]) -> str:
        dataset = match.group("dataset")
        prefix = match.group("prefix")
        keyword = match.group("kw")
        asalias = match.group("asalias") or ""
        sql_project = match.group("project")

        # Bare ``FROM events_*`` (no dataset) — no expansion possible.
        if not dataset:
            return match.group(0)

        lookup_project = sql_project or project_id
        # Storage-level lookup so DDL-created tables (``CREATE TABLE …
        # AS SELECT`` via the SQL pipeline) are visible. The catalog's
        # cached ``list_tables`` only sees REST-registered tables.
        all_table_ids = catalog.list_storage_tables(lookup_project, dataset)
        matching_ids = sorted(
            tid for tid in all_table_ids if tid.startswith(prefix) and tid != prefix
        )
        if not matching_ids:
            return match.group(0)

        # Restrict the matching set using any _TABLE_SUFFIX predicate
        # in the outer WHERE clause (pre-expansion pushdown).
        matching_ids = _apply_table_suffix_pushdown(matching_ids, prefix, bq_sql)

        if not matching_ids:
            # Emit a subquery that produces the full schema shape but
            # no rows, so downstream SQL stays syntactically valid even
            # when the predicate excludes every matching table.
            subquery = "(SELECT NULL AS _TABLE_SUFFIX WHERE FALSE)"
        else:
            # Per-table fully-qualified reference, backticked as one
            # unit so hyphenated project ids (``test-project``) parse
            # correctly through SQLGlot and DuckDB. Without the
            # backticks, ``test-project.ds.tbl`` lexes as
            # ``(test - project).ds.tbl``.
            qualifier = f"{sql_project}.{dataset}" if sql_project else dataset
            union_parts: list[str] = []
            for tid in matching_ids:
                suffix = tid[len(prefix) :]
                ref = f"`{qualifier}.{tid}`" if sql_project else f"{qualifier}.{tid}"
                union_parts.append(
                    f"SELECT *, '{suffix}' AS _TABLE_SUFFIX FROM {ref}",
                )
            subquery = "(" + " UNION ALL ".join(union_parts) + ")"

        if asalias:
            return f"{keyword} {subquery}{asalias}"
        return f"{keyword} {subquery} AS __wildcard"

    return _WILDCARD_PATTERN.sub(_replace, bq_sql)


def _apply_table_suffix_pushdown(
    table_ids: list[str],
    prefix: str,
    bq_sql: str,
) -> list[str]:
    """Narrow the match set using any ``_TABLE_SUFFIX`` predicate.

    Recognises:

    - ``_TABLE_SUFFIX = 'x'``
    - ``_TABLE_SUFFIX IN ('x', 'y', 'z')``
    - ``_TABLE_SUFFIX BETWEEN 'x' AND 'y'``
    - ``_TABLE_SUFFIX >= 'x'`` / ``<=`` / ``<`` / ``>``

    Unknown predicate shapes fall through to the full match set; the
    resulting SQL still filters correctly at row level, it's just not
    pruned at plan time.
    """
    upper = bq_sql.upper()
    if "_TABLE_SUFFIX" not in upper:
        return table_ids

    pairs = [(tid[len(prefix) :], tid) for tid in table_ids]

    # _TABLE_SUFFIX = 'x'
    m = re.search(
        r"_TABLE_SUFFIX\s*=\s*(['\"])([^'\"]+)\1",
        bq_sql,
        flags=re.IGNORECASE,
    )
    if m:
        target = m.group(2)
        return [tid for s, tid in pairs if s == target]

    # _TABLE_SUFFIX IN ('a', 'b')
    m = re.search(
        r"_TABLE_SUFFIX\s+IN\s*\(([^)]+)\)",
        bq_sql,
        flags=re.IGNORECASE,
    )
    if m:
        values = re.findall(r"['\"]([^'\"]+)['\"]", m.group(1))
        return [tid for s, tid in pairs if s in values]

    # _TABLE_SUFFIX BETWEEN 'a' AND 'b'
    m = re.search(
        r"_TABLE_SUFFIX\s+BETWEEN\s+(['\"])([^'\"]+)\1\s+AND\s+(['\"])([^'\"]+)\3",
        bq_sql,
        flags=re.IGNORECASE,
    )
    if m:
        low = m.group(2)
        high = m.group(4)
        return [tid for s, tid in pairs if low <= s <= high]

    # Single-sided inequality: >= / > / <= / <
    compare_fns: list[tuple[str, Callable[[str, str], bool]]] = [
        (r">=", lambda s, v: s >= v),
        (r"<=", lambda s, v: s <= v),
        (r">", lambda s, v: s > v),
        (r"<", lambda s, v: s < v),
    ]
    for op_pattern, op_fn in compare_fns:
        m = re.search(
            rf"_TABLE_SUFFIX\s*{op_pattern}\s*(['\"])([^'\"]+)\1",
            bq_sql,
            flags=re.IGNORECASE,
        )
        if m:
            target = m.group(2)
            return [tid for s, tid in pairs if op_fn(s, target)]

    return table_ids


__all__ = ["expand_wildcard_tables"]
