"""Property tests for ``CREATE MODEL`` feature/label derivation (RFC 0002 / ADR 0047).

The model-registration logic (:func:`bqemulator.jobs.executor.register_model`)
touches only the catalog, not the storage engine, so these properties run
against a real ``MemoryCatalogRepository`` without starting DuckDB. They pin
two invariants over arbitrary training-query schemas:

* the feature/label column split is a complete, disjoint partition of the
  output columns, ordered to match the query;
* ``CREATE OR REPLACE`` is idempotent: re-registering the same schema yields
  identical feature/label columns.
"""

from __future__ import annotations

from datetime import UTC, datetime

from hypothesis import given
from hypothesis import strategies as st
import pyarrow as pa

from bqemulator.api.dependencies import AppContext
from bqemulator.catalog.memory_repository import MemoryCatalogRepository
from bqemulator.catalog.models import DatasetMeta
from bqemulator.config import PersistenceMode, Settings
from bqemulator.domain.clock import FrozenClock
from bqemulator.domain.events import EventBus
from bqemulator.jobs.executor import _CreateModelRequest, register_model
from bqemulator.observability.metrics import MetricsRegistry
from bqemulator.row_access.policy import RowAccessPolicyManager
from bqemulator.storage.engine import DuckDBEngine
from bqemulator.udf.runtime import UDFRegistry
from bqemulator.versioning.snapshots import SnapshotManager

NOW = datetime(2026, 5, 16, tzinfo=UTC)
_SETTINGS = Settings(persistence_mode=PersistenceMode.EPHEMERAL, rest_port=0, grpc_port=0)  # type: ignore[arg-type]
# register_model never touches the engine (it writes only the catalog), so an
# unstarted engine is sufficient and keeps these properties fast.
_ENGINE = DuckDBEngine(_SETTINGS)

_ARROW_TYPES = [pa.int64(), pa.float64(), pa.string(), pa.bool_(), pa.date32()]


def _fresh_ctx() -> AppContext:
    """Build a catalog-only ``AppContext`` with dataset ``p.ds`` registered."""
    catalog = MemoryCatalogRepository()
    catalog.create_dataset(
        DatasetMeta(
            project_id="p",
            dataset_id="ds",
            creation_time=NOW,
            last_modified_time=NOW,
            etag="e",
        ),
    )
    return AppContext(
        settings=_SETTINGS,
        clock=FrozenClock(NOW),
        engine=_ENGINE,
        catalog=catalog,
        metrics=MetricsRegistry(),
        events=EventBus(),
        udf_registry=UDFRegistry(_SETTINGS),
        snapshots=SnapshotManager(
            engine=_ENGINE,
            catalog=MemoryCatalogRepository(),
            clock=FrozenClock(NOW),
            events=EventBus(),
            retention_days=7,
        ),
        row_access=RowAccessPolicyManager(catalog=catalog, clock=FrozenClock(NOW)),
    )


@st.composite
def _schema_and_labels(draw: st.DrawFn) -> tuple[pa.Schema, tuple[str, ...]]:
    """Draw a unique-column Arrow schema plus a subset of its names as labels."""
    names = draw(
        st.lists(
            st.from_regex(r"[a-z][a-z0-9_]{0,7}", fullmatch=True),
            min_size=1,
            max_size=6,
            unique=True,
        ),
    )
    types = [draw(st.sampled_from(_ARROW_TYPES)) for _ in names]
    schema = pa.schema(list(zip(names, types, strict=True)))
    labels = tuple(draw(st.lists(st.sampled_from(names), unique=True, max_size=len(names))))
    return schema, labels


def _request(label_cols: tuple[str, ...]) -> _CreateModelRequest:
    """A minimal ``_CreateModelRequest`` for ``p.ds.m`` with the given labels."""
    return _CreateModelRequest(
        project_id=None,
        dataset_id="ds",
        model_id="m",
        model_type="linear_reg",
        label_cols=label_cols,
        select_sql="SELECT 1",
        replace=False,
        if_not_exists=False,
    )


@given(_schema_and_labels())
def test_feature_label_partition_is_complete_and_disjoint(
    case: tuple[pa.Schema, tuple[str, ...]],
) -> None:
    """Feature and label columns together cover the output columns, in query order."""
    schema, labels = case
    ctx = _fresh_ctx()
    register_model(_request(labels), "p", schema, operation="CREATE", now=NOW, ctx=ctx)
    model = ctx.catalog.get_model("p", "ds", "m")
    assert model is not None
    feature_names = [c["name"] for c in model.feature_columns]
    label_names = [c["name"] for c in model.label_columns]
    label_set = set(labels)
    # Disjoint partition covering every column, each side ordered by the query.
    assert set(feature_names).isdisjoint(label_names)
    assert sorted(feature_names + label_names) == sorted(schema.names)
    assert feature_names == [n for n in schema.names if n not in label_set]
    assert label_names == [n for n in schema.names if n in label_set]


@given(_schema_and_labels())
def test_create_or_replace_is_idempotent(
    case: tuple[pa.Schema, tuple[str, ...]],
) -> None:
    """Re-registering the same schema via REPLACE yields identical columns."""
    schema, labels = case
    ctx = _fresh_ctx()
    register_model(_request(labels), "p", schema, operation="CREATE", now=NOW, ctx=ctx)
    first = ctx.catalog.get_model("p", "ds", "m")
    register_model(_request(labels), "p", schema, operation="REPLACE", now=NOW, ctx=ctx)
    second = ctx.catalog.get_model("p", "ds", "m")
    assert first is not None
    assert second is not None
    assert first.feature_columns == second.feature_columns
    assert first.label_columns == second.label_columns
