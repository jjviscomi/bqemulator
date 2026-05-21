"""Fuzz harnesses for bqemulator (v1 confidence-plan workstream P3.c).

Each module in this package is a standalone Atheris harness exercising one
of three high-attack-surface translator entry points:

* ``fuzz_sql_translator.py`` — the BigQuery → DuckDB SQL translator.
* ``fuzz_dyn_proto.py`` — the dynamic-protobuf row deserialiser used by
  the Storage Write API servicer.
* ``fuzz_arrow_bridge.py`` — the Arrow ↔ BigQuery REST JSON row bridge
  plus the Arrow IPC deserialiser used by the Storage Write API
  ``arrow_rows`` path.

The harnesses are run via ``make test-fuzz`` (locally) or the manual-only
``fuzz.yml`` workflow (in CI). The tier is intentionally not part of the
standard 7-tier pyramid — it shares the property-tier discipline rather
than asserting fresh invariants. See ADR 0031 for the design contract.

Python-version note: Atheris 3.0.0 supports Python 3.11-3.13. The dev-box
runs Python 3.14, which Atheris does NOT yet support — ``make test-fuzz``
requires a 3.11/3.12/3.13 venv.
"""
