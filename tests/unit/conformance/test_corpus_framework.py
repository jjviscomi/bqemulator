"""Unit tests for the conformance corpus framework (P2.d + P2.e).

Pins the contract of the optional ``setup_rest.json`` + ``headers.json``
+ ``parameters.json`` files, the ``${PROJECT}`` / ``${DATASET_ID}`` /
``${PRINCIPAL}`` / ``${GROUP}`` placeholders, and the
``substitute_in_json`` JSON-shaped substitution helper. The corpus
framework lives at [`tests/conformance/_corpus.py`](../../conformance/_corpus.py);
the BQ-parameter conversion helper lives in
[`tests/conformance/_parameters.py`](../../conformance/_parameters.py);
the runner-side dataset-tracking helper lives in
[`tests/conformance/test_corpus.py`](../../conformance/test_corpus.py)
and the recorder-side mirror in
[`scripts/record_conformance_fixtures.py`](../../../scripts/record_conformance_fixtures.py).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conformance._corpus import (
    DEFAULT_RUNNER_GROUP,
    DEFAULT_RUNNER_PRINCIPAL,
    PlaceholderContext,
    discover_fixtures,
    substitute_dataset,
    substitute_in_json,
    substitute_placeholders,
)

pytestmark = pytest.mark.unit


class TestPlaceholderContext:
    """The ``${PROJECT}`` / ``${DATASET_ID}`` split is derived from ``dataset``."""

    def test_project_split_from_qualified_dataset(self) -> None:
        ctx = PlaceholderContext(dataset="my-proj.my_ds")
        assert ctx.project == "my-proj"
        assert ctx.dataset_id == "my_ds"

    def test_project_is_dataset_when_unqualified(self) -> None:
        """A bare project name (no ``.``) is returned as ``project`` with empty id.

        The recorder uses this fallback when a fixture has no setup
        and therefore no per-test dataset; ``${DATASET_ID}`` should
        be empty rather than crashing on a missing split.
        """
        ctx = PlaceholderContext(dataset="my-proj")
        assert ctx.project == "my-proj"
        assert ctx.dataset_id == ""

    def test_principal_and_group_default_to_runner_values(self) -> None:
        ctx = PlaceholderContext(dataset="p.ds")
        assert ctx.principal == DEFAULT_RUNNER_PRINCIPAL
        assert ctx.group == DEFAULT_RUNNER_GROUP


class TestSubstitutePlaceholders:
    """Every supported placeholder expands; unknown ones raise."""

    @pytest.fixture
    def ctx(self) -> PlaceholderContext:
        return PlaceholderContext(
            dataset="my-proj.my_ds",
            principal="user:alice@example.com",
            group="group:eng@example.com",
        )

    def test_expands_dataset(self, ctx: PlaceholderContext) -> None:
        assert substitute_placeholders("FROM `${DATASET}.t`", ctx) == "FROM `my-proj.my_ds.t`"

    def test_expands_project_and_dataset_id(self, ctx: PlaceholderContext) -> None:
        path = "/bigquery/v2/projects/${PROJECT}/datasets/${DATASET_ID}/tables/t"
        expected = "/bigquery/v2/projects/my-proj/datasets/my_ds/tables/t"
        assert substitute_placeholders(path, ctx) == expected

    def test_expands_principal_and_group(self, ctx: PlaceholderContext) -> None:
        text = "grantees: ${PRINCIPAL} ${GROUP}"
        assert substitute_placeholders(text, ctx) == (
            "grantees: user:alice@example.com group:eng@example.com"
        )

    def test_expands_gcs_bucket_default(self, ctx: PlaceholderContext) -> None:
        """G1: the ``${GCS_BUCKET}`` placeholder substitutes the runner default."""
        out = substitute_placeholders("gs://${GCS_BUCKET}/g1/x.avro", ctx)
        # Default runner bucket is a syntactically valid placeholder
        # string, not a real bucket — the recorder overrides it via
        # ``BQEMU_CONFORMANCE_GCS_BUCKET``.
        assert out.startswith("gs://bqemu-")
        assert out.endswith("/g1/x.avro")

    def test_expands_gcs_bucket_explicit(self) -> None:
        """G1: explicit ``gcs_bucket`` on the context propagates."""
        ctx = PlaceholderContext(dataset="p.ds", gcs_bucket="my-bucket-name")
        assert (
            substitute_placeholders(
                "gs://${GCS_BUCKET}/g1/x.orc",
                ctx,
            )
            == "gs://my-bucket-name/g1/x.orc"
        )

    def test_raises_on_unknown_placeholder(self, ctx: PlaceholderContext) -> None:
        with pytest.raises(ValueError, match="Unknown placeholder"):
            substitute_placeholders("${TYPO}", ctx)

    def test_lowercase_token_is_not_matched_and_passes_through(
        self, ctx: PlaceholderContext
    ) -> None:
        """A ``${dataset}`` typo (lowercase) is NOT matched by the regex.

        The placeholder pattern only matches ``[A-Z_][A-Z0-9_]*`` so a
        lowercase token never enters the resolver — it passes through
        as the literal ``${dataset}`` string. This matches the legacy
        ``substitute_dataset`` behaviour and is the documented author
        convention (placeholders are always UPPER-CASE).
        """
        assert substitute_placeholders("FROM `${dataset}.t`", ctx) == "FROM `${dataset}.t`"


class TestSubstituteDataset:
    """Legacy shim around the new substituter — kept for back-compat."""

    def test_back_compat_with_substitute_dataset(self) -> None:
        """The legacy two-arg shim is equivalent to the new substituter
        on inputs that only reference ``${DATASET}``.
        """
        sql = "SELECT * FROM `${DATASET}.t`"
        assert substitute_dataset(sql, "p.ds") == "SELECT * FROM `p.ds.t`"


class TestSubstituteInJson:
    """JSON-shaped recursive substitution preserves nesting + non-string values."""

    @pytest.fixture
    def ctx(self) -> PlaceholderContext:
        return PlaceholderContext(
            dataset="my-proj.my_ds",
            principal="user:alice@example.com",
        )

    def test_string_value_is_substituted(self, ctx: PlaceholderContext) -> None:
        assert substitute_in_json("${PRINCIPAL}", ctx) == "user:alice@example.com"

    def test_dict_walks_keys_and_values(self, ctx: PlaceholderContext) -> None:
        body = {
            "rowAccessPolicyReference": {
                "projectId": "${PROJECT}",
                "datasetId": "${DATASET_ID}",
            },
            "grantees": ["${PRINCIPAL}"],
        }
        out = substitute_in_json(body, ctx)
        assert out == {
            "rowAccessPolicyReference": {
                "projectId": "my-proj",
                "datasetId": "my_ds",
            },
            "grantees": ["user:alice@example.com"],
        }

    def test_non_string_values_pass_through(self, ctx: PlaceholderContext) -> None:
        body = {"count": 3, "flag": True, "missing": None}
        assert substitute_in_json(body, ctx) == body


class TestDiscoverFixtures:
    """Discovery surfaces optional ``setup_rest.json`` + ``headers.json`` files."""

    def test_setup_rest_and_headers_are_loaded(self, tmp_path: Path) -> None:
        """A fixture with all four optional files is fully populated."""
        phase_dir = tmp_path / "row_access"
        fx_dir = phase_dir / "rap_demo"
        fx_dir.mkdir(parents=True)

        (fx_dir / "query.sql").write_text("SELECT 1", encoding="utf-8")
        (fx_dir / "setup.sql").write_text("CREATE TABLE t (id INT64)", encoding="utf-8")
        (fx_dir / "setup_rest.json").write_text(
            json.dumps(
                [
                    {
                        "method": "POST",
                        "path": "/p/${PROJECT}",
                        "body": {"grantees": ["${PRINCIPAL}"]},
                    }
                ]
            ),
            encoding="utf-8",
        )
        (fx_dir / "headers.json").write_text(
            json.dumps({"X-Bqemu-Caller": "${PRINCIPAL}"}),
            encoding="utf-8",
        )
        (fx_dir / "expected.json").write_text("{}", encoding="utf-8")

        fixtures = discover_fixtures(corpus_dir=tmp_path)
        assert len(fixtures) == 1
        f = fixtures[0]
        assert f.name == "rap_demo"
        assert len(f.setup_rest) == 1
        assert f.setup_rest[0]["method"] == "POST"
        assert f.headers == (("X-Bqemu-Caller", "${PRINCIPAL}"),)

    def test_fixture_without_optional_files_is_unchanged(self, tmp_path: Path) -> None:
        """Pre-P2.d fixtures (no setup_rest/headers) round-trip with empty values."""
        phase_dir = tmp_path / "rest_crud"
        fx_dir = phase_dir / "literal"
        fx_dir.mkdir(parents=True)
        (fx_dir / "query.sql").write_text("SELECT 1", encoding="utf-8")
        (fx_dir / "expected.json").write_text("{}", encoding="utf-8")

        fixtures = discover_fixtures(corpus_dir=tmp_path)
        assert len(fixtures) == 1
        f = fixtures[0]
        assert f.setup_rest == ()
        assert f.headers == ()
        assert f.setup_sql is None
        assert f.needs_dataset is False

    def test_needs_dataset_true_for_rest_only_fixture(self, tmp_path: Path) -> None:
        """A REST-only fixture still needs a temp dataset (URL template).

        Phase 8 RAP fixtures sometimes need only ``setup_rest.json``
        (e.g. when the table is created via setup.sql but the RAP is
        added via REST). The runner must still provision a dataset so
        the ``${DATASET_ID}`` placeholder has something to expand to.
        """
        phase_dir = tmp_path / "row_access"
        fx_dir = phase_dir / "rest_only"
        fx_dir.mkdir(parents=True)
        (fx_dir / "query.sql").write_text("SELECT 1", encoding="utf-8")
        (fx_dir / "setup_rest.json").write_text(
            json.dumps([{"method": "POST", "path": "/x"}]),
            encoding="utf-8",
        )
        (fx_dir / "expected.json").write_text("{}", encoding="utf-8")

        fixtures = discover_fixtures(corpus_dir=tmp_path)
        assert fixtures[0].needs_dataset is True

    def test_malformed_setup_rest_raises(self, tmp_path: Path) -> None:
        phase_dir = tmp_path / "row_access"
        fx_dir = phase_dir / "bad_rest"
        fx_dir.mkdir(parents=True)
        (fx_dir / "query.sql").write_text("SELECT 1", encoding="utf-8")
        (fx_dir / "setup_rest.json").write_text("not json{{", encoding="utf-8")
        (fx_dir / "expected.json").write_text("{}", encoding="utf-8")
        with pytest.raises(ValueError, match="invalid JSON"):
            discover_fixtures(corpus_dir=tmp_path)

    def test_non_list_setup_rest_raises(self, tmp_path: Path) -> None:
        phase_dir = tmp_path / "row_access"
        fx_dir = phase_dir / "wrong_shape"
        fx_dir.mkdir(parents=True)
        (fx_dir / "query.sql").write_text("SELECT 1", encoding="utf-8")
        (fx_dir / "setup_rest.json").write_text('{"not": "a list"}', encoding="utf-8")
        (fx_dir / "expected.json").write_text("{}", encoding="utf-8")
        with pytest.raises(TypeError, match="must be a top-level list"):
            discover_fixtures(corpus_dir=tmp_path)

    def test_missing_method_in_setup_rest_raises(self, tmp_path: Path) -> None:
        phase_dir = tmp_path / "row_access"
        fx_dir = phase_dir / "missing_method"
        fx_dir.mkdir(parents=True)
        (fx_dir / "query.sql").write_text("SELECT 1", encoding="utf-8")
        (fx_dir / "setup_rest.json").write_text(json.dumps([{"path": "/foo"}]), encoding="utf-8")
        (fx_dir / "expected.json").write_text("{}", encoding="utf-8")
        with pytest.raises(ValueError, match="requires 'method' and 'path'"):
            discover_fixtures(corpus_dir=tmp_path)

    def test_non_object_headers_raises(self, tmp_path: Path) -> None:
        phase_dir = tmp_path / "row_access"
        fx_dir = phase_dir / "bad_headers"
        fx_dir.mkdir(parents=True)
        (fx_dir / "query.sql").write_text("SELECT 1", encoding="utf-8")
        (fx_dir / "headers.json").write_text(json.dumps(["X-Foo", "Y"]), encoding="utf-8")
        (fx_dir / "expected.json").write_text("{}", encoding="utf-8")
        with pytest.raises(TypeError, match="must be a top-level object"):
            discover_fixtures(corpus_dir=tmp_path)


class TestTrackDatasetCreation:
    """Both runner and recorder must spot ``POST /datasets`` for teardown."""

    @pytest.fixture(scope="class")
    def runner_module(self):
        """Load the conformance runner's REST helpers as a module."""
        import importlib.util
        from pathlib import Path

        path = Path(__file__).resolve().parents[2] / "conformance" / "test_corpus.py"
        spec = importlib.util.spec_from_file_location("_runner_under_test", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @pytest.fixture(scope="class")
    def recorder_module(self):
        """Load the recorder's REST helpers as a module."""
        import importlib.util
        from pathlib import Path

        path = Path(__file__).resolve().parents[3] / "scripts" / "record_conformance_fixtures.py"
        spec = importlib.util.spec_from_file_location("_recorder_track_test", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_runner_tracks_post_to_datasets(self, runner_module) -> None:
        result = runner_module._track_dataset_creation(
            method="POST",
            path="/bigquery/v2/projects/my-proj/datasets",
            body={"datasetReference": {"projectId": "my-proj", "datasetId": "second_ds"}},
        )
        assert result == ("my-proj", "second_ds")

    def test_runner_ignores_get_against_dataset_url(self, runner_module) -> None:
        """A GET to the dataset URL is a list, not a creation, and is ignored."""
        result = runner_module._track_dataset_creation(
            method="GET",
            path="/bigquery/v2/projects/my-proj/datasets",
            body=None,
        )
        assert result is None

    def test_runner_ignores_post_to_unrelated_path(self, runner_module) -> None:
        result = runner_module._track_dataset_creation(
            method="POST",
            path="/bigquery/v2/projects/my-proj/datasets/ds/tables/t/rowAccessPolicies",
            body={"datasetReference": {"projectId": "my-proj", "datasetId": "ds"}},
        )
        assert result is None

    def test_recorder_mirrors_runner_behaviour(self, recorder_module) -> None:
        """The recorder's tracker has the same contract — same inputs, same output."""
        ok = recorder_module._track_dataset_creation_bq(
            method="POST",
            path="/bigquery/v2/projects/proj-x/datasets",
            body={"datasetReference": {"projectId": "proj-x", "datasetId": "foo"}},
        )
        assert ok == ("proj-x", "foo")
        skip = recorder_module._track_dataset_creation_bq(
            method="PATCH",
            path="/bigquery/v2/projects/proj-x/datasets/foo",
            body={"access": []},
        )
        assert skip is None


class TestApplySetupRestAgainstEmulator:
    """End-to-end: the runner-side REST helper hits the in-process emulator."""

    def test_creates_rap_via_setup_rest_against_emulator(
        self,
        bqemu_endpoint,
    ) -> None:
        """Drive the in-process emulator through one ``setup_rest`` operation.

        Exercises the full runner pipeline: placeholder substitution
        in the URL, JSON-body substitution, dataset-creation tracking,
        and successful POST against the emulator's
        ``/rowAccessPolicies`` endpoint. Confirms the framework
        plumbing produces the same shape Phase 8 integration tests
        already exercise by hand.
        """
        import importlib.util
        from pathlib import Path
        import uuid

        from google.api_core.client_options import ClientOptions
        from google.auth.credentials import AnonymousCredentials
        from google.cloud import bigquery

        path = Path(__file__).resolve().parents[2] / "conformance" / "test_corpus.py"
        spec = importlib.util.spec_from_file_location("_apply_setup_rest_test", path)
        assert spec is not None and spec.loader is not None
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)

        project = bqemu_endpoint.project_id
        dataset_id = f"bqemu_unit_{uuid.uuid4().hex[:10]}"
        dataset_fqdn = f"{project}.{dataset_id}"

        client = bigquery.Client(
            project=project,
            credentials=AnonymousCredentials(),  # type: ignore[no-untyped-call]
            client_options=ClientOptions(api_endpoint=bqemu_endpoint.rest_url),
        )
        client.create_dataset(bigquery.Dataset(dataset_fqdn))
        try:
            # Bypass the BQ client's jobs.query polling (which is
            # flaky against the in-process emulator under pytest) by
            # using httpx directly to create the table via the REST
            # tables endpoint.
            import httpx

            with httpx.Client(base_url=bqemu_endpoint.rest_url, timeout=30.0) as http:
                create = http.post(
                    f"/bigquery/v2/projects/{project}/datasets/{dataset_id}/tables",
                    json={
                        "tableReference": {
                            "projectId": project,
                            "datasetId": dataset_id,
                            "tableId": "t",
                        },
                        "schema": {"fields": [{"name": "id", "type": "INT64"}]},
                    },
                )
                assert create.status_code == 200, create.text

            ctx = PlaceholderContext(
                dataset=dataset_fqdn,
                principal="user:test@example.com",
            )
            ops = (
                {
                    "method": "POST",
                    "path": (
                        "/bigquery/v2/projects/${PROJECT}/datasets/"
                        "${DATASET_ID}/tables/t/rowAccessPolicies"
                    ),
                    "body": {
                        "rowAccessPolicyReference": {
                            "projectId": "${PROJECT}",
                            "datasetId": "${DATASET_ID}",
                            "tableId": "t",
                            "policyId": "p1",
                        },
                        "filterPredicate": "id = 1",
                        "grantees": ["${PRINCIPAL}"],
                    },
                },
            )
            created = runner._apply_setup_rest(bqemu_endpoint.rest_url, ops, ctx)
            assert created == []

            # The policy is now visible via the emulator's GET path.
            import httpx

            with httpx.Client(base_url=bqemu_endpoint.rest_url) as http:
                response = http.get(
                    f"/bigquery/v2/projects/{project}/datasets/{dataset_id}"
                    "/tables/t/rowAccessPolicies",
                )
            assert response.status_code == 200
            policies = response.json().get("rowAccessPolicies", [])
            assert any(p["rowAccessPolicyReference"]["policyId"] == "p1" for p in policies)
        finally:
            client.delete_dataset(dataset_fqdn, delete_contents=True, not_found_ok=True)


class TestParametersDiscovery:
    """``parameters.json`` (P2.e) round-trips through ``discover_fixtures``."""

    def test_named_parameters_load_into_fixture(self, tmp_path: Path) -> None:
        phase_dir = tmp_path / "rest_crud"
        fx_dir = phase_dir / "param_named"
        fx_dir.mkdir(parents=True)
        (fx_dir / "query.sql").write_text("SELECT @n AS n", encoding="utf-8")
        (fx_dir / "parameters.json").write_text(
            json.dumps(
                {
                    "mode": "named",
                    "parameters": [{"name": "n", "type": "INT64", "value": 42}],
                }
            ),
            encoding="utf-8",
        )
        (fx_dir / "expected.json").write_text("{}", encoding="utf-8")
        fixtures = discover_fixtures(corpus_dir=tmp_path)
        assert len(fixtures) == 1
        assert fixtures[0].parameters is not None
        assert fixtures[0].parameters["mode"] == "named"
        assert fixtures[0].parameters["parameters"][0]["name"] == "n"

    def test_positional_parameters_load_into_fixture(self, tmp_path: Path) -> None:
        phase_dir = tmp_path / "rest_crud"
        fx_dir = phase_dir / "param_positional"
        fx_dir.mkdir(parents=True)
        (fx_dir / "query.sql").write_text("SELECT ? AS a", encoding="utf-8")
        (fx_dir / "parameters.json").write_text(
            json.dumps(
                {
                    "mode": "positional",
                    "parameters": [{"type": "STRING", "value": "hi"}],
                }
            ),
            encoding="utf-8",
        )
        (fx_dir / "expected.json").write_text("{}", encoding="utf-8")
        fixtures = discover_fixtures(corpus_dir=tmp_path)
        assert fixtures[0].parameters is not None
        assert fixtures[0].parameters["mode"] == "positional"

    def test_no_parameters_file_yields_none(self, tmp_path: Path) -> None:
        """Pre-P2.e fixtures (no parameters.json) get parameters=None."""
        phase_dir = tmp_path / "rest_crud"
        fx_dir = phase_dir / "literal"
        fx_dir.mkdir(parents=True)
        (fx_dir / "query.sql").write_text("SELECT 1", encoding="utf-8")
        (fx_dir / "expected.json").write_text("{}", encoding="utf-8")
        fixtures = discover_fixtures(corpus_dir=tmp_path)
        assert fixtures[0].parameters is None

    def test_malformed_parameters_json_raises(self, tmp_path: Path) -> None:
        phase_dir = tmp_path / "rest_crud"
        fx_dir = phase_dir / "bad"
        fx_dir.mkdir(parents=True)
        (fx_dir / "query.sql").write_text("SELECT 1", encoding="utf-8")
        (fx_dir / "parameters.json").write_text("not json{{", encoding="utf-8")
        (fx_dir / "expected.json").write_text("{}", encoding="utf-8")
        with pytest.raises(ValueError, match="invalid JSON"):
            discover_fixtures(corpus_dir=tmp_path)

    def test_non_object_parameters_json_raises(self, tmp_path: Path) -> None:
        phase_dir = tmp_path / "rest_crud"
        fx_dir = phase_dir / "bad_shape"
        fx_dir.mkdir(parents=True)
        (fx_dir / "query.sql").write_text("SELECT 1", encoding="utf-8")
        (fx_dir / "parameters.json").write_text(json.dumps(["a", "b"]), encoding="utf-8")
        (fx_dir / "expected.json").write_text("{}", encoding="utf-8")
        with pytest.raises(TypeError, match="must be a top-level object"):
            discover_fixtures(corpus_dir=tmp_path)

    def test_unknown_mode_raises(self, tmp_path: Path) -> None:
        phase_dir = tmp_path / "rest_crud"
        fx_dir = phase_dir / "bad_mode"
        fx_dir.mkdir(parents=True)
        (fx_dir / "query.sql").write_text("SELECT 1", encoding="utf-8")
        (fx_dir / "parameters.json").write_text(
            json.dumps({"mode": "MIXED", "parameters": []}),
            encoding="utf-8",
        )
        (fx_dir / "expected.json").write_text("{}", encoding="utf-8")
        with pytest.raises(ValueError, match="'mode' must be one of"):
            discover_fixtures(corpus_dir=tmp_path)

    def test_parameters_not_a_list_raises(self, tmp_path: Path) -> None:
        phase_dir = tmp_path / "rest_crud"
        fx_dir = phase_dir / "bad_params"
        fx_dir.mkdir(parents=True)
        (fx_dir / "query.sql").write_text("SELECT 1", encoding="utf-8")
        (fx_dir / "parameters.json").write_text(
            json.dumps({"mode": "named", "parameters": "oops"}),
            encoding="utf-8",
        )
        (fx_dir / "expected.json").write_text("{}", encoding="utf-8")
        with pytest.raises(TypeError, match="'parameters' must be a list"):
            discover_fixtures(corpus_dir=tmp_path)

    def test_named_parameter_without_name_raises(self, tmp_path: Path) -> None:
        phase_dir = tmp_path / "rest_crud"
        fx_dir = phase_dir / "missing_name"
        fx_dir.mkdir(parents=True)
        (fx_dir / "query.sql").write_text("SELECT 1", encoding="utf-8")
        (fx_dir / "parameters.json").write_text(
            json.dumps(
                {
                    "mode": "named",
                    "parameters": [{"type": "INT64", "value": 1}],
                }
            ),
            encoding="utf-8",
        )
        (fx_dir / "expected.json").write_text("{}", encoding="utf-8")
        with pytest.raises(ValueError, match="requires a string 'name'"):
            discover_fixtures(corpus_dir=tmp_path)

    def test_parameter_entry_without_type_raises(self, tmp_path: Path) -> None:
        phase_dir = tmp_path / "rest_crud"
        fx_dir = phase_dir / "missing_type"
        fx_dir.mkdir(parents=True)
        (fx_dir / "query.sql").write_text("SELECT 1", encoding="utf-8")
        (fx_dir / "parameters.json").write_text(
            json.dumps(
                {
                    "mode": "positional",
                    "parameters": [{"value": 1}],
                }
            ),
            encoding="utf-8",
        )
        (fx_dir / "expected.json").write_text("{}", encoding="utf-8")
        with pytest.raises(ValueError, match="requires 'type'"):
            discover_fixtures(corpus_dir=tmp_path)


class TestBuildQueryParameters:
    """``build_query_parameters`` converts JSON payloads to BQ ``QueryParameter`` objects."""

    def test_named_scalar_int64(self) -> None:
        from tests.conformance._parameters import build_query_parameters

        payload = {
            "mode": "named",
            "parameters": [{"name": "n", "type": "INT64", "value": 42}],
        }
        params = build_query_parameters(payload)
        assert len(params) == 1
        assert params[0].name == "n"
        assert params[0].type_ == "INT64"
        assert params[0].value == 42

    def test_positional_scalar_string(self) -> None:
        from tests.conformance._parameters import build_query_parameters

        payload = {
            "mode": "positional",
            "parameters": [{"type": "STRING", "value": "hello"}],
        }
        params = build_query_parameters(payload)
        assert params[0].name is None  # positional parameters carry name=None
        assert params[0].type_ == "STRING"
        assert params[0].value == "hello"

    def test_named_array_int64(self) -> None:
        from tests.conformance._parameters import build_query_parameters

        payload = {
            "mode": "named",
            "parameters": [
                {
                    "name": "ids",
                    "type": {"type": "ARRAY", "arrayType": {"type": "INT64"}},
                    "value": [1, 2, 3],
                }
            ],
        }
        params = build_query_parameters(payload)
        assert params[0].name == "ids"
        assert params[0].array_type == "INT64"
        assert list(params[0].values) == [1, 2, 3]

    def test_named_struct_round_trip(self) -> None:
        from tests.conformance._parameters import build_query_parameters

        payload = {
            "mode": "named",
            "parameters": [
                {
                    "name": "profile",
                    "type": {
                        "type": "STRUCT",
                        "structTypes": [
                            {"name": "name", "type": "STRING"},
                            {"name": "age", "type": "INT64"},
                        ],
                    },
                    "value": {"name": "Alice", "age": 30},
                }
            ],
        }
        params = build_query_parameters(payload)
        assert params[0].name == "profile"
        # StructQueryParameter exposes sub-fields via .struct_values
        assert params[0].struct_values["name"] == "Alice"
        assert params[0].struct_values["age"] == 30

    def test_null_scalar_passes_through(self) -> None:
        from tests.conformance._parameters import build_query_parameters

        payload = {
            "mode": "named",
            "parameters": [{"name": "x", "type": "INT64", "value": None}],
        }
        params = build_query_parameters(payload)
        assert params[0].value is None

    def test_unknown_scalar_type_raises(self) -> None:
        from tests.conformance._parameters import build_query_parameters

        payload = {
            "mode": "named",
            "parameters": [{"name": "x", "type": "NOTATYPE", "value": 1}],
        }
        with pytest.raises(ValueError, match="Unknown scalar type"):
            build_query_parameters(payload)

    def test_unknown_compound_kind_raises(self) -> None:
        from tests.conformance._parameters import build_query_parameters

        payload = {
            "mode": "named",
            "parameters": [{"name": "x", "type": {"type": "WEIRD"}, "value": None}],
        }
        with pytest.raises(ValueError, match="Unknown compound type kind"):
            build_query_parameters(payload)

    def test_array_element_type_lowercase_normalises(self) -> None:
        """Element types are normalised to upper-case before BQ submission."""
        from tests.conformance._parameters import build_query_parameters

        payload = {
            "mode": "named",
            "parameters": [
                {
                    "name": "labels",
                    "type": {"type": "ARRAY", "arrayType": {"type": "string"}},
                    "value": ["a", "b"],
                }
            ],
        }
        params = build_query_parameters(payload)
        assert params[0].array_type == "STRING"

    def test_positional_array_emits_no_name(self) -> None:
        from tests.conformance._parameters import build_query_parameters

        payload = {
            "mode": "positional",
            "parameters": [
                {
                    "type": {"type": "ARRAY", "arrayType": {"type": "INT64"}},
                    "value": [9, 8, 7],
                }
            ],
        }
        params = build_query_parameters(payload)
        assert params[0].name is None
        assert list(params[0].values) == [9, 8, 7]


class TestRunnerBuildJobConfig:
    """The runner's ``_build_job_config`` produces a ``QueryJobConfig`` or ``None``."""

    @pytest.fixture(scope="class")
    def runner_module(self):
        """Load the conformance runner module so its private helpers are testable."""
        import importlib.util

        path = Path(__file__).resolve().parents[2] / "conformance" / "test_corpus.py"
        spec = importlib.util.spec_from_file_location("_runner_build_job_test", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_no_parameters_yields_none(self, runner_module, tmp_path: Path) -> None:
        from tests.conformance._corpus import Fixture

        fixture = Fixture(
            phase="rest_crud",
            name="literal",
            path=tmp_path,
            query_sql="SELECT 1",
            setup_sql=None,
            expected_path=tmp_path / "expected.json",
        )
        ctx = PlaceholderContext(dataset="p.ds")
        assert runner_module._build_job_config(fixture, ctx) is None

    def test_with_parameters_yields_job_config(self, runner_module, tmp_path: Path) -> None:
        from tests.conformance._corpus import Fixture

        fixture = Fixture(
            phase="rest_crud",
            name="param",
            path=tmp_path,
            query_sql="SELECT @n AS n",
            setup_sql=None,
            expected_path=tmp_path / "expected.json",
            parameters={
                "mode": "named",
                "parameters": [{"name": "n", "type": "INT64", "value": 7}],
            },
        )
        ctx = PlaceholderContext(dataset="p.ds")
        cfg = runner_module._build_job_config(fixture, ctx)
        assert cfg is not None
        assert len(cfg.query_parameters) == 1
        assert cfg.query_parameters[0].name == "n"
        assert cfg.query_parameters[0].value == 7

    def test_parameters_pass_through_placeholder_substitution(
        self, runner_module, tmp_path: Path
    ) -> None:
        """A ``${…}`` placeholder inside a parameter value expands via substitute_in_json.

        None of the shipping P2.e fixtures actually use placeholders
        inside parameter values, but the pattern matches setup_rest.json
        bodies so the framework is consistent.
        """
        from tests.conformance._corpus import Fixture

        fixture = Fixture(
            phase="rest_crud",
            name="param_placeholder",
            path=tmp_path,
            query_sql="SELECT @who AS who",
            setup_sql=None,
            expected_path=tmp_path / "expected.json",
            parameters={
                "mode": "named",
                "parameters": [{"name": "who", "type": "STRING", "value": "${PRINCIPAL}"}],
            },
        )
        ctx = PlaceholderContext(
            dataset="p.ds",
            principal="user:alice@example.com",
        )
        cfg = runner_module._build_job_config(fixture, ctx)
        assert cfg is not None
        assert cfg.query_parameters[0].value == "user:alice@example.com"


class TestRecorderBuildJobConfig:
    """The recorder's ``_build_job_config`` mirrors the runner's contract."""

    @pytest.fixture(scope="class")
    def recorder_module(self):
        import importlib.util

        path = Path(__file__).resolve().parents[3] / "scripts" / "record_conformance_fixtures.py"
        spec = importlib.util.spec_from_file_location("_recorder_build_job_test", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_no_parameters_yields_none(self, recorder_module, tmp_path: Path) -> None:
        from google.cloud import bigquery

        from tests.conformance._corpus import Fixture

        fixture = Fixture(
            phase="rest_crud",
            name="literal",
            path=tmp_path,
            query_sql="SELECT 1",
            setup_sql=None,
            expected_path=tmp_path / "expected.json",
        )
        ctx = PlaceholderContext(dataset="p.ds")
        assert recorder_module._build_job_config(fixture, ctx, bigquery) is None

    def test_with_parameters_yields_job_config(self, recorder_module, tmp_path: Path) -> None:
        from google.cloud import bigquery

        from tests.conformance._corpus import Fixture

        fixture = Fixture(
            phase="rest_crud",
            name="param",
            path=tmp_path,
            query_sql="SELECT ? AS s",
            setup_sql=None,
            expected_path=tmp_path / "expected.json",
            parameters={
                "mode": "positional",
                "parameters": [{"type": "STRING", "value": "hi"}],
            },
        )
        ctx = PlaceholderContext(dataset="p.ds")
        cfg = recorder_module._build_job_config(fixture, ctx, bigquery)
        assert cfg is not None
        assert len(cfg.query_parameters) == 1
        assert cfg.query_parameters[0].value == "hi"


class TestRecorderReferencesGcsBucket:
    """The recorder's ``${GCS_BUCKET}`` needs-bucket guard predicate (RFC 0001)."""

    @pytest.fixture(scope="class")
    def recorder_module(self):
        import importlib.util

        path = Path(__file__).resolve().parents[3] / "scripts" / "record_conformance_fixtures.py"
        spec = importlib.util.spec_from_file_location("_recorder_gcs_bucket_test", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_query_sql_reference_detected(self, recorder_module, tmp_path: Path) -> None:
        """An EXPORT DATA query that templates ``${GCS_BUCKET}`` is flagged."""
        from tests.conformance._corpus import Fixture

        fixture = Fixture(
            phase="export_data",
            name="export_csv_basic",
            path=tmp_path,
            query_sql="EXPORT DATA OPTIONS (uri='gs://${GCS_BUCKET}/e/*.csv') AS SELECT 1",
            setup_sql=None,
            expected_path=tmp_path / "expected.json",
        )
        assert recorder_module._references_gcs_bucket(fixture) is True

    def test_setup_sql_reference_detected(self, recorder_module, tmp_path: Path) -> None:
        """A reference only in ``setup.sql`` (query.sql clean) is still flagged."""
        from tests.conformance._corpus import Fixture

        fixture = Fixture(
            phase="export_data",
            name="export_setup_ref",
            path=tmp_path,
            query_sql="SELECT 1",
            setup_sql="EXPORT DATA OPTIONS (uri='gs://${GCS_BUCKET}/e/*.csv') AS SELECT 1",
            expected_path=tmp_path / "expected.json",
        )
        assert recorder_module._references_gcs_bucket(fixture) is True

    def test_no_reference_returns_false(self, recorder_module, tmp_path: Path) -> None:
        """A fixture with neither a query nor a setup reference is not flagged."""
        from tests.conformance._corpus import Fixture

        fixture = Fixture(
            phase="rest_crud",
            name="plain_select",
            path=tmp_path,
            query_sql="SELECT 1 AS x",
            setup_sql="CREATE OR REPLACE TABLE `p.ds.t` (id INT64)",
            expected_path=tmp_path / "expected.json",
        )
        assert recorder_module._references_gcs_bucket(fixture) is False
