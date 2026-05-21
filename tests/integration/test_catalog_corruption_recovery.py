"""Integration test: corrupt catalog row → restart → operator action.

Verifies that when an operator restarts the emulator against a
``data_dir`` whose catalog tables contain corruption (one row has
malformed metadata_json), the second emulator's startup raises a
clean :class:`InternalError` identifying the bad row instead of a
cryptic Pydantic ValidationError.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from bqemulator.config import PersistenceMode, Settings
from bqemulator.domain.errors import InternalError
from bqemulator.storage.engine import CATALOG_SCHEMA

pytestmark = pytest.mark.integration


@pytest.fixture
def bq_client() -> Iterator[type]:
    try:
        from google.cloud import bigquery
    except ImportError:  # pragma: no cover
        pytest.skip("google-cloud-bigquery not installed")
    return bigquery


def _start(data_dir: Path) -> object:
    from bqemulator.testing._thread_runner import ThreadedEmulator

    threaded = ThreadedEmulator(
        Settings(
            persistence_mode=PersistenceMode.PERSISTENT,
            data_dir=data_dir,
            rest_port=0,
            grpc_port=0,
        ),
    )
    threaded.start()
    return threaded


def test_corrupt_dataset_row_surfaces_clean_error_on_restart(
    tmp_path: Path,
    bq_client: type,
) -> None:
    """Round-trip: populate → corrupt → second emulator boot raises cleanly."""
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials

    data_dir = tmp_path / "data"

    # 1. Populate the catalog through a normal emulator run.
    threaded = _start(data_dir)
    try:
        client = bq_client.Client(
            project="p",
            credentials=AnonymousCredentials(),
            client_options=ClientOptions(api_endpoint=threaded.server.rest_url),
        )
        try:
            client.create_dataset("d")
            client.create_table(
                bq_client.Table(
                    "p.d.orders",
                    schema=[bq_client.SchemaField("id", "INT64")],
                ),
            )
        finally:
            client.close()
    finally:
        threaded.stop()

    # 2. Inject corruption directly into the persistent DuckDB file.
    import duckdb

    conn = duckdb.connect(str(data_dir / "bqemulator.duckdb"))
    try:
        conn.execute(
            f'UPDATE "{CATALOG_SCHEMA}"."datasets" '
            "SET metadata_json = '{this is not valid json' "
            "WHERE dataset_id = 'd'",
        )
    finally:
        conn.close()

    # 3. Restart the emulator. Strict mode must raise with row identity.
    with pytest.raises(InternalError, match=r"datasets row p\.d"):
        _start(data_dir)


def test_lenient_mode_via_construction_skips_corrupt_row(
    tmp_path: Path,
    bq_client: type,
) -> None:
    """Lenient mode (constructor flag) skips one corrupt row, loads the rest.

    The ``ThreadedEmulator`` always uses strict mode; this test calls
    the repository directly with ``lenient=True`` to confirm the
    operator escape hatch works against a real persistent catalog.
    """
    import asyncio

    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials

    from bqemulator.catalog.duckdb_repository import DuckDBCatalogRepository
    from bqemulator.storage.engine import DuckDBEngine

    data_dir = tmp_path / "data"

    threaded = _start(data_dir)
    try:
        client = bq_client.Client(
            project="p",
            credentials=AnonymousCredentials(),
            client_options=ClientOptions(api_endpoint=threaded.server.rest_url),
        )
        try:
            client.create_dataset("a")
            client.create_dataset("b")
        finally:
            client.close()
    finally:
        threaded.stop()

    # Corrupt dataset ``a`` but leave ``b`` intact.
    import duckdb

    conn = duckdb.connect(str(data_dir / "bqemulator.duckdb"))
    try:
        conn.execute(
            f'UPDATE "{CATALOG_SCHEMA}"."datasets" '
            "SET metadata_json = 'BROKEN' WHERE dataset_id = 'a'",
        )
    finally:
        conn.close()

    async def _lenient_hydrate() -> tuple[str, ...]:
        engine = DuckDBEngine(
            Settings(persistence_mode=PersistenceMode.PERSISTENT, data_dir=data_dir),
        )
        await engine.start()
        try:
            repo = DuckDBCatalogRepository(engine, lenient=True)
            repo.ensure_ready()
            return tuple(d.dataset_id for d in repo.list_all_datasets())
        finally:
            await engine.stop()

    loaded = asyncio.run(_lenient_hydrate())
    # The corrupt ``a`` is skipped; ``b`` still loads.
    assert loaded == ("b",)
