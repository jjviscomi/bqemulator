"""Property invariants for the ``ML.PREDICT`` rewriter (RFC 0002 / ADR 0047).

For any set of label columns, the rewrite is complete (no ``exp.Predict``
survives), appends exactly one deterministic stub column per label, preserves
the input query verbatim, and never drops the passthrough projection. These
hold structurally, so they are checked against the pure rewriter without an
engine. The execution-level row-count and passthrough invariants are pinned
by the in-process tests in ``tests/unit/jobs/test_ml_predict.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from hypothesis import given
from hypothesis import strategies as st
import sqlglot
from sqlglot import exp

from bqemulator.catalog.memory_repository import MemoryCatalogRepository
from bqemulator.catalog.models import DatasetMeta, ModelMeta
from bqemulator.sql.rewriter.ml_predict import rewrite_ml_predict

NOW = datetime(2026, 5, 16, tzinfo=UTC)

#: Lowercase SQL-identifier-shaped label names (never collide with the input ``x``).
_LABELS = st.lists(
    st.from_regex(r"[a-z][a-z0-9_]{0,7}", fullmatch=True),
    min_size=1,
    max_size=4,
    unique=True,
)


def _catalog_with_labels(labels: list[str]) -> MemoryCatalogRepository:
    """Return a catalog with model ``p.ds.m`` carrying ``labels`` label columns."""
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
    catalog.create_model(
        ModelMeta(
            project_id="p",
            dataset_id="ds",
            model_id="m",
            label_columns=tuple({"name": name, "type": {"typeKind": "FLOAT64"}} for name in labels),
            creation_time=NOW,
            last_modified_time=NOW,
            etag="e",
        ),
    )
    return catalog


@given(labels=_LABELS)
def test_one_stub_per_label_and_input_preserved(labels: list[str]) -> None:
    """The rewrite appends one ``0.0`` stub per label and preserves the input."""
    out = rewrite_ml_predict(
        "SELECT * FROM ML.PREDICT(MODEL ds.m, (SELECT 1 AS x))",
        project_id="p",
        catalog=_catalog_with_labels(labels),
    )
    # The rewrite is complete: no ML.PREDICT node survives to translation.
    assert not list(sqlglot.parse_one(out, read="bigquery").find_all(exp.Predict))
    # Exactly one deterministic stub column per label, each correctly named.
    assert out.count("CAST(0.0 AS FLOAT64)") == len(labels)
    for name in labels:
        assert f"predicted_{name}" in out
    # The input query and the passthrough projection are preserved.
    assert "SELECT 1 AS x" in out
    assert "SELECT *" in out
