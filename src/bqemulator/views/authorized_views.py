"""Authorized view detection.

The bypass model — captured in
[ADR 0018](../../docs/adr/0018-caller-identity-and-row-access-enforcement.md)
— says: when a query expands a view body and the body references a
protected base table, look at the *base table's dataset's* ``access``
array. If any entry is a ``view`` reference matching the outer view,
the row access policies on that base table are bypassed for this
read.

The two helpers below isolate that decision:

* :func:`is_view_authorized_on` is a pure function the rewriter calls
  per (view, base-dataset) pair.
* :class:`AuthorizedViewManager` caches the dataset look-up across
  multiple base-table checks in a single rewrite pass.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from bqemulator.catalog.models import DatasetMeta
    from bqemulator.catalog.repository import CatalogRepository


def is_view_authorized_on(
    *,
    view_project: str,
    view_dataset: str,
    view_table: str,
    target_dataset: DatasetMeta,
) -> bool:
    """Return True iff ``view`` is an authorized reader of ``target_dataset``.

    BigQuery's authorization model: the *base table's dataset* lists
    the authorized views in its ``access`` array. The check is
    project-aware — a view from a different project is only
    authorized when the access entry's ``view`` reference includes
    that project. We lower-case the dataset and table-id segments
    only when comparing, never when emitting; BigQuery is
    case-sensitive on dataset and table ids unless the dataset is
    flagged ``isCaseInsensitive``.
    """
    case_insensitive = target_dataset.is_case_insensitive
    for entry in target_dataset.access_entries:
        if entry.view is None:
            continue
        proj, dataset, table = entry.view
        if not _id_equal(proj, view_project, case_insensitive=False):
            continue
        if not _id_equal(dataset, view_dataset, case_insensitive=case_insensitive):
            continue
        if not _id_equal(table, view_table, case_insensitive=case_insensitive):
            continue
        return True
    return False


def _id_equal(left: str, right: str, *, case_insensitive: bool) -> bool:
    if case_insensitive:
        return left.lower() == right.lower()
    return left == right


class AuthorizedViewManager:
    """Cache dataset look-ups across a single rewrite pass.

    The rewriter creates one manager per query. Each call to
    :meth:`is_authorized` may consult the catalog for the *base
    table's* dataset, and we cache the result so a query that joins
    two protected tables in the same dataset only hits the catalog
    once.
    """

    def __init__(self, catalog: CatalogRepository) -> None:
        self._catalog = catalog
        self._dataset_cache: dict[tuple[str, str], DatasetMeta | None] = {}
        self._view_meta_cache: dict[tuple[str, str, str], _MaybeView] = {}

    def is_authorized(
        self,
        *,
        view_project: str,
        view_dataset: str,
        view_table: str,
        base_project: str,
        base_dataset: str,
    ) -> bool:
        """Return True iff the named view authorizes a read of the base dataset."""
        target = self._dataset(base_project, base_dataset)
        if target is None:
            return False
        return is_view_authorized_on(
            view_project=view_project,
            view_dataset=view_dataset,
            view_table=view_table,
            target_dataset=target,
        )

    def _dataset(self, project_id: str, dataset_id: str) -> DatasetMeta | None:
        key = (project_id, dataset_id)
        if key in self._dataset_cache:
            return self._dataset_cache[key]
        meta = self._catalog.get_dataset(project_id, dataset_id)
        self._dataset_cache[key] = meta
        return meta

    def view_body(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
    ) -> str | None:
        """Return the BigQuery SQL body of a view, or ``None`` if absent.

        Result is cached per (project, dataset, table). Returns
        ``None`` for tables that are not VIEWs (so the caller can
        decide whether to apply RAP directly).
        """
        key = (project_id, dataset_id, table_id)
        cached = self._view_meta_cache.get(key)
        if cached is not None:
            return cached.body
        meta = self._catalog.get_table(project_id, dataset_id, table_id)
        body = (
            meta.view_query
            if meta is not None and meta.table_type == "VIEW" and meta.view_query
            else None
        )
        self._view_meta_cache[key] = _MaybeView(body=body)
        return body


class _MaybeView:
    """Tiny sentinel so ``None`` is a real cache hit, not a miss."""

    __slots__ = ("body",)

    def __init__(self, *, body: str | None) -> None:
        self.body = body


__all__ = ["AuthorizedViewManager", "is_view_authorized_on"]
