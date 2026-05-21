"""Unit tests for :mod:`bqemulator.versioning.clone`."""

from __future__ import annotations

import pytest

from bqemulator.api.dependencies import AppContext
from bqemulator.domain.errors import AlreadyExistsError, NotFoundError
from bqemulator.storage.sql_identifiers import quoted_table_ref
from bqemulator.versioning.clone import CloneManager

pytestmark = pytest.mark.unit


async def test_clone_copies_rows_and_marks_table_type_clone(
    full_ctx: AppContext,
    make_dataset,
    make_table,
) -> None:
    make_dataset("p", "ds")
    make_table("p", "ds", "t", rows=[(1, "a"), (2, "b")])

    manager = CloneManager(full_ctx)
    meta = await manager.create("p", "ds", "copy_t", "p", "ds", "t")
    assert meta.table_type == "CLONE"
    assert meta.base_table == "p.ds.t"

    rows = full_ctx.engine.execute(
        f"SELECT id FROM {quoted_table_ref('p', 'ds', 'copy_t')} ORDER BY id",
    ).fetchall()
    assert rows == [(1,), (2,)]


async def test_clone_diverges_after_dml_against_clone(
    full_ctx: AppContext,
    make_dataset,
    make_table,
) -> None:
    make_dataset("p", "ds")
    make_table("p", "ds", "t", rows=[(1, "a")])

    manager = CloneManager(full_ctx)
    await manager.create("p", "ds", "clone_t", "p", "ds", "t")

    # Mutate the clone; the source must remain untouched.
    full_ctx.engine.execute(
        f"INSERT INTO {quoted_table_ref('p', 'ds', 'clone_t')} VALUES (99, 'z')",
    )

    src_rows = full_ctx.engine.execute(
        f"SELECT count(*) FROM {quoted_table_ref('p', 'ds', 't')}",
    ).fetchone()
    clone_rows = full_ctx.engine.execute(
        f"SELECT count(*) FROM {quoted_table_ref('p', 'ds', 'clone_t')}",
    ).fetchone()
    assert src_rows is not None and src_rows[0] == 1
    assert clone_rows is not None and clone_rows[0] == 2


async def test_clone_missing_source_raises(
    full_ctx: AppContext,
    make_dataset,
) -> None:
    make_dataset("p", "ds")
    manager = CloneManager(full_ctx)
    with pytest.raises(NotFoundError):
        await manager.create("p", "ds", "x", "p", "ds", "missing")


async def test_clone_existing_destination_raises(
    full_ctx: AppContext,
    make_dataset,
    make_table,
) -> None:
    make_dataset("p", "ds")
    make_table("p", "ds", "t", rows=[(1, "a")])
    make_table("p", "ds", "existing", rows=[(1, "a")])
    manager = CloneManager(full_ctx)
    with pytest.raises(AlreadyExistsError):
        await manager.create("p", "ds", "existing", "p", "ds", "t")


async def test_clone_missing_destination_dataset_raises(
    full_ctx: AppContext,
    make_dataset,
    make_table,
) -> None:
    make_dataset("p", "ds")
    make_table("p", "ds", "t")
    manager = CloneManager(full_ctx)
    with pytest.raises(NotFoundError):
        await manager.create("p", "missing_ds", "x", "p", "ds", "t")
