"""Unit tests for :class:`TempRoutineRegistry` (ADR 0023 §1.D)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio

from bqemulator.catalog.models import RoutineArgument, RoutineMeta
from bqemulator.config import Settings
from bqemulator.domain.errors import InvalidQueryError
from bqemulator.storage.engine import DuckDBEngine
from bqemulator.udf.runtime import UDFRegistry
from bqemulator.udf.temp_registry import TempRoutineRegistry

pytestmark = pytest.mark.unit

NOW = datetime(2026, 4, 15, tzinfo=UTC)


@pytest_asyncio.fixture
async def engine(ephemeral_settings: Settings) -> AsyncIterator[DuckDBEngine]:
    e = DuckDBEngine(ephemeral_settings)
    await e.start()
    try:
        yield e
    finally:
        await e.stop()


def _routine(
    *,
    bare_name: str,
    dataset_id: str,
    body: str = "x + 1",
    arguments: tuple[RoutineArgument, ...] = (
        RoutineArgument(name="x", data_type={"typeKind": "INT64"}),
    ),
    return_type: dict[str, object] | None = None,
) -> RoutineMeta:
    return RoutineMeta(
        project_id="p",
        dataset_id=dataset_id,
        routine_id=bare_name,
        routine_type="SCALAR_FUNCTION",
        language="SQL",
        definition_body=body,
        arguments=arguments,
        return_type=return_type or {"typeKind": "INT64"},
        creation_time=NOW,
        last_modified_time=NOW,
        etag="e",
    )


class TestTempRoutineRegistry:
    """Single-part identifier resolves to TEMP function contract."""

    def test_synthetic_dataset_unique_per_instance(
        self,
        engine: DuckDBEngine,
        ephemeral_settings: Settings,
    ) -> None:
        """Two registries on the same engine never share a dataset id."""
        reg_a = TempRoutineRegistry(
            engine=engine,
            udf_registry=UDFRegistry(ephemeral_settings),
        )
        reg_b = TempRoutineRegistry(
            engine=engine,
            udf_registry=UDFRegistry(ephemeral_settings),
        )
        assert reg_a.synthetic_dataset != reg_b.synthetic_dataset
        assert reg_a.synthetic_dataset.startswith("_bqemu_temp_")
        assert reg_b.synthetic_dataset.startswith("_bqemu_temp_")

    def test_register_resolves_bare_name(
        self,
        engine: DuckDBEngine,
        ephemeral_settings: Settings,
    ) -> None:
        """``register(name, …)`` makes the bare name resolvable."""
        reg = TempRoutineRegistry(
            engine=engine,
            udf_registry=UDFRegistry(ephemeral_settings),
        )
        routine = _routine(bare_name="addone", dataset_id=reg.synthetic_dataset)
        reg.register("addone", routine)

        resolved = reg.resolve("addone")
        assert resolved is routine
        assert resolved.dataset_id == reg.synthetic_dataset

    def test_resolve_unknown_returns_none(
        self,
        engine: DuckDBEngine,
        ephemeral_settings: Settings,
    ) -> None:
        """Bare names not in the registry resolve to None — the script
        interpreter's resolver then falls through to the 2/3-part check."""
        reg = TempRoutineRegistry(
            engine=engine,
            udf_registry=UDFRegistry(ephemeral_settings),
        )
        assert reg.resolve("nothing_here") is None

    def test_register_rejects_mismatched_dataset(
        self,
        engine: DuckDBEngine,
        ephemeral_settings: Settings,
    ) -> None:
        """The routine must carry the registry's synthetic dataset id."""
        reg = TempRoutineRegistry(
            engine=engine,
            udf_registry=UDFRegistry(ephemeral_settings),
        )
        bad = _routine(bare_name="addone", dataset_id="someone_elses_ds")
        with pytest.raises(InvalidQueryError):
            reg.register("addone", bad)

    def test_materialised_macro_is_callable(
        self,
        engine: DuckDBEngine,
        ephemeral_settings: Settings,
    ) -> None:
        """``register`` materialises a DuckDB MACRO under the flat name."""
        reg = TempRoutineRegistry(
            engine=engine,
            udf_registry=UDFRegistry(ephemeral_settings),
        )
        routine = _routine(bare_name="addone", dataset_id=reg.synthetic_dataset)
        reg.register("addone", routine)

        from bqemulator.udf.naming import qualified_routine_name

        flat = qualified_routine_name(routine)
        (result,) = engine.execute(f'SELECT "{flat}"(5)').fetchone()
        assert result == 6

    def test_rewrite_calls_renames_anonymous(
        self,
        engine: DuckDBEngine,
        ephemeral_settings: Settings,
    ) -> None:
        """Pre-rewrite turns bare ``foo(...)`` into the flat qualified call."""
        reg = TempRoutineRegistry(
            engine=engine,
            udf_registry=UDFRegistry(ephemeral_settings),
        )
        routine = _routine(bare_name="addone", dataset_id=reg.synthetic_dataset)
        reg.register("addone", routine)

        rewritten = reg.rewrite_calls("SELECT addone(41) AS n")

        from bqemulator.udf.naming import qualified_routine_name

        flat = qualified_routine_name(routine)
        assert flat in rewritten
        # The flat name ends in ``__addone`` so substring-checking for
        # the bare call requires a word-boundary-aware check: confirm
        # no token equals ``addone(`` once the flat name is stripped.
        scrubbed = rewritten.replace(flat, "<FLAT>")
        assert "addone(" not in scrubbed

    def test_rewrite_calls_unregistered_passthrough(
        self,
        engine: DuckDBEngine,
        ephemeral_settings: Settings,
    ) -> None:
        """Function calls whose name is not in the registry are unchanged."""
        reg = TempRoutineRegistry(
            engine=engine,
            udf_registry=UDFRegistry(ephemeral_settings),
        )
        # No routines registered.
        out = reg.rewrite_calls("SELECT UPPER('x')")
        assert out == "SELECT UPPER('x')"

    def test_rewrite_calls_skips_unregistered_when_registry_nonempty(
        self,
        engine: DuckDBEngine,
        ephemeral_settings: Settings,
    ) -> None:
        """When the registry has TEMP functions, unrelated ``Anonymous``
        nodes pass through untouched and the SQL is returned unchanged."""
        reg = TempRoutineRegistry(
            engine=engine,
            udf_registry=UDFRegistry(ephemeral_settings),
        )
        routine = _routine(bare_name="addone", dataset_id=reg.synthetic_dataset)
        reg.register("addone", routine)
        # ``some_other_fn`` is not in the registry — the rewrite walks
        # every ``Anonymous`` node but rewrites none, and the no-change
        # path returns the input string verbatim.
        sql = "SELECT some_other_fn(1)"
        assert reg.rewrite_calls(sql) == sql

    def test_rewrite_calls_unparseable_input_passthrough(
        self,
        engine: DuckDBEngine,
        ephemeral_settings: Settings,
    ) -> None:
        """Malformed SQL flows through — the translator emits the error."""
        reg = TempRoutineRegistry(
            engine=engine,
            udf_registry=UDFRegistry(ephemeral_settings),
        )
        routine = _routine(bare_name="addone", dataset_id=reg.synthetic_dataset)
        reg.register("addone", routine)
        gibberish = "SELECT this is not valid SQL (((("
        assert reg.rewrite_calls(gibberish) == gibberish

    def test_cleanup_drops_macros(
        self,
        engine: DuckDBEngine,
        ephemeral_settings: Settings,
    ) -> None:
        """After ``cleanup``, the materialised macro is no longer callable."""
        reg = TempRoutineRegistry(
            engine=engine,
            udf_registry=UDFRegistry(ephemeral_settings),
        )
        routine = _routine(bare_name="addone", dataset_id=reg.synthetic_dataset)
        reg.register("addone", routine)

        from bqemulator.udf.naming import qualified_routine_name

        flat = qualified_routine_name(routine)
        reg.cleanup()

        # Macro is dropped — DuckDB raises a catalog error.
        with pytest.raises(Exception):
            engine.execute(f'SELECT "{flat}"(5)').fetchone()
        # Subsequent cleanup is idempotent.
        reg.cleanup()
        assert reg.resolve("addone") is None
