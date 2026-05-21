# Conformance HTTP corpus (P2.f)

Sibling of `sql_corpus/`. Each directory under this folder is one
**HTTP-shape conformance fixture**: a non-SQL REST response shape
whose recorded baseline from real BigQuery is the ground-truth the
emulator is diffed against.

Pagination, job lifecycle (`jobs.get` / `jobs.list` / `jobs.cancel` /
`jobs.delete`), and `dryRun` preview semantics live here — they
exercise wire-format details the row+schema diff in `sql_corpus/`
cannot express. See [`docs/roadmap/v1-confidence-plan.md`](../../../docs/roadmap/v1-confidence-plan.md)
**P2.f** for the workstream scope.

## Layout

    tests/conformance/http_corpus/
        <phase>/
            <fixture_name>/
                setup.sql               # optional — table seed run before any REST call
                setup_requests.json     # optional — ordered REST calls run before the canonical request
                request.json            # required — the canonical REST call diffed against the baseline
                expected_response.json  # generated — recorded baseline (status + body, optional header subset)

`<phase>` corresponds to a phase in the roadmap and groups fixtures
by the subsystem they exercise:

| Subdir | Exercises |
|---|---|
| `jobs` | pagination, jobs.cancel / jobs.list / jobs.get / jobs.delete response shapes, dryRun preview semantics |

## Authoring

1. **Placeholders are UPPER-CASE only.** The runner and recorder
   accept the shared corpus set (`${PROJECT}`, `${DATASET}`,
   `${DATASET_ID}`, `${PRINCIPAL}`, `${GROUP}`, `${OTHER_PRINCIPAL}`)
   plus any extra tokens captured from prior `setup_requests`
   responses.
2. **`setup_requests.json` is an ordered list of REST calls.** Each
   entry carries `method`, `path`, optional `body`, optional
   `headers`, and optional `capture`. `capture` is a map of
   `UPPER_SNAKE` token → dotted JSON path inside the response body.
   Captured tokens are usable in any later setup request and in the
   canonical `request.json`.
3. **`request.json` is the canonical request.** It's the only call
   whose response is diffed against `expected_response.json`. Setup
   requests are functional — their responses feed substitution but
   are not pinned.
4. **`expected_response.json` is the recorded baseline.** It carries:
   - `http_status` — exact match.
   - `headers` — subset match (only the listed headers are checked;
     BigQuery's opaque ones drift between recordings).
   - `body` — **structural subset** match. Recorded keys must be
     present in the actual body; extra emulator-side keys are
     tolerated. Server-generated opaque values (job ids, etags,
     timestamps, opaque self-links) are masked to the
     `"<*>"` sentinel; the comparator only checks they're present.
5. **Single canonical request.** Don't try to thread a 3-call
   sequence through one fixture — split it into two fixtures or use
   `setup_requests` for the preamble. One fixture asserts one shape.

## Wildcards and masking

The recorder writes `"<*>"` at every leaf whose value is server-
generated. The comparator treats this as "key may be absent or
present; value not checked". The current masked path set lives in
[`scripts/record_http_fixtures.py`](../../../scripts/record_http_fixtures.py)
under `VOLATILE_PATHS`; add new ones there when a recording surfaces
a key whose value drifts between runs (e.g. statistics timing fields,
opaque ids, etags).

## Determinism

Like the SQL corpus (ADR 0022), HTTP fixtures must be deterministic:

- No `CURRENT_TIMESTAMP()` / `RAND()` in setup SQL.
- No fixture that depends on a job being in the RUNNING state — the
  emulator executes synchronously, so a "running" job is unreachable.
  Test the DONE shape instead.
- No fixture that depends on rate-limiting or quota responses — those
  are non-portable.

## Running

    make test-conformance          # both SQL + HTTP corpora
    pytest tests/conformance/test_http_corpus.py -m conformance

## Recording

    BQEMU_CONFORMANCE_PROJECT=<project> GOOGLE_APPLICATION_CREDENTIALS=<key.json> \
        python scripts/record_http_fixtures.py --project "$BQEMU_CONFORMANCE_PROJECT"

Re-record one fixture:

    python scripts/record_http_fixtures.py \
        --project "$BQEMU_CONFORMANCE_PROJECT" \
        --filter jobs/page_first_page_only \
        --force
