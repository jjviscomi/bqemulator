# G3 Avro Storage Read conformance fixture recording — operator-side checklist

The six G3 fixtures (`read_session_avro_*`) shipped with synthesized
expected_response.json baselines that mirror the post-mask structural
shape of real BigQuery responses (every server-generated opaque field
is `WILDCARD`, so the shape diff catches drift but doesn't compare
runtime identities). The baselines are intentionally
re-recordable from real BigQuery once an operator has ADC credentials.

## When to re-record

Re-record whenever the gRPC servicer's Avro-path shape changes — e.g.
when the Storage Read protos add a new field that real BQ populates
that the emulator doesn't yet emit. The structural-subset comparator
makes added-field drift the failure mode.

## Prerequisites

1. **BigQuery ADC** — `gcloud auth application-default login` against
   the project named by `BQEMU_CONFORMANCE_PROJECT`.
2. **Python deps** — `pip install -e .[avro,testing]`.

## Run the recorder

```bash
make record-grpc-conformance
# or directly:
python scripts/record_grpc_fixtures.py \
    --project "$BQEMU_CONFORMANCE_PROJECT" \
    --location US \
    --force \
    --filter read_session_avro
```

The recorder will:

1. Provision a per-fixture temp dataset on the operator's project.
2. Execute `setup.sql` to seed the table.
3. Issue the canonical gRPC call sequence in `request.json` against
   `bigquerystorage.googleapis.com:443`.
4. Mask every server-generated opaque field (session name, stream
   names, Avro schema string, Avro row bytes, timing stats) to the
   `WILDCARD` sentinel.
5. Write the recorded baseline to `expected_response.json`.

## Cross-implementation Avro interop (ADR 0030 §6)

After re-recording, also re-run the integration test
`test_avro_schema_converter_against_reference_file`. This catches
the case where real BQ's Avro schema for a given table shape no
longer matches the reference files under `tests/fixtures/avro/`.
If it diverges, regenerate the reference files via
`make generate-avro-fixtures` and update
`tests/fixtures/avro/_schemas.py` to match the new shape.
