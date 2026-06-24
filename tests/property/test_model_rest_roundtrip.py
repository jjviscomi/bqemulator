"""Hypothesis property tests for Model REST mapping.

Two invariants of the Models REST adapter:

* ``_model_to_rest`` preserves identity, declared type, and feature/label
  column *shape*, and never leaks the internal training-query provenance.
* An empty ``PATCH`` carries every read-only field through unchanged and
  only advances ``lastModifiedTime``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from hypothesis import given
from hypothesis import strategies as st
import pytest

from bqemulator.api.routes.models import _model_to_rest, _rest_to_model_meta
from bqemulator.catalog.models import ModelMeta
from bqemulator.domain.clock import FrozenClock

pytestmark = pytest.mark.property

_CREATED = datetime(2026, 4, 15, tzinfo=UTC)
_PATCH_AT = datetime(2026, 5, 1, tzinfo=UTC)

_IDENT = st.from_regex(r"^[a-z][a-z0-9_]{0,15}$", fullmatch=True)
_COLUMN = st.builds(
    lambda name, kind: {"name": name, "type": {"typeKind": kind}},
    _IDENT,
    st.sampled_from(["INT64", "FLOAT64", "STRING", "BOOL"]),
)


@st.composite
def _models(draw: st.DrawFn) -> ModelMeta:
    return ModelMeta(
        project_id=draw(_IDENT),
        dataset_id=draw(_IDENT),
        model_id=draw(_IDENT),
        model_type=draw(st.sampled_from(["MODEL_TYPE_UNSPECIFIED", "KMEANS", "ARIMA_PLUS"])),
        description=draw(st.none() | st.text(max_size=20)),
        friendly_name=draw(st.none() | st.text(max_size=20)),
        labels=draw(st.dictionaries(_IDENT, _IDENT, max_size=3)),
        feature_columns=draw(st.lists(_COLUMN, max_size=3).map(tuple)),
        label_columns=draw(st.lists(_COLUMN, max_size=3).map(tuple)),
        training_query=draw(st.none() | st.text(max_size=30)),
        creation_time=_CREATED,
        last_modified_time=_CREATED,
        etag="etag-fixed",
    )


@given(model=_models())
def test_model_to_rest_preserves_shape_and_hides_provenance(model: ModelMeta) -> None:
    rest = _model_to_rest(model)
    assert rest["modelReference"] == {
        "projectId": model.project_id,
        "datasetId": model.dataset_id,
        "modelId": model.model_id,
    }
    assert rest["modelType"] == model.model_type
    assert rest["etag"] == model.etag
    assert rest["location"] == model.location
    assert rest.get("featureColumns", []) == [dict(c) for c in model.feature_columns]
    assert rest.get("labelColumns", []) == [dict(c) for c in model.label_columns]
    # Internal provenance must never surface in the REST representation.
    assert "training_query" not in rest
    assert "trainingQuery" not in rest


@given(model=_models())
def test_empty_patch_preserves_read_only_fields(model: ModelMeta) -> None:
    updated = _rest_to_model_meta({}, FrozenClock(_PATCH_AT), model)
    # Read-only + (absent-from-body) mutable fields carry through verbatim.
    assert updated.model_type == model.model_type
    assert updated.feature_columns == model.feature_columns
    assert updated.label_columns == model.label_columns
    assert updated.location == model.location
    assert updated.training_query == model.training_query
    assert updated.creation_time == model.creation_time
    assert updated.description == model.description
    assert updated.friendly_name == model.friendly_name
    assert updated.labels == model.labels
    # Only the modification stamp advances.
    assert updated.last_modified_time == _PATCH_AT
