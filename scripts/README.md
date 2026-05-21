# scripts/

Project automation scripts. Invoked from the Makefile, CI workflows, and
the release process.

All scripts must be:

- POSIX-shell-safe or pure Python (no Bash-4 features like `associative
  arrays` unless the script is clearly flagged Bash-only).
- Executable (`chmod +x`) and start with a proper shebang.
- Idempotent where feasible — safe to re-run on the same inputs.
- Print what they are doing, to stderr when interactive, stdout when
  producing machine-readable output.

## Planned scripts (added in phases)

| Script | Phase | Purpose |
|---|---|---|
| `generate_protos.sh` | 4 | Vendor + compile googleapis BigQuery Storage protos |
| `generate_compatibility_matrix.py` | 6 | Build `docs/reference/compatibility-matrix.md` from test results |
| `generate_function_mapping.py` | 6 | Build `docs/reference/sql-function-mapping.md` from SQL rule registry |
| `record_conformance_fixtures.py` | 11 | Record real-BigQuery snapshots for `tests/conformance/` |
| `release.py` | 11 | End-to-end release orchestration (tag + changelog + publish) |
| `bump_version.py` | 11 | Bump `__version__` + commit |
| `changelog.py` | 11 | Finalize `Unreleased` section of `CHANGELOG.md` from commits |
