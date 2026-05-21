"""Unit tests for ``bqemulator.commands.import_project``.

We mock the ``google.cloud.bigquery`` client surface so the test runs
without credentials and without network access.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import types
from unittest import mock

import pytest

from bqemulator.catalog.duckdb_repository import DuckDBCatalogRepository
from bqemulator.commands.import_project import ImportSummary, run_import
from bqemulator.config import PersistenceMode, Settings
from bqemulator.storage.engine import DuckDBEngine

pytestmark = pytest.mark.unit


def _make_fake_field(name: str, ftype: str, mode: str = "NULLABLE") -> object:
    """Build a stand-in for ``google.cloud.bigquery.SchemaField``."""
    return types.SimpleNamespace(
        name=name,
        field_type=ftype,
        mode=mode,
        fields=(),
        description=None,
    )


def _make_fake_dataset_ref(project: str, dataset_id: str) -> object:
    return types.SimpleNamespace(
        dataset_id=dataset_id,
        reference=types.SimpleNamespace(project=project, dataset_id=dataset_id),
    )


def _make_fake_dataset(project: str, dataset_id: str) -> object:
    return types.SimpleNamespace(
        dataset_id=dataset_id,
        project=project,
        reference=types.SimpleNamespace(project=project, dataset_id=dataset_id),
        friendly_name="Friendly",
        description="desc",
        labels={"env": "test"},
        location="US",
    )


def _make_fake_table_ref(table_id: str) -> object:
    return types.SimpleNamespace(
        table_id=table_id,
        reference=types.SimpleNamespace(table_id=table_id),
    )


def _make_fake_table(table_id: str, *, view_query: str | None = None) -> object:
    return types.SimpleNamespace(
        table_id=table_id,
        reference=types.SimpleNamespace(table_id=table_id),
        schema=(
            _make_fake_field("id", "INT64", "REQUIRED"),
            _make_fake_field("name", "STRING"),
        ),
        table_type="VIEW" if view_query else "TABLE",
        view_query=view_query,
        friendly_name=None,
        description=None,
        labels={},
    )


def _make_fake_routine_ref(routine_id: str) -> object:
    inner = types.SimpleNamespace(routine_id=routine_id)
    # Mirror google.cloud.bigquery: the iterator yields ref-shaped
    # objects that themselves expose a ``.reference`` attribute used for
    # the subsequent ``get_routine`` call.
    return types.SimpleNamespace(routine_id=routine_id, reference=inner)


def _make_fake_routine(routine_id: str) -> object:
    return types.SimpleNamespace(
        routine_id=routine_id,
        reference=_make_fake_routine_ref(routine_id),
        arguments=(types.SimpleNamespace(name="x", data_type={"typeKind": "INT64"}),),
        return_type=None,
        type_="SCALAR_FUNCTION",
        language="SQL",
        body="SELECT x + 1",
        description=None,
    )


class _FakeClient:
    """Stand-in for ``google.cloud.bigquery.Client`` for the import path."""

    def __init__(
        self,
        datasets: list[object],
        tables_per_ds: dict[str, list[object]] | None = None,
        routines_per_ds: dict[str, list[object]] | None = None,
    ) -> None:
        self._datasets = datasets
        self._tables = tables_per_ds or {}
        self._routines = routines_per_ds or {}

    def list_datasets(self, project: str) -> list[object]:  # noqa: ARG002
        return [_make_fake_dataset_ref("src", d.dataset_id) for d in self._datasets]

    def get_dataset(self, ref: object) -> object:
        ds_id = ref.dataset_id  # type: ignore[attr-defined]
        return next(d for d in self._datasets if d.dataset_id == ds_id)

    def list_tables(self, ref: object) -> list[object]:
        ds_id = ref.dataset_id  # type: ignore[attr-defined]
        return [_make_fake_table_ref(t.table_id) for t in self._tables.get(ds_id, [])]

    def get_table(self, ref: object) -> object:
        tid = ref.table_id  # type: ignore[attr-defined]
        for tables in self._tables.values():
            for tbl in tables:
                if tbl.table_id == tid:
                    return tbl
        raise LookupError(tid)

    def list_routines(self, ref: object) -> list[object]:
        ds_id = ref.dataset_id  # type: ignore[attr-defined]
        return [_make_fake_routine_ref(r.routine_id) for r in self._routines.get(ds_id, [])]

    def get_routine(self, ref: object) -> object:
        rid = ref.routine_id  # type: ignore[attr-defined]
        for routines in self._routines.values():
            for rtn in routines:
                if rtn.routine_id == rid:
                    return rtn
        raise LookupError(rid)


@pytest.fixture
def fake_bigquery_module() -> mock.MagicMock:
    """Install a fake ``google.cloud.bigquery`` module just for this test."""
    fake_module = mock.MagicMock(name="fake_bq_module")
    saved = sys.modules.get("google.cloud.bigquery")
    sys.modules["google.cloud.bigquery"] = fake_module  # type: ignore[assignment]
    yield fake_module
    if saved is not None:
        sys.modules["google.cloud.bigquery"] = saved  # type: ignore[assignment]
    else:  # pragma: no cover
        sys.modules.pop("google.cloud.bigquery", None)


def test_import_mirrors_schemas_and_routines(
    fake_bigquery_module: mock.MagicMock,
    tmp_path: Path,
) -> None:
    ds = _make_fake_dataset("src", "ds1")
    table = _make_fake_table("orders")
    view = _make_fake_table("v_orders", view_query="SELECT 1")
    routine = _make_fake_routine("inc")
    fake_bigquery_module.Client.return_value = _FakeClient(
        datasets=[ds],
        tables_per_ds={"ds1": [table, view]},
        routines_per_ds={"ds1": [routine]},
    )

    summary = run_import(
        source_project="src",
        dataset_filters=None,
        data_dir=tmp_path,
        target_project="local",
    )
    assert summary.datasets == 1
    assert summary.tables == 2
    assert summary.routines == 1

    async def _verify() -> None:
        engine = DuckDBEngine(
            Settings(persistence_mode=PersistenceMode.PERSISTENT, data_dir=tmp_path),
        )
        await engine.start()
        try:
            catalog = DuckDBCatalogRepository(engine)
            catalog.ensure_ready()
            assert catalog.get_dataset("local", "ds1") is not None
            orders = catalog.get_table("local", "ds1", "orders")
            assert orders is not None
            assert orders.table_type == "TABLE"
            assert tuple(f.name for f in orders.schema_.fields) == ("id", "name")
            view_meta = catalog.get_table("local", "ds1", "v_orders")
            assert view_meta is not None
            assert view_meta.table_type == "VIEW"
            assert view_meta.view_query == "SELECT 1"
            inc = catalog.get_routine("local", "ds1", "inc")
            assert inc is not None
            assert inc.definition_body == "SELECT x + 1"
        finally:
            await engine.stop()

    asyncio.run(_verify())


def test_import_filters_datasets(
    fake_bigquery_module: mock.MagicMock,
    tmp_path: Path,
) -> None:
    keep = _make_fake_dataset("src", "ds_a")
    skip = _make_fake_dataset("src", "ds_b")
    fake_bigquery_module.Client.return_value = _FakeClient(datasets=[keep, skip])
    summary = run_import(
        source_project="src",
        dataset_filters=["ds_a"],
        data_dir=tmp_path,
    )
    assert summary.datasets == 1


def test_import_summary_as_dict() -> None:
    s = ImportSummary()
    s.datasets = 2
    s.tables = 4
    s.routines = 1
    assert s.as_dict() == {"datasets": 2, "tables": 4, "routines": 1}


def test_import_update_path_when_dataset_pre_exists(
    fake_bigquery_module: mock.MagicMock,
    tmp_path: Path,
) -> None:
    """Re-importing the same dataset must update, not raise AlreadyExists."""
    ds = _make_fake_dataset("src", "ds1")
    fake_bigquery_module.Client.return_value = _FakeClient(
        datasets=[ds],
        tables_per_ds={"ds1": [_make_fake_table("t1")]},
        routines_per_ds={"ds1": [_make_fake_routine("r1")]},
    )
    # First import.
    run_import(source_project="src", dataset_filters=None, data_dir=tmp_path)
    # Second import — should not raise; update path exercised for all three.
    summary = run_import(source_project="src", dataset_filters=None, data_dir=tmp_path)
    assert summary.datasets == 1
    assert summary.tables == 1
    assert summary.routines == 1
