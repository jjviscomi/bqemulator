r"""Atheris harness for :class:`bqemulator.sql.translator.SQLTranslator`.

Contract: ``SQLTranslator.translate`` must always return a
:class:`bqemulator.domain.result.Result` instance — either
``Ok(duckdb_sql)`` or ``Err(InvalidQueryError | UnsupportedFeatureError)``.
Any uncaught Python exception that is NOT a
:class:`bqemulator.domain.errors.DomainError` is a bug — the translator
is the project's outermost-input boundary for SQL and is contractually
required to convert every parse failure into a clean ``Err``. The
fuzzer's job is to find inputs that escape that contract.

The harness is run via ``make test-fuzz`` locally (one minute per
harness) or by the manual-dispatch ``fuzz.yml`` workflow in CI (ten
minutes per harness). See ADR 0031 for the design contract.

Run directly::

    python fuzz/fuzz_sql_translator.py -max_total_time=60 \
        fuzz/corpus/sql_translator
"""

from __future__ import annotations

import sys

import atheris

# ``instrument_imports`` must wrap the imports that own the code paths
# we want libFuzzer's coverage map to instrument. The translator + its
# transitive dependencies (sqlglot, the rewriter modules, the rules
# package) are the surface; the domain-error hierarchy below is a small
# leaf module the coverage signal does not depend on, so we leave it
# uninstrumented.
with atheris.instrument_imports():
    from bqemulator.sql.translator import SQLTranslator

from bqemulator.domain.errors import DomainError
from bqemulator.domain.result import Err, Ok

# A single translator instance is reused across iterations. The
# constructor walks the rules registry and is too expensive to redo per
# input; ``translate`` is documented as stateless so sharing the
# instance is safe.
_TRANSLATOR = SQLTranslator()


def TestOneInput(data: bytes) -> None:  # noqa: N802 — libFuzzer entry-point name
    """LibFuzzer entry point — exercise the translator once per input.

    Decodes ``data`` into a unicode string (the translator's documented
    input type) and asserts that ``translate`` returns a ``Result``.
    Catches :class:`DomainError` because subclasses are part of the
    documented surface — they may eventually escape ``translate`` once
    the rewriter pipeline grows; tolerating them keeps the harness from
    surfacing false positives. Any other exception type propagates and
    Atheris reports it as a crash.
    """
    fdp = atheris.FuzzedDataProvider(data)
    # Cap the per-iteration string length so a single oversize input
    # cannot starve the fuzzer. 8 KiB is well above the longest fixture
    # query in the corpus (~3 KiB for the TPC-DS Q23 variant) so any
    # real translator branch is still reachable.
    sql = fdp.ConsumeUnicode(8192)
    if not sql:
        return
    try:
        result = _TRANSLATOR.translate(sql)
    except DomainError:
        # Documented contract — see module docstring.
        return
    if not isinstance(result, (Ok, Err)):
        # AssertionError (not TypeError) is the canonical libFuzzer
        # crash signal — Atheris's reporter treats it as a bug class.
        msg = f"SQLTranslator.translate returned non-Result {type(result).__name__}"
        raise AssertionError(msg)  # noqa: TRY004 — see comment above


def _main() -> None:
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()


if __name__ == "__main__":
    _main()
