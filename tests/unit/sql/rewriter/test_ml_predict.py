"""Unit tests for the ``ML.PREDICT`` surface-only rewriter (ADR 0047 / RFC 0002).

The rewriter is a pure ``str -> str`` transform over the BigQuery AST that
resolves a registered model and turns ``ML.PREDICT(MODEL ref, input)`` into a
passthrough-plus-prediction subquery before translation. These tests pin the
rewrite shape, the deterministic ``0.0`` stub, alias preservation, the
not-found (404) parity, and the no-op fast paths, all without an engine.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import sqlglot
from sqlglot import exp

from bqemulator.catalog.memory_repository import MemoryCatalogRepository
from bqemulator.catalog.models import DatasetMeta, ModelMeta
from bqemulator.domain.errors import NotFoundError
from bqemulator.sql.rewriter.ml_predict import rewrite_ml_predict

pytestmark = pytest.mark.unit

NOW = datetime(2026, 5, 16, tzinfo=UTC)
_DEFAULT_LABELS = ({"name": "label", "type": {"typeKind": "FLOAT64"}},)


def _catalog(
    *,
    label_columns: tuple[dict[str, object], ...] = _DEFAULT_LABELS,
    project: str = "p",
    dataset: str = "ds",
    model_id: str = "m",
) -> MemoryCatalogRepository:
    """Return a catalog holding dataset ``project.dataset`` and one model."""
    catalog = MemoryCatalogRepository()
    catalog.create_dataset(
        DatasetMeta(
            project_id=project,
            dataset_id=dataset,
            creation_time=NOW,
            last_modified_time=NOW,
            etag="e",
        ),
    )
    catalog.create_model(
        ModelMeta(
            project_id=project,
            dataset_id=dataset,
            model_id=model_id,
            label_columns=label_columns,
            creation_time=NOW,
            last_modified_time=NOW,
            etag="e",
        ),
    )
    return catalog


def _rewrite(sql: str, catalog: MemoryCatalogRepository | None = None) -> str:
    """Rewrite ``sql`` against ``catalog`` (a default single-model catalog)."""
    return rewrite_ml_predict(sql, project_id="p", catalog=catalog or _catalog())


def _has_predict_node(sql: str) -> bool:
    """Return True if ``sql`` still parses to an ``exp.Predict`` node."""
    return bool(list(sqlglot.parse_one(sql, read="bigquery").find_all(exp.Predict)))


class TestRewriteShape:
    """The rewritten SQL preserves passthrough and appends the stub column(s)."""

    def test_subquery_form(self) -> None:
        """A subquery input becomes the inner FROM source with a stub column."""
        out = _rewrite("SELECT * FROM ML.PREDICT(MODEL ds.m, (SELECT 1 AS x))")
        assert not _has_predict_node(out)
        assert "CAST(0.0 AS FLOAT64) AS predicted_label" in out
        assert "SELECT 1 AS x" in out  # input query preserved

    def test_table_form(self) -> None:
        """A ``TABLE ref`` input is read directly as the FROM source."""
        out = _rewrite("SELECT * FROM ML.PREDICT(MODEL ds.m, TABLE ds.scoring)")
        assert not _has_predict_node(out)
        assert "ds.scoring" in out
        assert "AS predicted_label" in out

    def test_alias_preserved(self) -> None:
        """An alias on the table function carries onto the synthesised subquery."""
        out = _rewrite("SELECT t.predicted_label FROM ML.PREDICT(MODEL ds.m, (SELECT 1 AS x)) AS t")
        assert out.rstrip().endswith("AS t")
        # The outer reference still resolves against the aliased subquery.
        assert "t.predicted_label" in out

    def test_multiple_label_columns(self) -> None:
        """One ``predicted_<label>`` column is appended per label column."""
        catalog = _catalog(
            label_columns=(
                {"name": "a", "type": {"typeKind": "FLOAT64"}},
                {"name": "b", "type": {"typeKind": "FLOAT64"}},
            ),
        )
        out = _rewrite("SELECT * FROM ML.PREDICT(MODEL ds.m, (SELECT 1 AS x))", catalog)
        assert "AS predicted_a" in out
        assert "AS predicted_b" in out

    def test_no_label_columns_falls_back_to_predicted(self) -> None:
        """A model with no label columns appends a single generic ``predicted``."""
        catalog = _catalog(label_columns=())
        out = _rewrite("SELECT * FROM ML.PREDICT(MODEL ds.m, (SELECT 1 AS x))", catalog)
        assert "AS predicted FROM" in out
        assert "predicted_" not in out

    def test_label_name_requiring_quoting_is_escaped(self) -> None:
        """A label name with special characters is safely quoted in the alias."""
        catalog = _catalog(label_columns=({"name": "my col", "type": {"typeKind": "FLOAT64"}},))
        out = _rewrite("SELECT * FROM ML.PREDICT(MODEL ds.m, (SELECT 1 AS x))", catalog)
        assert "`predicted_my col`" in out
        assert not _has_predict_node(out)

    def test_nested_ml_predict_both_levels_rewritten(self) -> None:
        """A nested ``ML.PREDICT`` (inside another's input query) is fully rewritten.

        Both levels must be rewritten deepest-first, or the outer rewrite would
        serialise the un-rewritten inner call back into the generated SQL.
        """
        catalog = _catalog()
        catalog.create_model(
            ModelMeta(
                project_id="p",
                dataset_id="ds",
                model_id="m2",
                label_columns=_DEFAULT_LABELS,
                creation_time=NOW,
                last_modified_time=NOW,
                etag="e",
            ),
        )
        sql = (
            "SELECT * FROM ML.PREDICT(MODEL ds.m, "
            "(SELECT * FROM ML.PREDICT(MODEL ds.m2, (SELECT 1 AS x))))"
        )
        out = _rewrite(sql, catalog)
        assert not _has_predict_node(out)
        assert out.count("CAST(0.0 AS FLOAT64)") == 2

    def test_nested_in_cte(self) -> None:
        """``ML.PREDICT`` inside a CTE-bearing query is rewritten in place."""
        sql = "WITH s AS (SELECT 1 x) SELECT * FROM ML.PREDICT(MODEL ds.m, (SELECT * FROM s))"
        out = _rewrite(sql)
        assert not _has_predict_node(out)
        assert "WITH s AS" in out
        assert "AS predicted_label" in out

    def test_explicit_project_qualified_model(self) -> None:
        """A fully ``project.dataset.model`` qualified reference resolves."""
        out = _rewrite("SELECT * FROM ML.PREDICT(MODEL p.ds.m, (SELECT 1 AS x))")
        assert not _has_predict_node(out)
        assert "AS predicted_label" in out


class TestStubValue:
    """The prediction value is the deterministic, non-real ``0.0`` stub."""

    def test_stub_is_constant_zero(self) -> None:
        """Every predicted column is ``CAST(0.0 AS FLOAT64)``."""
        out = _rewrite("SELECT * FROM ML.PREDICT(MODEL ds.m, (SELECT 1 AS x))")
        assert out.count("CAST(0.0 AS FLOAT64)") == 1


class TestModelResolution:
    """Model-reference resolution and BigQuery 404 parity."""

    def test_missing_model_raises_not_found(self) -> None:
        """An unregistered model raises ``NotFoundError`` (HTTP 404, notFound)."""
        with pytest.raises(NotFoundError, match=r"model:p\.ds\.missing"):
            _rewrite("SELECT * FROM ML.PREDICT(MODEL ds.missing, (SELECT 1 AS x))")

    def test_unqualified_model_raises_not_found(self) -> None:
        """A model reference with no dataset cannot resolve and 404s."""
        with pytest.raises(NotFoundError):
            _rewrite("SELECT * FROM ML.PREDICT(MODEL m, (SELECT 1 AS x))")


class TestNoOpFastPaths:
    """Queries without a usable ``ML.PREDICT`` are returned unchanged."""

    def test_no_predict_keyword_returns_unchanged(self) -> None:
        """A query that never mentions predict is returned byte-identical."""
        sql = "SELECT COUNT(*) FROM ds.t"
        assert _rewrite(sql) == sql

    def test_predict_substring_without_node_returns_unchanged(self) -> None:
        """A column merely named like ``predict`` is not a table function."""
        sql = "SELECT predict_score FROM ds.t"
        assert _rewrite(sql) == sql

    def test_unparseable_sql_returns_unchanged(self) -> None:
        """Unparseable SQL falls through to later layers untouched."""
        sql = "SELECT predict FROM FROM (("
        assert _rewrite(sql) == sql
