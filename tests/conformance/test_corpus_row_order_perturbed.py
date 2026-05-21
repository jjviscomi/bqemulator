"""Differential conformance runner — row-order perturbation (workstream P8.f).

The differential tier (ADR 0028) re-runs the conformance corpus with
the order of setup-data row insertions **reversed** and asserts the
emulator still produces the recorded baseline result set under
canonical row sorting. A fixture that diverges under perturbation
exposes emulator logic that silently depends on storage order — the
fixture-specific-shortcut bug class — which the parity-locked
conformance baseline cannot surface (because BigQuery and DuckDB
happened to return rows in compatible orders for the *recorded*
setup data).

This module ships **row-order perturbation only** (mode A in ADR
0028 §"Perturbation taxonomy"). Modes B (value-shift) and C
(schema-reorder) require operator BigQuery time to re-record
perturbed-sibling fixtures and are deferred to v1.0.x.

The runner is gated on the ``differential`` pytest marker and runs
via ``make test-differential``. The differential CI workflow
([`.github/workflows/differential.yml`](https://github.com/jjviscomi/bqemulator/blob/main/.github/workflows/differential.yml))
ships as ``workflow_dispatch``-only for v1.0; the cadence decision
(per-PR vs nightly vs release-gate) is deferred until post-repo-setup
when there is real CI traffic to measure runtime / flakiness against.

Failure mode taxonomy (see ADR 0028 §"Triage protocol"):

* **Emulator bug** — fix inline; remove from skip-list.
* **Fixture has implicit row-order assumption** — add explicit
  ``ORDER BY`` to ``query.sql``, re-record against BigQuery
  (deferred to v1.0.x if operator credentials unavailable).
* **Row order is semantically meaningful** — add to
  :data:`tests.conformance._row_order_perturbation.PERTURBATION_SKIP_LIST`
  with a citation to an ADR or ``out-of-scope.md`` anchor.

The skip-list is intentionally bounded; every entry is reviewed
during the PR.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
import uuid

import pytest

from tests.conformance._comparison import (
    compare_error,
    compare_results,
    extract_actual_error,
)
from tests.conformance._corpus import (
    DEFAULT_RUNNER_GROUP,
    DEFAULT_RUNNER_OTHER_PRINCIPAL,
    DEFAULT_RUNNER_PRINCIPAL,
    Fixture,
    PlaceholderContext,
    discover_fixtures,
    split_statements,
    substitute_placeholders,
)
from tests.conformance._row_order_perturbation import (
    canonical_row_key,
    is_perturbable,
    reverse_insert_values,
)

if TYPE_CHECKING:  # pragma: no cover
    from bqemulator.testing.fixtures import EmulatorEndpoint

ALL_FIXTURES = discover_fixtures()


def _parametrize_perturbable() -> list[pytest.param]:
    """Build the parametrise-list of perturbable fixtures.

    A fixture is included iff
    :func:`tests.conformance._row_order_perturbation.is_perturbable`
    returns ``(True, "")``. The non-perturbable fixtures are NOT
    parametrised in (rather than skipped during the test body) so
    pytest's progress report only lists fixtures the runner actually
    exercises — a 1141-fixture skipped-list would drown the genuine
    differential signal.
    """
    out: list[pytest.param] = []
    for fixture in ALL_FIXTURES:
        ok, _reason = is_perturbable(fixture)
        if ok:
            out.append(pytest.param(fixture, id=fixture.id))
    return out


def _perturbable_count() -> int:
    """Diagnostic helper: how many fixtures the runner exercises."""
    return sum(1 for f in ALL_FIXTURES if is_perturbable(f)[0])


@pytest.mark.parametrize("fixture", _parametrize_perturbable())
def test_row_order_perturbed_fixture(
    fixture: Fixture,
    bqemu_endpoint: EmulatorEndpoint,
) -> None:
    """Replay ``fixture`` with setup rows inserted in reverse order.

    The perturbed setup is byte-for-byte the original ``setup.sql``
    with every ``INSERT … VALUES (t1), (t2), …`` rewritten to
    ``INSERT … VALUES (tN), …, (t1)``. The query runs unchanged.
    The expected baseline is the recorded ``expected.json``;
    canonical row sorting is applied to both sides so a row-order
    difference between BigQuery and the emulator (allowed by
    BigQuery's contract on ``ORDER BY``-less queries) does not flag
    as a divergence — only a **row-content** difference does.

    A fixture that returns the same row set under perturbation is
    storage-order-independent; a fixture that returns a *different*
    row set (different count, different content) under perturbation
    exposes a shortcut bug.
    """
    expected = json.loads(fixture.expected_path.read_text(encoding="utf-8"))
    expected_error = expected.get("error")
    bq_job = expected.get("bigquery", {}).get("job_id", "<no job_id>")

    actual_rows, actual_schema, actual_error = _execute_perturbed_fixture(fixture, bqemu_endpoint)

    if expected_error is not None:
        if actual_error is None:
            pytest.fail(
                f"Row-order perturbation kind mismatch for {fixture.id} "
                f"(BQ job {bq_job}): expected an error envelope but emulator "
                f"succeeded with {len(actual_rows)} row(s)"
            )
        report = compare_error(expected_error, actual_error)
        if not report.ok:
            diagnostic = "\n".join(report.diffs)
            pytest.fail(
                f"Row-order perturbation error diff for {fixture.id} "
                f"(BQ job {bq_job}):\n{diagnostic}"
            )
        return

    if actual_error is not None:
        pytest.fail(
            f"Row-order perturbation kind mismatch for {fixture.id} "
            f"(BQ job {bq_job}): expected rows + schema but emulator raised: "
            f"{actual_error.get('message', '<no message>')}"
        )

    sorted_expected = _sort_for_canonical_diff(expected, actual_schema)
    sorted_actual_rows = sorted(actual_rows, key=canonical_row_key)

    report = compare_results(sorted_expected, sorted_actual_rows, actual_schema)
    if not report.ok:
        diagnostic = "\n".join(report.diffs)
        pytest.fail(
            f"Row-order perturbation diff for {fixture.id} (BQ job {bq_job}):\n"
            f"{diagnostic}\n"
            f"Triage: emulator returned different rows when setup data was "
            f"inserted in reverse order. Either a storage-order shortcut in "
            f"the emulator OR an implicit row-order assumption in the "
            f"fixture. See ADR 0028 §'Triage protocol'."
        )


def _sort_for_canonical_diff(
    expected: dict[str, Any],
    actual_schema: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return a copy of ``expected`` with rows canonical-sorted and job_metadata stripped.

    The runner sorts both the recorded ``expected.rows`` and the
    emulator's actual rows so a row-order difference (permitted on
    ``ORDER BY``-less queries) doesn't surface as a divergence —
    only a row-content difference does.

    The optional ``job_metadata`` block recorded by P7.a is stripped
    here because the differential tier's contract is row-content
    parity under perturbation, NOT response-object equivalence —
    the latter is the canonical conformance runner's domain (and
    the perturbed runner intentionally doesn't capture the
    ``statement_type`` / ``num_dml_affected_rows`` / etc. fields).

    The ``actual_schema`` argument is accepted for symmetry with
    :func:`compare_results`; it is not consulted directly because
    the canonical sort key uses the row's serialised form.
    """
    del actual_schema  # the canonical sort key doesn't need schema.
    expected_copy = dict(expected)
    expected_copy["rows"] = sorted(expected.get("rows", []), key=canonical_row_key)
    expected_copy.pop("job_metadata", None)
    return expected_copy


def _execute_perturbed_fixture(
    fixture: Fixture, endpoint: EmulatorEndpoint
) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, Any] | None]:
    """Run the fixture's query against a perturbed-setup dataset.

    The perturbation is applied to the post-substitution setup.sql
    (so ``${DATASET}`` resolves before the parser sees the script).
    Statement splitting then proceeds as in the canonical runner,
    and each statement runs sequentially. The query is replayed
    unchanged via the BigQuery Python client.

    Returns a 3-tuple ``(rows, schema, error)`` mirroring the
    canonical runner's shape minus the job-metadata block (the
    differential tier does not assert response-metadata equivalence
    — that's the canonical conformance runner's domain).
    """
    from google.api_core.client_options import ClientOptions
    from google.api_core.exceptions import GoogleAPIError
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery

    project = endpoint.project_id
    setup_client = bigquery.Client(
        project=project,
        credentials=AnonymousCredentials(),  # type: ignore[no-untyped-call]
        client_options=ClientOptions(api_endpoint=endpoint.rest_url),
    )

    dataset_name = f"bqemu_diff_{uuid.uuid4().hex[:12]}"
    dataset_fqdn = f"{project}.{dataset_name}"
    setup_client.create_dataset(bigquery.Dataset(dataset_fqdn))

    ctx = PlaceholderContext(
        dataset=dataset_fqdn,
        principal=DEFAULT_RUNNER_PRINCIPAL,
        group=DEFAULT_RUNNER_GROUP,
        other_principal=DEFAULT_RUNNER_OTHER_PRINCIPAL,
    )

    actual_rows: list[dict[str, object]] = []
    actual_schema: list[dict[str, object]] = []
    actual_error: dict[str, Any] | None = None
    try:
        assert fixture.setup_sql is not None  # narrowing — is_perturbable guarantees this
        substituted = substitute_placeholders(fixture.setup_sql, ctx)
        perturbed = reverse_insert_values(substituted)
        for stmt in split_statements(perturbed):
            setup_client.query(stmt).result()

        query_sql = substitute_placeholders(fixture.query_sql, ctx)
        try:
            query_job = setup_client.query(query_sql)
            result = query_job.result()
        except GoogleAPIError as exc:
            actual_error = extract_actual_error(exc)
        else:
            actual_rows = _result_to_rows(result)
            actual_schema = _result_to_schema(result)
    finally:
        setup_client.delete_dataset(dataset_fqdn, delete_contents=True, not_found_ok=True)
    return actual_rows, actual_schema, actual_error


def _result_to_rows(result: object) -> list[dict[str, object]]:
    """Convert a BigQuery ``QueryResult`` into JSON-friendly rows.

    Mirrors :func:`tests.conformance.test_corpus._result_to_rows` —
    duplicated here rather than imported because the canonical
    runner's helpers are private. Refactoring them into a shared
    helper would touch the canonical runner and is out of scope for
    P8.f's "infrastructure-level, medium risk" budget.
    """
    from tests.conformance._row_encoding import row_to_jsonable

    rows: list[dict[str, object]] = []
    for row in result:  # type: ignore[attr-defined]
        encoded: dict[str, object] = {}
        for field_def, value in zip(result.schema, row.values(), strict=True):  # type: ignore[attr-defined]
            encoded[field_def.name] = row_to_jsonable(value, field_def)
        rows.append(encoded)
    return rows


def _result_to_schema(result: object) -> list[dict[str, object]]:
    """Convert a BigQuery ``QueryResult.schema`` into the recorded shape."""
    from tests.conformance._row_encoding import field_to_jsonable

    return [field_to_jsonable(f) for f in result.schema]  # type: ignore[attr-defined]


def test_perturbable_fixtures_are_discovered() -> None:
    """Pin the perturbable-fixture count so a corpus refactor surfaces here.

    The differential tier's value is proportional to coverage — if a
    refactor accidentally drops the perturbable count to single
    digits, this test fails and forces investigation. The current
    floor (50) is set well below the actual count (~150-200) so
    typical corpus growth doesn't bounce the floor; the floor lives
    as a floor, not as an exact match.
    """
    count = _perturbable_count()
    assert count >= 50, (
        f"differential tier exercises only {count} fixtures — "
        "expected ≥ 50. A corpus refactor may have broken the "
        "perturbation eligibility checks."
    )
