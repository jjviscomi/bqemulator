"""Authorized-view helper tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bqemulator.catalog.memory_repository import MemoryCatalogRepository
from bqemulator.catalog.models import (
    AccessEntry,
    DatasetMeta,
    TableMeta,
)
from bqemulator.views.authorized_views import (
    AuthorizedViewManager,
    is_view_authorized_on,
)

pytestmark = pytest.mark.unit


def _ds(
    project_id: str,
    dataset_id: str,
    *,
    access: tuple[AccessEntry, ...] = (),
    case_insensitive: bool = False,
) -> DatasetMeta:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return DatasetMeta(
        project_id=project_id,
        dataset_id=dataset_id,
        creation_time=now,
        last_modified_time=now,
        etag='"a"',
        access_entries=access,
        is_case_insensitive=case_insensitive,
    )


class TestIsViewAuthorizedOn:
    def test_authorized_when_view_in_access(self) -> None:
        target = _ds(
            "p",
            "ds",
            access=(AccessEntry(view=("p", "view_ds", "v")),),
        )
        assert is_view_authorized_on(
            view_project="p",
            view_dataset="view_ds",
            view_table="v",
            target_dataset=target,
        )

    def test_unauthorized_with_different_view(self) -> None:
        target = _ds(
            "p",
            "ds",
            access=(AccessEntry(view=("p", "view_ds", "other_view")),),
        )
        assert not is_view_authorized_on(
            view_project="p",
            view_dataset="view_ds",
            view_table="v",
            target_dataset=target,
        )

    def test_unauthorized_when_no_access_entries(self) -> None:
        target = _ds("p", "ds")
        assert not is_view_authorized_on(
            view_project="p",
            view_dataset="view_ds",
            view_table="v",
            target_dataset=target,
        )

    def test_unauthorized_when_only_routine_entries(self) -> None:
        target = _ds(
            "p",
            "ds",
            access=(AccessEntry(routine=("p", "view_ds", "v")),),
        )
        assert not is_view_authorized_on(
            view_project="p",
            view_dataset="view_ds",
            view_table="v",
            target_dataset=target,
        )

    def test_case_sensitive_by_default(self) -> None:
        target = _ds(
            "p",
            "ds",
            access=(AccessEntry(view=("p", "VIEW_DS", "V")),),
            case_insensitive=False,
        )
        assert not is_view_authorized_on(
            view_project="p",
            view_dataset="view_ds",
            view_table="v",
            target_dataset=target,
        )

    def test_case_insensitive_dataset(self) -> None:
        target = _ds(
            "p",
            "ds",
            access=(AccessEntry(view=("p", "VIEW_DS", "V")),),
            case_insensitive=True,
        )
        assert is_view_authorized_on(
            view_project="p",
            view_dataset="view_ds",
            view_table="v",
            target_dataset=target,
        )

    def test_project_always_case_sensitive(self) -> None:
        target = _ds(
            "p",
            "ds",
            access=(AccessEntry(view=("OTHER", "view_ds", "v")),),
            case_insensitive=True,
        )
        assert not is_view_authorized_on(
            view_project="p",
            view_dataset="view_ds",
            view_table="v",
            target_dataset=target,
        )


class TestAuthorizedViewManager:
    def test_dataset_caching(self) -> None:
        catalog = MemoryCatalogRepository()
        catalog.create_dataset(
            _ds(
                "p",
                "ds",
                access=(AccessEntry(view=("p", "view_ds", "v")),),
            ),
        )
        manager = AuthorizedViewManager(catalog)
        # Multiple calls should hit the cache.
        for _ in range(3):
            assert manager.is_authorized(
                view_project="p",
                view_dataset="view_ds",
                view_table="v",
                base_project="p",
                base_dataset="ds",
            )

    def test_returns_false_when_dataset_missing(self) -> None:
        catalog = MemoryCatalogRepository()
        manager = AuthorizedViewManager(catalog)
        assert not manager.is_authorized(
            view_project="p",
            view_dataset="vds",
            view_table="v",
            base_project="p",
            base_dataset="missing",
        )

    def test_view_body_returns_view_query(self) -> None:
        catalog = MemoryCatalogRepository()
        catalog.create_dataset(_ds("p", "ds"))
        catalog.create_table(
            TableMeta(
                project_id="p",
                dataset_id="ds",
                table_id="v",
                table_type="VIEW",
                view_query="SELECT 1",
                creation_time=datetime(2026, 1, 1, tzinfo=UTC),
                last_modified_time=datetime(2026, 1, 1, tzinfo=UTC),
                etag='"v"',
            ),
        )
        manager = AuthorizedViewManager(catalog)
        assert manager.view_body("p", "ds", "v") == "SELECT 1"

    def test_view_body_returns_none_for_table(self) -> None:
        catalog = MemoryCatalogRepository()
        catalog.create_dataset(_ds("p", "ds"))
        catalog.create_table(
            TableMeta(
                project_id="p",
                dataset_id="ds",
                table_id="t",
                creation_time=datetime(2026, 1, 1, tzinfo=UTC),
                last_modified_time=datetime(2026, 1, 1, tzinfo=UTC),
                etag='"t"',
            ),
        )
        manager = AuthorizedViewManager(catalog)
        assert manager.view_body("p", "ds", "t") is None

    def test_view_body_returns_none_for_missing(self) -> None:
        catalog = MemoryCatalogRepository()
        manager = AuthorizedViewManager(catalog)
        assert manager.view_body("p", "ds", "missing") is None
