# EXPORT DATA conformance fixture recording — operator-side checklist

The `export_data` fixtures (RFC 0001 / ADR 0043) record the
GoogleSQL `EXPORT DATA OPTIONS(...) AS SELECT` statement against real
BigQuery. They span **two corpora**, recorded by **two scripts**:

| Corpus | Path | Recorder | Pins |
|---|---|---|---|
| SQL | `sql_corpus/export_data/export_*` | `scripts/record_conformance_fixtures.py` | empty result + `job_metadata.statement_type = EXPORT_DATA`; error envelopes |
| HTTP | `http_corpus/jobs/export_*` | `scripts/record_http_fixtures.py` | REST job resource — `configuration.query` + `statistics.query.statementType` |

Both reference `gs://${GCS_BUCKET}/export/<name>/*.<ext>` URIs. Unlike
the G1 load/extract fixtures, **no input file needs pre-staging** — the
recorder writes the destination object as a side-effect of running the
statement. The destination is templated through `${GCS_BUCKET}` so no
private bucket name lands in version control.

## Prerequisites

1. **BigQuery ADC** — `gcloud auth application-default login` against
   the project named by `BQEMU_CONFORMANCE_PROJECT`.
2. **A GCS bucket the ADC principal can write.** Export its name as
   `BQEMU_CONFORMANCE_GCS_BUCKET` (no `gs://` prefix). The recorder
   fails fast with an actionable message if an `export_*` fixture is
   selected while this is unset.
3. **Python deps** — `pip install bqemulator[avro]` (the AVRO fixture
   exercises the avro extension) plus the recorder script's imports.

All fixtures use `overwrite = true`, so re-recording is idempotent —
the destination object is overwritten rather than tripping BigQuery's
"destination already exists" guard.

## Record

```bash
export BQEMU_CONFORMANCE_PROJECT=<your-project>
export BQEMU_CONFORMANCE_GCS_BUCKET=<your-writable-bucket>   # no gs:// prefix

# SQL corpus — empty result + statement_type baselines.
python scripts/record_conformance_fixtures.py \
    --project "$BQEMU_CONFORMANCE_PROJECT" \
    --filter export_data/

# HTTP corpus — REST job-resource shape (statementType + statistics).
python scripts/record_http_fixtures.py \
    --project "$BQEMU_CONFORMANCE_PROJECT" \
    --filter export_
```

Each fixture's baseline (`expected.json` for SQL, `expected_response.json`
for HTTP) is written next to its inputs. The two error candidates
(`export_missing_uri`, `export_orc_rejected`) record a version-2 error
envelope instead of a result; if BigQuery does **not** reject one of
them, drop or re-scope that fixture rather than committing a
non-error baseline.

## Scrub before committing

The recorder writes the neutral `your-bigquery-project` placeholder
into the SQL `bigquery.project` field and wildcards opaque values in
the HTTP baselines, but **read every generated file before staging**:

- No real project id, bucket name, job id, or principal in any
  committed JSON (the SQL baselines carry no URI; the HTTP baselines
  wildcard server-generated values as `<*>`).
- Error `message_sample` / `message_pattern` must not embed the real
  project — the recorder scrubs the billing project, but verify.

## Regenerate the coverage matrix + verify

```bash
make coverage-matrix        # ddl.export_data now counts the recorded fixtures
make coverage-matrix-check  # must be clean (CI gate)
make test-conformance -k export
```

All `export_*` fixtures should pass. Pin any emulator-vs-BigQuery
divergence via [`tests/conformance/divergences.py`](../../divergences.py)
(see ADR 0023) rather than hand-editing a baseline.

## Sharding caveat

BigQuery's real >1 GB multi-file sharding cannot be feasibly triggered
during recording, so every fixture targets a **single shard**
(`<prefix>/000000000000.<ext>`). Multi-shard behaviour is covered by
the emulator's unit + property tiers with a small
`export_shard_threshold_bytes` — see ADR 0043.
