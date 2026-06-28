"""``ML.PREDICT`` surface-only rewrite (ADR 0047 / RFC 0002).

``ML.PREDICT(MODEL ref, (input_query | TABLE ref))`` is a surface-only
construct: it resolves a registered model's metadata and returns the
input rows unchanged plus a deterministic, explicitly non-real
prediction column per label column. **No model is run.** The prediction
value is a constant ``0.0`` (identical on every row, which genuine
row-varying model output never is) and is documented as a stub
everywhere it surfaces.

This module turns the ``exp.Predict`` table function into an ordinary
subquery *before* translation, so the surrounding query and the rest of
the rewrite chain (row-access, ``INFORMATION_SCHEMA`` expansion,
time-travel, wildcard expansion, BigQuery to DuckDB translation) need no
special handling. Because it runs inside the shared
:func:`bqemulator.sql.inner_query.rewrite_and_translate_statement`
chain, standalone and scripted statements share one code path, exactly
as ``EXPORT DATA`` and ``CREATE MODEL`` do.

Scope (RFC 0002): the regression-shaped default ``predicted_<label>``
(``FLOAT64``) per label column. Per-model-task output shapes
(classifier probability arrays, k-means ``centroid_id``), exact column
order, and the recorded prediction-value divergence are resolved by
conformance recording in a later phase, not here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlglot
from sqlglot import exp

from bqemulator.domain.errors import ResourceRef, resource_not_found

if TYPE_CHECKING:
    from bqemulator.catalog.models import ModelMeta
    from bqemulator.catalog.repository import CatalogRepository

#: The deterministic, intentionally non-real prediction value (RFC 0002).
#: Constant across every row, so it can never be mistaken for genuine,
#: row-varying model output; documented as a stub wherever it appears.
_STUB_PREDICTION = "CAST(0.0 AS FLOAT64)"

#: Alias given to the wrapped input query inside the synthesised subquery.
_INPUT_ALIAS = "_ml_predict_input"


def rewrite_ml_predict(
    bq_sql: str,
    *,
    project_id: str,
    catalog: CatalogRepository,
) -> str:
    """Rewrite every ``ML.PREDICT`` table function into an equivalent subquery.

    Returns ``bq_sql`` unchanged when it parses to no ``exp.Predict``
    node (the common case) or fails to parse (a later layer reports the
    error). Raises :func:`resource_not_found` (HTTP 404, ``notFound``)
    when a referenced model is not registered, matching BigQuery.
    """
    if "predict" not in bq_sql.lower():
        return bq_sql
    try:
        tree = sqlglot.parse_one(bq_sql, read="bigquery")
    except Exception:  # noqa: BLE001 - parse failures fall through to later layers
        return bq_sql

    predicts = list(tree.find_all(exp.Predict))
    if not predicts:
        return bq_sql

    for node in predicts:
        target, alias_name = _replacement_target(node)
        target.replace(
            _predict_subquery(node, project_id=project_id, catalog=catalog, alias_name=alias_name)
        )
    return tree.sql(dialect="bigquery")


def _replacement_target(node: exp.Predict) -> tuple[exp.Expression, str]:
    """Return the node to replace and the alias to carry onto the subquery.

    ``FROM ML.PREDICT(...) AS t`` wraps the ``Predict`` in a parent
    ``exp.Table`` that holds the alias, so that wrapper is replaced and
    its alias preserved. An unaliased ``ML.PREDICT`` is replaced in place.
    """
    parent = node.parent
    if isinstance(parent, exp.Table) and parent.this is node:
        return parent, parent.alias or ""
    # sqlglot always wraps a FROM table function in exp.Table, so this
    # in-place fallback only guards an unexpected future parse shape.
    return node, ""  # pragma: no cover


def _predict_subquery(
    node: exp.Predict,
    *,
    project_id: str,
    catalog: CatalogRepository,
    alias_name: str,
) -> exp.Subquery:
    """Build the passthrough-plus-prediction subquery replacing one ``Predict``."""
    model = _resolve_model(node.this, project_id=project_id, catalog=catalog)
    projections = ["*", *(f"{_STUB_PREDICTION} AS {name}" for name in _predicted_columns(model))]
    select = exp.select(*projections).from_(_input_from_clause(node.args.get("expression")))
    return select.subquery(alias=alias_name) if alias_name else select.subquery()


def _resolve_model(
    model_ref: exp.Expression,
    *,
    project_id: str,
    catalog: CatalogRepository,
) -> ModelMeta:
    """Resolve ``MODEL ref`` against the catalog or raise a BigQuery 404."""
    model_id = model_ref.name
    dataset_id = model_ref.args.get("db")
    dataset_name = dataset_id.name if isinstance(dataset_id, exp.Identifier) else (dataset_id or "")
    catalog_node = model_ref.args.get("catalog")
    project_part = catalog_node.name if isinstance(catalog_node, exp.Identifier) else None
    project = project_part or project_id
    model = catalog.get_model(project, dataset_name, model_id) if dataset_name else None
    if model is None:
        raise resource_not_found(
            ResourceRef("model", project, dataset_name or None, model_id or None)
        )
    return model


def _predicted_columns(model: ModelMeta) -> list[str]:
    """Return the ``predicted_<label>`` column name(s) for ``model``.

    One per registered label column; falls back to a single generic
    ``predicted`` column for a model with no label columns (an
    unsupervised model whose exact output shape is out of scope here).
    """
    names = [f"predicted_{col['name']}" for col in model.label_columns if col.get("name")]
    return names or ["predicted"]


def _input_from_clause(expression: exp.Expression) -> str:
    """Return the FROM-clause text for the ``ML.PREDICT`` input.

    The input is either a subquery (``(SELECT ...)``), which is wrapped as an
    aliased derived table so the passthrough ``*`` can read from it, or a
    ``TABLE ref``, which is read directly.
    """
    if isinstance(expression, exp.Subquery):
        return f"({expression.this.sql(dialect='bigquery')}) AS {_INPUT_ALIAS}"
    return expression.sql(dialect="bigquery")


__all__ = ["rewrite_ml_predict"]
