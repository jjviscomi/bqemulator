"""Unit tests for the P7.a ``job_config.json`` framework.

Covers:

- :func:`tests.conformance._corpus._load_job_config` — discovery-time
  loader for the optional ``job_config.json`` slot.
- :func:`tests.conformance._job_config.build_job_config` — converter
  from the on-disk JSON shape to a ``google.cloud.bigquery.QueryJobConfig``.
- :func:`tests.conformance._comparison._compare_job_metadata` — the
  optional response-object equivalence diff.

The 6 pilot fixtures under ``sql_corpus/api_configuration/`` are
exercised through ``discover_fixtures(include_unrecorded=True)`` to
prove they parse cleanly; their actual execution is a P7.b
operator-side follow-up (needs real BigQuery to record
``expected.json``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conformance._comparison import _compare_job_metadata, compare_results
from tests.conformance._corpus import discover_fixtures
from tests.conformance._job_config import SUPPORTED_KEYS, build_job_config

pytestmark = pytest.mark.unit


class TestJobConfigDiscovery:
    """``job_config.json`` round-trips through ``discover_fixtures``."""

    def test_job_config_loads_into_fixture(self, tmp_path: Path) -> None:
        phase_dir = tmp_path / "api_configuration"
        fx_dir = phase_dir / "labels_example"
        fx_dir.mkdir(parents=True)
        (fx_dir / "query.sql").write_text("SELECT 1", encoding="utf-8")
        (fx_dir / "job_config.json").write_text(
            json.dumps({"labels": {"a": "b"}}),
            encoding="utf-8",
        )
        (fx_dir / "expected.json").write_text("{}", encoding="utf-8")
        fixtures = discover_fixtures(corpus_dir=tmp_path)
        assert len(fixtures) == 1
        assert fixtures[0].job_config == {"labels": {"a": "b"}}

    def test_no_job_config_file_yields_none(self, tmp_path: Path) -> None:
        """Pre-P7.a fixtures (no job_config.json) get job_config=None."""
        phase_dir = tmp_path / "rest_crud"
        fx_dir = phase_dir / "literal"
        fx_dir.mkdir(parents=True)
        (fx_dir / "query.sql").write_text("SELECT 1", encoding="utf-8")
        (fx_dir / "expected.json").write_text("{}", encoding="utf-8")
        fixtures = discover_fixtures(corpus_dir=tmp_path)
        assert fixtures[0].job_config is None

    def test_malformed_job_config_json_raises(self, tmp_path: Path) -> None:
        phase_dir = tmp_path / "api_configuration"
        fx_dir = phase_dir / "bad"
        fx_dir.mkdir(parents=True)
        (fx_dir / "query.sql").write_text("SELECT 1", encoding="utf-8")
        (fx_dir / "job_config.json").write_text("not json{{", encoding="utf-8")
        (fx_dir / "expected.json").write_text("{}", encoding="utf-8")
        with pytest.raises(ValueError, match="invalid JSON"):
            discover_fixtures(corpus_dir=tmp_path)

    def test_non_object_job_config_json_raises(self, tmp_path: Path) -> None:
        phase_dir = tmp_path / "api_configuration"
        fx_dir = phase_dir / "bad_shape"
        fx_dir.mkdir(parents=True)
        (fx_dir / "query.sql").write_text("SELECT 1", encoding="utf-8")
        (fx_dir / "job_config.json").write_text(
            json.dumps(["a", "b"]),
            encoding="utf-8",
        )
        (fx_dir / "expected.json").write_text("{}", encoding="utf-8")
        with pytest.raises(TypeError, match="must be a top-level object"):
            discover_fixtures(corpus_dir=tmp_path)


_P7A_PILOT_FIXTURES = frozenset(
    {
        "dml_insert_into_destination",
        "dry_run_select",
        "labels_metadata_echo",
        "positional_parameter_int64",
        "priority_batch",
        "use_query_cache_disabled",
    },
)


class TestPilotFixturesParse:
    """The 6 committed P7.a pilot fixtures all parse cleanly.

    P7.b phase 2 (2026-05-19) added ~40 Tier 1 fixtures to the same
    ``api_configuration/`` directory. The original 6 pilots are still
    present and are the regression anchor — the assertions below
    check the pilots are a subset of the discovered set rather than
    equality, so future fixture-authoring sessions can grow the
    directory without breaking the regression guard.
    """

    def test_all_six_pilot_fixtures_discover(self) -> None:
        """The committed pilots show up under ``include_unrecorded=True``."""
        fixtures = discover_fixtures(include_unrecorded=True)
        api_cfg = {f.name for f in fixtures if f.phase == "api_configuration"}
        missing = _P7A_PILOT_FIXTURES - api_cfg
        assert not missing, f"pilots missing from discovery: {sorted(missing)}"

    def test_pilots_all_have_job_config(self) -> None:
        """Every pilot carries a ``job_config.json``."""
        fixtures = discover_fixtures(include_unrecorded=True)
        for fixture in (f for f in fixtures if f.phase == "api_configuration"):
            assert fixture.job_config is not None, f"{fixture.name}: missing job_config.json"

    def test_pilots_appear_in_default_discovery_once_recorded(self) -> None:
        """After P7.a recording (2026-05-19), the 6 pilots have expected.json.

        The discovery default (``include_unrecorded=False``) surfaces them
        because real BigQuery baselines were captured the same session.
        Subsequent recording sessions (e.g. P7.b phase 2) grow the
        ``api_configuration/`` set; the assertion below only requires
        the 6 P7.a pilots remain recorded.
        """
        recorded = discover_fixtures(include_unrecorded=False)
        api_cfg = {f.name for f in recorded if f.phase == "api_configuration"}
        missing = _P7A_PILOT_FIXTURES - api_cfg
        assert not missing, f"pilots no longer recorded: {sorted(missing)}"


class TestBuildJobConfig:
    """``build_job_config`` round-trips the supported keys to a real ``QueryJobConfig``."""

    def test_use_query_cache_false(self) -> None:
        config = build_job_config({"use_query_cache": False})
        assert config.use_query_cache is False

    def test_use_legacy_sql_true(self) -> None:
        config = build_job_config({"use_legacy_sql": True})
        assert config.use_legacy_sql is True

    def test_dry_run_true(self) -> None:
        config = build_job_config({"dry_run": True})
        assert config.dry_run is True

    def test_priority_batch_uppercased(self) -> None:
        config = build_job_config({"priority": "batch"})
        assert config.priority == "BATCH"

    def test_priority_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="priority must be one of"):
            build_job_config({"priority": "LOW"})

    def test_write_disposition_truncate(self) -> None:
        config = build_job_config({"write_disposition": "WRITE_TRUNCATE"})
        assert config.write_disposition == "WRITE_TRUNCATE"

    def test_write_disposition_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="write_disposition must be one of"):
            build_job_config({"write_disposition": "WRITE_MAYBE"})

    def test_create_disposition_never(self) -> None:
        config = build_job_config({"create_disposition": "CREATE_NEVER"})
        assert config.create_disposition == "CREATE_NEVER"

    def test_destination_table_string(self) -> None:
        config = build_job_config({"destination": "p.d.t"})
        # The BQ client parses the string lazily; we can inspect the
        # raw attribute that the client serialises into the REST body.
        assert str(config.destination) == "p.d.t"

    def test_default_dataset_string(self) -> None:
        config = build_job_config({"default_dataset": "myproj.myds"})
        assert config.default_dataset is not None
        assert config.default_dataset.project == "myproj"
        assert config.default_dataset.dataset_id == "myds"

    def test_maximum_bytes_billed(self) -> None:
        config = build_job_config({"maximum_bytes_billed": 10_000_000})
        assert config.maximum_bytes_billed == 10_000_000

    def test_int_key_rejects_bool(self) -> None:
        """``True`` is an ``int`` subclass — the loader rejects it explicitly."""
        with pytest.raises(TypeError, match="must be an int"):
            build_job_config({"maximum_bytes_billed": True})

    def test_labels_dict(self) -> None:
        config = build_job_config({"labels": {"team": "platform"}})
        assert config.labels == {"team": "platform"}

    def test_labels_non_string_value_raises(self) -> None:
        with pytest.raises(TypeError, match="labels must be a dict"):
            build_job_config({"labels": {"team": 1}})

    def test_schema_update_options_uppercased(self) -> None:
        config = build_job_config(
            {"schema_update_options": ["allow_field_addition", "ALLOW_FIELD_RELAXATION"]}
        )
        assert config.schema_update_options == [
            "ALLOW_FIELD_ADDITION",
            "ALLOW_FIELD_RELAXATION",
        ]

    def test_schema_update_options_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="schema_update_options entries must be"):
            build_job_config({"schema_update_options": ["ALLOW_RANDOM"]})

    def test_unknown_key_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown keys"):
            build_job_config({"write_dispositon": "WRITE_TRUNCATE"})  # typo

    def test_bool_key_rejects_string(self) -> None:
        with pytest.raises(TypeError, match="must be a bool"):
            build_job_config({"use_query_cache": "false"})

    def test_priority_must_be_string(self) -> None:
        with pytest.raises(TypeError, match="must be a string"):
            build_job_config({"priority": 1})

    def test_create_session_true(self) -> None:
        config = build_job_config({"create_session": True})
        assert config.create_session is True

    def test_create_session_must_be_bool(self) -> None:
        with pytest.raises(TypeError, match="must be a bool"):
            build_job_config({"create_session": "yes"})

    def test_connection_properties_round_trip(self) -> None:
        config = build_job_config(
            {
                "connection_properties": [
                    {"key": "session_id", "value": "abc-123"},
                ],
            }
        )
        assert len(config.connection_properties) == 1
        prop = config.connection_properties[0]
        assert prop.key == "session_id"
        assert prop.value == "abc-123"

    def test_connection_properties_must_be_list(self) -> None:
        with pytest.raises(TypeError, match="connection_properties must be a list"):
            build_job_config({"connection_properties": {"k": "v"}})

    def test_clustering_fields_round_trip(self) -> None:
        config = build_job_config({"clustering_fields": ["region", "tier"]})
        assert list(config.clustering_fields) == ["region", "tier"]

    def test_clustering_fields_must_be_non_empty(self) -> None:
        with pytest.raises(ValueError, match="must be a non-empty list"):
            build_job_config({"clustering_fields": []})

    def test_clustering_fields_must_be_list_of_str(self) -> None:
        with pytest.raises(TypeError, match="must be a list"):
            build_job_config({"clustering_fields": [1, 2]})

    def test_time_partitioning_day_only(self) -> None:
        config = build_job_config({"time_partitioning": {"type": "DAY"}})
        assert config.time_partitioning.type_ == "DAY"
        assert config.time_partitioning.field is None

    def test_time_partitioning_with_field(self) -> None:
        config = build_job_config({"time_partitioning": {"type": "DAY", "field": "event_date"}})
        assert config.time_partitioning.type_ == "DAY"
        assert config.time_partitioning.field == "event_date"

    def test_time_partitioning_with_expiration_ms(self) -> None:
        config = build_job_config(
            {
                "time_partitioning": {
                    "type": "DAY",
                    "field": "event_date",
                    "expiration_ms": 86400000,
                },
            }
        )
        assert config.time_partitioning.expiration_ms == 86400000

    def test_time_partitioning_invalid_type(self) -> None:
        with pytest.raises(ValueError, match="must be one of"):
            build_job_config({"time_partitioning": {"type": "SECOND"}})

    def test_time_partitioning_rejects_unknown_subkey(self) -> None:
        with pytest.raises(ValueError, match="unknown keys"):
            build_job_config({"time_partitioning": {"type": "DAY", "bogus": 1}})

    def test_time_partitioning_must_be_dict(self) -> None:
        with pytest.raises(TypeError, match="must be a dict"):
            build_job_config({"time_partitioning": ["DAY"]})

    def test_supported_keys_set_is_non_empty(self) -> None:
        """Regression guard — at least the Tier 1 + Tier 2 keys are present."""
        for key in (
            "use_legacy_sql",
            "use_query_cache",
            "dry_run",
            "priority",
            "write_disposition",
            "create_disposition",
            "destination",
            "default_dataset",
            "labels",
            "schema_update_options",
            "connection_properties",
            "maximum_bytes_billed",
            "job_timeout_ms",
            "create_session",
            "clustering_fields",  # P7.c
            "time_partitioning",  # P7.c
        ):
            assert key in SUPPORTED_KEYS, f"expected {key!r} in SUPPORTED_KEYS"


class TestCompareJobMetadata:
    """The optional ``job_metadata`` block diffs only keys present in ``expected``."""

    def test_pre_p7_fixture_with_no_job_metadata_is_unaffected(self) -> None:
        """A fixture that doesn't write job_metadata gets no comparison.

        Guards against the 878 pre-P7 fixtures: even though
        ``actual_job_metadata`` is always passed by the runner, the
        comparator only diffs when the recorded baseline opts in.
        """
        expected = {
            "schema": [{"name": "n", "type": "INT64"}],
            "rows": [{"n": 1}],
        }
        report = compare_results(
            expected,
            actual_rows=[{"n": 1}],
            actual_schema=[{"name": "n", "type": "INT64"}],
            actual_job_metadata={"cache_hit": True, "statement_type": "SELECT"},
        )
        assert report.ok, report.diffs

    def test_cache_hit_mismatch_surfaces(self) -> None:
        diffs = _compare_job_metadata(
            {"cache_hit": False},
            {"cache_hit": True},
        )
        assert len(diffs) == 1
        assert "cache_hit" in diffs[0]
        assert "False" in diffs[0]
        assert "True" in diffs[0]

    def test_statement_type_match(self) -> None:
        diffs = _compare_job_metadata(
            {"statement_type": "INSERT"},
            {"statement_type": "INSERT"},
        )
        assert diffs == []

    def test_statement_type_mismatch(self) -> None:
        diffs = _compare_job_metadata(
            {"statement_type": "INSERT"},
            {"statement_type": "SELECT"},
        )
        assert any("statement_type" in d for d in diffs)

    def test_num_dml_affected_rows_match(self) -> None:
        diffs = _compare_job_metadata(
            {"num_dml_affected_rows": 42},
            {"num_dml_affected_rows": 42},
        )
        assert diffs == []

    def test_num_dml_affected_rows_mismatch(self) -> None:
        diffs = _compare_job_metadata(
            {"num_dml_affected_rows": 1},
            {"num_dml_affected_rows": 2},
        )
        assert any("num_dml_affected_rows" in d for d in diffs)

    def test_ddl_operation_performed_match(self) -> None:
        diffs = _compare_job_metadata(
            {"ddl_operation_performed": "CREATE"},
            {"ddl_operation_performed": "CREATE"},
        )
        assert diffs == []

    def test_missing_key_in_actual_surfaces_as_absent(self) -> None:
        diffs = _compare_job_metadata(
            {"num_dml_affected_rows": 5},
            {},  # actual produced no num_dml_affected_rows
        )
        assert any("<absent>" in d for d in diffs)

    def test_unknown_recorded_key_raises_diff(self) -> None:
        """A typo in the recorded baseline (``cached_hit``) surfaces as a diff."""
        diffs = _compare_job_metadata(
            {"cached_hit": True},  # typo
            {"cache_hit": True},
        )
        assert any("unknown" in d.lower() for d in diffs)

    def test_multiple_mismatches_all_surfaced(self) -> None:
        diffs = _compare_job_metadata(
            {"cache_hit": False, "statement_type": "INSERT", "num_dml_affected_rows": 5},
            {"cache_hit": True, "statement_type": "SELECT", "num_dml_affected_rows": 5},
        )
        # 2 mismatches, 1 match — the match should not produce a diff.
        assert len(diffs) == 2

    def test_extra_actual_keys_are_ignored(self) -> None:
        """Actual can carry extra fields without breaking expected-only comparison."""
        diffs = _compare_job_metadata(
            {"cache_hit": False},
            {"cache_hit": False, "statement_type": "SELECT"},
        )
        assert diffs == []

    def test_compare_results_routes_job_metadata_through(self) -> None:
        """End-to-end: ``compare_results`` calls ``_compare_job_metadata``."""
        expected = {
            "schema": [{"name": "x", "type": "INT64"}],
            "rows": [{"x": 1}],
            "job_metadata": {"cache_hit": False, "statement_type": "SELECT"},
        }
        # All good.
        report = compare_results(
            expected,
            actual_rows=[{"x": 1}],
            actual_schema=[{"name": "x", "type": "INT64"}],
            actual_job_metadata={"cache_hit": False, "statement_type": "SELECT"},
        )
        assert report.ok, report.diffs

        # Cache-hit divergence.
        report = compare_results(
            expected,
            actual_rows=[{"x": 1}],
            actual_schema=[{"name": "x", "type": "INT64"}],
            actual_job_metadata={"cache_hit": True, "statement_type": "SELECT"},
        )
        assert not report.ok
        assert any("cache_hit" in d for d in report.diffs)
