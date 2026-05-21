"""Convert ``job_config.json`` payloads to BigQuery ``QueryJobConfig`` objects.

Both the conformance recorder (``scripts/record_conformance_fixtures.py``)
and the conformance runner (``tests/conformance/test_corpus.py``) call
:func:`build_job_config` to turn the on-disk JSON shape into a
configured ``google.cloud.bigquery.QueryJobConfig`` instance. The
recorder passes that config to real BigQuery via
``client.query(sql, job_config=...)``; the runner passes the same
config to the BQ Python client against the emulator's REST endpoint,
so the wire-format `jobs.insert` body fields are exercised
end-to-end.

The on-disk shape is documented in ``sql_corpus/README.md`` and
catalogued in
[`api-configuration-coverage-matrix`](../../docs/reference/api-configuration-coverage-matrix.md).
This module sits alongside :mod:`tests.conformance._parameters` —
the two together compose into the full ``QueryJobConfig`` a fixture
needs: if both ``job_config.json`` and ``parameters.json`` are
present, the runner merges the parameter list into the config
returned by this module.

Supported keys (subset of ``QueryJobConfig`` that fixtures actually
need; the loader rejects unknown keys so a typo fails loudly):

- ``use_legacy_sql`` (bool)
- ``use_query_cache`` (bool)
- ``dry_run`` (bool)
- ``priority`` (``"INTERACTIVE"`` | ``"BATCH"``)
- ``write_disposition`` (``"WRITE_EMPTY"`` | ``"WRITE_TRUNCATE"`` | ``"WRITE_APPEND"``)
- ``create_disposition`` (``"CREATE_IF_NEEDED"`` | ``"CREATE_NEVER"``)
- ``destination`` (``"project.dataset.table"``)
- ``default_dataset`` (``"project.dataset"``)
- ``maximum_bytes_billed`` (int)
- ``labels`` (dict[str, str])
- ``job_timeout_ms`` (int)
- ``schema_update_options`` (list[str], values from ``ALLOW_FIELD_ADDITION`` /
  ``ALLOW_FIELD_RELAXATION``)
- ``allow_large_results`` (bool, legacy-SQL only)
- ``flatten_results`` (bool, legacy-SQL only)
- ``create_session`` (bool) — when true, BigQuery creates a transient
  session for the job; subsequent connection-scoped state (TEMP TABLE,
  declared variable) is visible to follow-up queries that carry the
  ``connection_properties.session_id`` referenced from the response
- ``connection_properties`` (list[{"key": str, "value": str}]) — used for
  ``session_id`` and other connection-scoped overrides
- ``clustering_fields`` (list[str]) — destination-table clustering
  columns when the job writes to ``destination`` (P7.c)
- ``time_partitioning`` (dict with ``type``, optional ``field``, and
  optional ``expiration_ms`` keys) — destination-table partitioning
  spec when the job writes to ``destination`` (P7.c)

Any value listed above may use the supported ``${…}`` placeholders
(``${DATASET}``, ``${PROJECT}``, ``${DATASET_ID}``, ``${PRINCIPAL}``,
``${GROUP}``, ``${OTHER_PRINCIPAL}``) — the recorder/runner pre-
substitutes them via :func:`substitute_in_json` before calling
:func:`build_job_config`.
"""

from __future__ import annotations

from typing import Any

_BOOL_KEYS = frozenset(
    {
        "use_legacy_sql",
        "use_query_cache",
        "dry_run",
        "allow_large_results",
        "flatten_results",
        "create_session",
    },
)
_STR_KEYS = frozenset(
    {
        "priority",
        "write_disposition",
        "create_disposition",
        "destination",
        "default_dataset",
    },
)
_INT_KEYS = frozenset({"maximum_bytes_billed", "job_timeout_ms"})
_LIST_OF_STR_KEYS = frozenset({"schema_update_options", "clustering_fields"})
_LIST_OF_PAIR_KEYS = frozenset({"connection_properties"})
_DICT_OF_STR_KEYS = frozenset({"labels"})
_TIME_PARTITIONING_KEYS = frozenset({"time_partitioning"})

#: Set of every recognised top-level key. Anything outside this set
#: in a ``job_config.json`` triggers a ValueError so typos like
#: ``write_dispositon`` (missing 'i') don't silently pass through.
SUPPORTED_KEYS: frozenset[str] = (
    _BOOL_KEYS
    | _STR_KEYS
    | _INT_KEYS
    | _LIST_OF_STR_KEYS
    | _LIST_OF_PAIR_KEYS
    | _DICT_OF_STR_KEYS
    | _TIME_PARTITIONING_KEYS
)

_VALID_PRIORITIES = frozenset({"INTERACTIVE", "BATCH"})
_VALID_WRITE_DISPOSITIONS = frozenset({"WRITE_EMPTY", "WRITE_TRUNCATE", "WRITE_APPEND"})
_VALID_CREATE_DISPOSITIONS = frozenset({"CREATE_IF_NEEDED", "CREATE_NEVER"})
_VALID_SCHEMA_UPDATE_OPTIONS = frozenset({"ALLOW_FIELD_ADDITION", "ALLOW_FIELD_RELAXATION"})
_VALID_TIME_PARTITIONING_TYPES = frozenset({"DAY", "HOUR", "MONTH", "YEAR"})
_VALID_TIME_PARTITIONING_KEYS = frozenset({"type", "field", "expiration_ms"})


def build_job_config(payload: dict[str, Any]) -> Any:
    """Convert a ``job_config.json`` payload into a ``QueryJobConfig``.

    Returns a configured ``google.cloud.bigquery.QueryJobConfig``
    that the recorder/runner can pass to ``client.query(sql,
    job_config=...)``. Parameter merging is the caller's job — when
    a fixture also carries ``parameters.json``, the caller appends
    the parameter list onto the returned config's
    ``query_parameters`` attribute.

    Raises:
        TypeError: when a key's value is the wrong type (e.g.
            ``use_legacy_sql: "yes"`` instead of a bool).
        ValueError: when a key is unrecognised or an enum value is
            outside the supported set.
    """
    from google.cloud import bigquery

    unknown = set(payload) - SUPPORTED_KEYS
    if unknown:
        msg = (
            f"job_config.json: unknown keys {sorted(unknown)}; "
            f"supported keys: {sorted(SUPPORTED_KEYS)}"
        )
        raise ValueError(msg)

    config = bigquery.QueryJobConfig()

    for key, value in payload.items():
        if key in _BOOL_KEYS:
            _set_bool(config, key, value)
        elif key in _INT_KEYS:
            _set_int(config, key, value)
        elif key in _STR_KEYS:
            _set_str(config, key, value, bigquery)
        elif key in _LIST_OF_STR_KEYS:
            _set_list_of_str(config, key, value)
        elif key in _LIST_OF_PAIR_KEYS:
            _set_connection_properties(config, value, bigquery)
        elif key in _DICT_OF_STR_KEYS:
            _set_labels(config, value)
        elif key in _TIME_PARTITIONING_KEYS:
            _set_time_partitioning(config, value, bigquery)

    return config


def _set_bool(config: Any, key: str, value: Any) -> None:
    if not isinstance(value, bool):
        msg = f"job_config.json: {key!r} must be a bool (got {type(value).__name__})"
        raise TypeError(msg)
    setattr(config, key, value)


def _set_int(config: Any, key: str, value: Any) -> None:
    # Reject ``bool`` explicitly because ``isinstance(True, int)`` is True.
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"job_config.json: {key!r} must be an int (got {type(value).__name__})"
        raise TypeError(msg)
    setattr(config, key, value)


def _set_str(config: Any, key: str, value: Any, bigquery: Any) -> None:
    if not isinstance(value, str):
        msg = f"job_config.json: {key!r} must be a string (got {type(value).__name__})"
        raise TypeError(msg)
    if key == "priority":
        upper = value.upper()
        if upper not in _VALID_PRIORITIES:
            msg = (
                f"job_config.json: priority must be one of "
                f"{sorted(_VALID_PRIORITIES)} (got {value!r})"
            )
            raise ValueError(msg)
        config.priority = upper
    elif key == "write_disposition":
        upper = value.upper()
        if upper not in _VALID_WRITE_DISPOSITIONS:
            msg = (
                f"job_config.json: write_disposition must be one of "
                f"{sorted(_VALID_WRITE_DISPOSITIONS)} (got {value!r})"
            )
            raise ValueError(msg)
        config.write_disposition = upper
    elif key == "create_disposition":
        upper = value.upper()
        if upper not in _VALID_CREATE_DISPOSITIONS:
            msg = (
                f"job_config.json: create_disposition must be one of "
                f"{sorted(_VALID_CREATE_DISPOSITIONS)} (got {value!r})"
            )
            raise ValueError(msg)
        config.create_disposition = upper
    elif key == "destination":
        # BigQuery accepts ``project.dataset.table`` strings on the
        # ``destination`` attribute (the client library parses the
        # string into a ``TableReference``).
        config.destination = value
    elif key == "default_dataset":
        config.default_dataset = bigquery.DatasetReference.from_string(value)


def _set_list_of_str(config: Any, key: str, value: Any) -> None:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        msg = f"job_config.json: {key!r} must be a list[str]"
        raise TypeError(msg)
    if key == "schema_update_options":
        bad = [v for v in value if v.upper() not in _VALID_SCHEMA_UPDATE_OPTIONS]
        if bad:
            msg = (
                f"job_config.json: schema_update_options entries must be "
                f"in {sorted(_VALID_SCHEMA_UPDATE_OPTIONS)} (got {bad!r})"
            )
            raise ValueError(msg)
        config.schema_update_options = [v.upper() for v in value]
    elif key == "clustering_fields":
        if not value:
            msg = "job_config.json: clustering_fields must be a non-empty list[str]"
            raise ValueError(msg)
        config.clustering_fields = list(value)


def _set_time_partitioning(config: Any, value: Any, bigquery: Any) -> None:
    """Hydrate ``time_partitioning`` from a ``{type, field?, expiration_ms?}`` dict."""
    if not isinstance(value, dict):
        msg = "job_config.json: time_partitioning must be a dict"
        raise TypeError(msg)
    unknown = set(value) - _VALID_TIME_PARTITIONING_KEYS
    if unknown:
        msg = (
            f"job_config.json: time_partitioning has unknown keys {sorted(unknown)}; "
            f"supported keys: {sorted(_VALID_TIME_PARTITIONING_KEYS)}"
        )
        raise ValueError(msg)
    type_value = value.get("type")
    if not isinstance(type_value, str):
        msg = "job_config.json: time_partitioning['type'] must be a string"
        raise TypeError(msg)
    type_upper = type_value.upper()
    if type_upper not in _VALID_TIME_PARTITIONING_TYPES:
        msg = (
            f"job_config.json: time_partitioning['type'] must be one of "
            f"{sorted(_VALID_TIME_PARTITIONING_TYPES)} (got {type_value!r})"
        )
        raise ValueError(msg)
    field = value.get("field")
    if field is not None and not isinstance(field, str):
        msg = "job_config.json: time_partitioning['field'] must be a string"
        raise TypeError(msg)
    expiration_ms = value.get("expiration_ms")
    if expiration_ms is not None and (
        isinstance(expiration_ms, bool) or not isinstance(expiration_ms, int)
    ):
        msg = "job_config.json: time_partitioning['expiration_ms'] must be an int"
        raise TypeError(msg)
    config.time_partitioning = bigquery.TimePartitioning(
        type_=type_upper,
        field=field,
        expiration_ms=expiration_ms,
    )


def _set_labels(config: Any, value: Any) -> None:
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    ):
        msg = "job_config.json: labels must be a dict[str, str]"
        raise TypeError(msg)
    config.labels = dict(value)


def _set_connection_properties(config: Any, value: Any, bigquery: Any) -> None:
    if not isinstance(value, list):
        msg = "job_config.json: connection_properties must be a list[{'key': str, 'value': str}]"
        raise TypeError(msg)
    props: list[Any] = []
    for idx, entry in enumerate(value):
        if not isinstance(entry, dict):
            msg = f"job_config.json: connection_properties[{idx}] must be a dict"
            raise TypeError(msg)
        k = entry.get("key")
        v = entry.get("value")
        if not isinstance(k, str) or not isinstance(v, str):
            msg = f"job_config.json: connection_properties[{idx}] requires string 'key' and 'value'"
            raise TypeError(msg)
        props.append(bigquery.query.ConnectionProperty(key=k, value=v))
    config.connection_properties = props


__all__ = ["SUPPORTED_KEYS", "build_job_config"]
