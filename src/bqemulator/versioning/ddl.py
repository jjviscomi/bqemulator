"""DDL router for versioning statements.

``CREATE SNAPSHOT TABLE``, ``CREATE TABLE ... CLONE``,
``CREATE MATERIALIZED VIEW``, ``REFRESH MATERIALIZED VIEW``, ``DROP
SNAPSHOT TABLE``, and ``DROP MATERIALIZED VIEW`` are versioning DDL
statements that SQLGlot either round-trips opaquely or rejects. They
never go through the regular SQL translator — instead, the job
executor asks this module whether the incoming statement is a
versioning DDL, and if so delegates the whole job to the matching
manager.

The detection is regex-based (similar in spirit to
``sql/rewriter/information_schema.py``) rather than AST-based so we
can intercept before the translator rejects the statement.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import TYPE_CHECKING

from bqemulator.domain.errors import InvalidQueryError

if TYPE_CHECKING:  # pragma: no cover
    from bqemulator.api.dependencies import AppContext


class VersioningDDLKind(StrEnum):
    """Types of Phase-7 DDL that bypass the SQL translator."""

    CREATE_SNAPSHOT = "CREATE_SNAPSHOT"
    DROP_SNAPSHOT = "DROP_SNAPSHOT"
    CREATE_CLONE = "CREATE_CLONE"
    CREATE_MATERIALIZED_VIEW = "CREATE_MATERIALIZED_VIEW"
    DROP_MATERIALIZED_VIEW = "DROP_MATERIALIZED_VIEW"
    REFRESH_MATERIALIZED_VIEW = "REFRESH_MATERIALIZED_VIEW"


# Patterns compiled with ``re.IGNORECASE | re.DOTALL`` so they work
# against arbitrarily-cased multi-line DDL bodies.
_ID = r"`?([A-Za-z0-9_\-]+)`?"
_QUALIFIED_NAME = rf"(?:{_ID}\s*\.\s*)?(?:{_ID}\s*\.\s*)?{_ID}"

_CREATE_SNAPSHOT_RE = re.compile(
    rf"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?SNAPSHOT\s+TABLE\s+(?P<dest>{_QUALIFIED_NAME})"
    rf"\s+(?:CLONE|COPY)\s+(?P<src>{_QUALIFIED_NAME})"
    r"\s*;?\s*$",
    flags=re.IGNORECASE | re.DOTALL,
)
_DROP_SNAPSHOT_RE = re.compile(
    rf"^\s*DROP\s+SNAPSHOT\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?P<dest>{_QUALIFIED_NAME})\s*;?\s*$",
    flags=re.IGNORECASE | re.DOTALL,
)
_CREATE_CLONE_RE = re.compile(
    rf"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?TABLE\s+(?P<dest>{_QUALIFIED_NAME})"
    rf"\s+CLONE\s+(?P<src>{_QUALIFIED_NAME})"
    r"\s*;?\s*$",
    flags=re.IGNORECASE | re.DOTALL,
)
_CREATE_MATERIALIZED_VIEW_RE = re.compile(
    rf"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?MATERIALIZED\s+VIEW\s+(?P<dest>{_QUALIFIED_NAME})"
    r"\s+AS\s+(?P<query>.+?)\s*;?\s*$",
    flags=re.IGNORECASE | re.DOTALL,
)
_DROP_MATERIALIZED_VIEW_RE = re.compile(
    rf"^\s*DROP\s+MATERIALIZED\s+VIEW\s+(?:IF\s+EXISTS\s+)?(?P<dest>{_QUALIFIED_NAME})\s*;?\s*$",
    flags=re.IGNORECASE | re.DOTALL,
)
_REFRESH_MATERIALIZED_VIEW_RE = re.compile(
    rf"^\s*CALL\s+BQ\.REFRESH_MATERIALIZED_VIEW\s*\(\s*['\"](?P<dest>[^'\"]+)['\"]\s*\)\s*;?\s*$"
    rf"|^\s*REFRESH\s+MATERIALIZED\s+VIEW\s+(?P<dest2>{_QUALIFIED_NAME})\s*;?\s*$",
    flags=re.IGNORECASE | re.DOTALL,
)


@dataclass(slots=True, frozen=True)
class VersioningDDL:
    """Parsed Phase-7 DDL statement."""

    kind: VersioningDDLKind
    target_project: str
    target_dataset: str
    target_table: str
    source_project: str | None = None
    source_dataset: str | None = None
    source_table: str | None = None
    view_query: str | None = None


def is_versioning_ddl(sql: str) -> bool:
    """Quick check — returns True if ``sql`` might be a Phase-7 DDL."""
    upper = sql.upper()
    return (
        "SNAPSHOT" in upper
        or "CLONE" in upper
        or "MATERIALIZED VIEW" in upper
        or "REFRESH_MATERIALIZED_VIEW" in upper
    )


class VersioningDDLRouter:
    """Parses and routes Phase-7 DDL."""

    def __init__(self, default_project_id: str) -> None:
        self._project_id = default_project_id

    def parse(self, sql: str) -> VersioningDDL | None:
        """Parse ``sql`` into a :class:`VersioningDDL`, or return ``None``."""
        sql = sql.strip().rstrip(";").strip()

        m = _CREATE_SNAPSHOT_RE.match(sql)
        if m is not None:
            dest_p, dest_d, dest_t = self._split_qualified(m.group("dest"))
            src_p, src_d, src_t = self._split_qualified(m.group("src"))
            return VersioningDDL(
                kind=VersioningDDLKind.CREATE_SNAPSHOT,
                target_project=dest_p,
                target_dataset=dest_d,
                target_table=dest_t,
                source_project=src_p,
                source_dataset=src_d,
                source_table=src_t,
            )

        m = _DROP_SNAPSHOT_RE.match(sql)
        if m is not None:
            dest_p, dest_d, dest_t = self._split_qualified(m.group("dest"))
            return VersioningDDL(
                kind=VersioningDDLKind.DROP_SNAPSHOT,
                target_project=dest_p,
                target_dataset=dest_d,
                target_table=dest_t,
            )

        m = _CREATE_CLONE_RE.match(sql)
        if m is not None:
            dest_p, dest_d, dest_t = self._split_qualified(m.group("dest"))
            src_p, src_d, src_t = self._split_qualified(m.group("src"))
            return VersioningDDL(
                kind=VersioningDDLKind.CREATE_CLONE,
                target_project=dest_p,
                target_dataset=dest_d,
                target_table=dest_t,
                source_project=src_p,
                source_dataset=src_d,
                source_table=src_t,
            )

        m = _CREATE_MATERIALIZED_VIEW_RE.match(sql)
        if m is not None:
            dest_p, dest_d, dest_t = self._split_qualified(m.group("dest"))
            return VersioningDDL(
                kind=VersioningDDLKind.CREATE_MATERIALIZED_VIEW,
                target_project=dest_p,
                target_dataset=dest_d,
                target_table=dest_t,
                view_query=m.group("query").strip(),
            )

        m = _DROP_MATERIALIZED_VIEW_RE.match(sql)
        if m is not None:
            dest_p, dest_d, dest_t = self._split_qualified(m.group("dest"))
            return VersioningDDL(
                kind=VersioningDDLKind.DROP_MATERIALIZED_VIEW,
                target_project=dest_p,
                target_dataset=dest_d,
                target_table=dest_t,
            )

        m = _REFRESH_MATERIALIZED_VIEW_RE.match(sql)
        if m is not None:
            raw = m.group("dest") or m.group("dest2")
            dest_p, dest_d, dest_t = self._split_qualified(raw)
            return VersioningDDL(
                kind=VersioningDDLKind.REFRESH_MATERIALIZED_VIEW,
                target_project=dest_p,
                target_dataset=dest_d,
                target_table=dest_t,
            )

        return None

    def _split_qualified(self, raw: str) -> tuple[str, str, str]:
        """Split ``[project.]dataset.table`` into its three parts."""
        # Drop backticks the regex may have retained.
        cleaned = raw.replace("`", "")
        parts = [p.strip() for p in cleaned.split(".") if p.strip()]
        if len(parts) == _PARTS_FULLY_QUALIFIED:
            return parts[0], parts[1], parts[2]
        if len(parts) == _PARTS_DATASET_QUALIFIED:
            return self._project_id, parts[0], parts[1]
        if len(parts) == _PARTS_BARE:
            raise InvalidQueryError(
                "Versioning DDL requires a fully-qualified or dataset-qualified table reference",
            )
        raise InvalidQueryError(
            f"Could not parse versioning DDL target: {raw!r}",
        )


_PARTS_FULLY_QUALIFIED = 3
_PARTS_DATASET_QUALIFIED = 2
_PARTS_BARE = 1


async def execute_versioning_ddl(
    ddl: VersioningDDL,
    ctx: AppContext,
) -> None:
    """Dispatch a parsed Phase-7 DDL to the matching manager.

    The caller holds neither the write lock nor the snapshot manager;
    every manager acquires its own resources as needed.
    """
    from bqemulator.versioning.clone import CloneManager
    from bqemulator.versioning.materialized_views import MaterializedViewManager
    from bqemulator.versioning.snapshot_table import SnapshotTableManager

    if ddl.kind is VersioningDDLKind.CREATE_SNAPSHOT:
        assert ddl.source_project is not None  # noqa: S101
        assert ddl.source_dataset is not None  # noqa: S101
        assert ddl.source_table is not None  # noqa: S101
        await SnapshotTableManager(ctx).create(
            ddl.target_project,
            ddl.target_dataset,
            ddl.target_table,
            ddl.source_project,
            ddl.source_dataset,
            ddl.source_table,
        )
    elif ddl.kind is VersioningDDLKind.DROP_SNAPSHOT:
        await SnapshotTableManager(ctx).drop(
            ddl.target_project,
            ddl.target_dataset,
            ddl.target_table,
        )
    elif ddl.kind is VersioningDDLKind.CREATE_CLONE:
        assert ddl.source_project is not None  # noqa: S101
        assert ddl.source_dataset is not None  # noqa: S101
        assert ddl.source_table is not None  # noqa: S101
        await CloneManager(ctx).create(
            ddl.target_project,
            ddl.target_dataset,
            ddl.target_table,
            ddl.source_project,
            ddl.source_dataset,
            ddl.source_table,
        )
    elif ddl.kind is VersioningDDLKind.CREATE_MATERIALIZED_VIEW:
        assert ddl.view_query is not None  # noqa: S101
        await MaterializedViewManager(ctx).create(
            ddl.target_project,
            ddl.target_dataset,
            ddl.target_table,
            ddl.view_query,
        )
    elif ddl.kind is VersioningDDLKind.DROP_MATERIALIZED_VIEW:
        await MaterializedViewManager(ctx).drop(
            ddl.target_project,
            ddl.target_dataset,
            ddl.target_table,
        )
    elif ddl.kind is VersioningDDLKind.REFRESH_MATERIALIZED_VIEW:
        await MaterializedViewManager(ctx).refresh(
            ddl.target_project,
            ddl.target_dataset,
            ddl.target_table,
        )
    else:  # pragma: no cover — all branches enumerated
        raise InvalidQueryError(f"Unknown Phase-7 DDL: {ddl.kind}")


__all__ = [
    "VersioningDDL",
    "VersioningDDLKind",
    "VersioningDDLRouter",
    "execute_versioning_ddl",
    "is_versioning_ddl",
]
