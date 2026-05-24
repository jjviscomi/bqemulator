#!/usr/bin/env python3
"""Generate the conformance-corpus snapshot inside ``compatibility-matrix.md``.

Walks every fixture returned by
:func:`tests.conformance._corpus.discover_fixtures` plus the SQL
fixtures under :data:`tests.conformance._corpus.CORPUS_DIR`, the
HTTP corpus under ``tests/conformance/http_corpus/``, the gRPC
corpus under ``tests/conformance/grpc_corpus/``, and the XFAIL
registry in :data:`tests.conformance.divergences.KNOWN_DIVERGENCES`.
Aggregates PASS / XFAIL counts per phase + per-divergence-category
and renders a Markdown block wedged between two sentinel comments
at the bottom of the committed document. The hand-maintained tables
ABOVE the sentinel block (REST API resources, gRPC services, SQL
features, Types, Load + extract formats, Supported clients) carry
operator-judgement tags that can't be derived mechanically; they
are left untouched.

Usage::

    make compat-matrix          # regenerate + write to disk
    python scripts/generate_compatibility_matrix.py --check  # CI gate

The ``--check`` mode regenerates in memory, compares to the
committed copy under the sentinel block, and exits non-zero if they
differ. ``make verify`` (and the per-PR ``docs-drift-check`` job in
ci.yml) calls this to prevent the snapshot drifting from the
fixture corpus.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
import re
import sys

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tests.conformance._corpus import (  # noqa: E402
    CORPUS_DIR,
    Fixture,
    discover_fixtures,
)
from tests.conformance.divergences import KNOWN_DIVERGENCES  # noqa: E402

#: Where the snapshot lands. The file's hand-maintained tables live
#: above the sentinels; the script only rewrites between them.
OUTPUT_PATH = _REPO_ROOT / "docs" / "reference" / "compatibility-matrix.md"

#: Corpus directories under ``tests/conformance/``. Order is stable
#: so the per-corpus tables stack in a predictable sequence.
HTTP_CORPUS_DIR = _REPO_ROOT / "tests" / "conformance" / "http_corpus"
GRPC_CORPUS_DIR = _REPO_ROOT / "tests" / "conformance" / "grpc_corpus"

#: Sentinel comments wrap the auto-generated block. Anything outside
#: this pair is hand-maintained. The exact strings must match the
#: committed copy of ``compatibility-matrix.md`` — see the regex in
#: :func:`_inject_into_file` for the substitution contract.
SENTINEL_BEGIN = "<!-- BEGIN AUTO-GENERATED CONFORMANCE SNAPSHOT -->"
SENTINEL_END = "<!-- END AUTO-GENERATED CONFORMANCE SNAPSHOT -->"

#: Comparison-mode result codes (mirrors sys.exit semantics).
EXIT_CLEAN = 0
EXIT_DRIFT = 1

#: GitHub blob base for fixture-id links — kept in sync with the
#: generator at :mod:`scripts.generate_coverage_matrix`.
_GITHUB_BLOB = "https://github.com/jjviscomi/bqemulator/blob/main"


@dataclass(slots=True, frozen=True)
class _PhaseStats:
    """PASS / XFAIL tallies for one phase within one corpus."""

    corpus: str  # "SQL" / "HTTP" / "gRPC"
    phase: str  # e.g. "rest_crud"
    total: int
    xfail_ids: tuple[str, ...]

    @property
    def pass_count(self) -> int:
        """Number of fixtures that pass cleanly under this phase."""
        return self.total - len(self.xfail_ids)

    @property
    def xfail_count(self) -> int:
        """Number of fixtures pinned under :data:`KNOWN_DIVERGENCES`."""
        return len(self.xfail_ids)

    @property
    def status_glyph(self) -> str:
        """``✅`` when every fixture passes; ``⚠`` when at least one XFAIL is pinned."""
        return "⚠" if self.xfail_count else "✅"


@dataclass(slots=True, frozen=True)
class _XFailEntry:
    """One row in the XFAIL summary — fixture id + truncated rationale."""

    fixture_id: str
    rationale_short: str


def _discover_http_fixtures() -> list[Fixture]:
    """Return synthetic Fixture stubs for every directory under the HTTP corpus.

    The HTTP corpus uses the same on-disk shape as the SQL corpus
    (one directory per fixture under ``phase<N>_<slug>/<name>/``) but
    doesn't go through :func:`discover_fixtures` because its bodies
    are HTTP requests rather than SQL. For the snapshot we only need
    the ``id`` (``<phase>/<name>``) and the phase grouping; minimal
    Fixture stubs satisfy that contract without parsing the HTTP
    request bodies.
    """
    return _discover_directory_corpus(HTTP_CORPUS_DIR)


def _discover_grpc_fixtures() -> list[Fixture]:
    """Return synthetic Fixture stubs for every directory under the gRPC corpus.

    Same shape as the HTTP corpus — see :func:`_discover_http_fixtures`.
    """
    return _discover_directory_corpus(GRPC_CORPUS_DIR)


def _discover_directory_corpus(root: Path) -> list[Fixture]:
    """Enumerate ``root/<phase>/<fixture_name>`` two-level fixtures."""
    if not root.is_dir():
        return []
    out: list[Fixture] = []
    for phase_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        phase = phase_dir.name
        out.extend(
            Fixture(
                phase=phase,
                name=fixture_dir.name,
                path=fixture_dir,
                query_sql="",
                setup_sql=None,
                expected_path=fixture_dir / "expected.json",
            )
            for fixture_dir in sorted(p for p in phase_dir.iterdir() if p.is_dir())
        )
    return out


def _phase_stats_for(corpus: str, fixtures: Iterable[Fixture]) -> list[_PhaseStats]:
    """Aggregate PASS / XFAIL counts per phase for one corpus.

    Returns the per-phase rows sorted by phase name so the rendered
    table is stable across regenerations.
    """
    by_phase: dict[str, list[Fixture]] = defaultdict(list)
    for f in fixtures:
        by_phase[f.phase].append(f)
    rows: list[_PhaseStats] = []
    for phase in sorted(by_phase):
        phase_fixtures = by_phase[phase]
        xfail_ids = tuple(sorted(f.id for f in phase_fixtures if f.id in KNOWN_DIVERGENCES))
        rows.append(
            _PhaseStats(
                corpus=corpus,
                phase=phase,
                total=len(phase_fixtures),
                xfail_ids=xfail_ids,
            )
        )
    return rows


def _shorten_rationale(rationale: str, *, limit: int = 110) -> str:
    """Trim a divergence rationale to a single-line summary.

    The :data:`KNOWN_DIVERGENCES` values are multi-sentence prose
    that wouldn't fit in a table cell. We keep the first sentence
    (cut at the first period followed by a space or end-of-string)
    and truncate to ``limit`` chars with an ellipsis on overflow.
    """
    collapsed = " ".join(rationale.split())  # collapse newlines + runs of spaces
    # First sentence: stop at the first ". " or end-of-string.
    sentence_end = collapsed.find(". ")
    if sentence_end > 0:
        collapsed = collapsed[: sentence_end + 1]
    if len(collapsed) > limit:
        collapsed = collapsed[: limit - 1].rstrip() + "…"
    return collapsed


def render(
    sql_fixtures: list[Fixture],
    http_fixtures: list[Fixture],
    grpc_fixtures: list[Fixture],
) -> str:
    """Build the Markdown block that lands between the sentinels.

    The block carries:

    1. A "Totals" line summarising the corpus + XFAIL count.
    2. A per-corpus table (SQL / HTTP / gRPC) with one row per phase.
    3. An "XFAIL pin registry" table listing every entry in
       :data:`KNOWN_DIVERGENCES` with a shortened rationale.
    """
    sql_stats = _phase_stats_for("SQL", sql_fixtures)
    http_stats = _phase_stats_for("HTTP", http_fixtures)
    grpc_stats = _phase_stats_for("gRPC", grpc_fixtures)
    all_stats = sql_stats + http_stats + grpc_stats
    total_fixtures = sum(row.total for row in all_stats)
    total_xfails = sum(row.xfail_count for row in all_stats)

    lines: list[str] = []
    lines.append(SENTINEL_BEGIN)
    lines.append("")
    lines.append("## Conformance corpus snapshot")
    lines.append("")
    lines.append(
        "> **Auto-generated.** Edit fixtures under "
        f"[`tests/conformance/`]({_GITHUB_BLOB}/tests/conformance) "
        "or update the XFAIL registry in "
        f"[`tests/conformance/divergences.py`]({_GITHUB_BLOB}/tests/conformance/divergences.py), "
        "then run `make compat-matrix` to regenerate this block. The "
        "CI gate (`--check`) refuses to merge a PR whose committed "
        "snapshot has drifted from the corpus."
    )
    lines.append("")
    lines.append(
        f"- **Corpus totals**: {total_fixtures} fixtures "
        f"({len(sql_fixtures)} SQL + {len(http_fixtures)} HTTP + "
        f"{len(grpc_fixtures)} gRPC); "
        f"**{total_fixtures - total_xfails} PASS / {total_xfails} XFAIL**"
    )
    lines.append(
        "- **XFAIL contract**: every pin in `KNOWN_DIVERGENCES` "
        "references an ADR or `out-of-scope.md` section — invented "
        "divergences are forbidden (see "
        f"[ADR 0023]({_GITHUB_BLOB}/docs/adr/0023-conformance-divergence-baseline.md))."
    )
    lines.append("")
    lines.append("### Per-phase fixture coverage")
    lines.append("")
    lines.append(
        "Each row aggregates fixtures by corpus (SQL / HTTP / gRPC) "
        "and the on-disk phase directory. The `Status` column is "
        "derived: `✅` when every fixture in the phase passes; `⚠` "
        "when at least one fixture is pinned in the XFAIL registry."
    )
    lines.append("")
    lines.append("| Corpus | Phase | Fixtures | PASS | XFAIL | Status |")
    lines.append("|---|---|---:|---:|---:|:---:|")
    lines.extend(
        f"| {stats.corpus} | `{stats.phase}` | {stats.total} | "
        f"{stats.pass_count} | {stats.xfail_count} | {stats.status_glyph} |"
        for stats in all_stats
    )
    lines.append(
        f"| **Total** | | **{total_fixtures}** | "
        f"**{total_fixtures - total_xfails}** | **{total_xfails}** | "
        f"{'⚠' if total_xfails else '✅'} |"
    )
    lines.append("")
    lines.append(_render_xfail_registry())
    lines.append("")
    lines.append(SENTINEL_END)
    return "\n".join(lines)


def _render_xfail_registry() -> str:
    """Render the table listing every entry in :data:`KNOWN_DIVERGENCES`.

    Each row carries the fixture id (clickable) and a one-line
    rationale extracted from the registry value. Sorted by fixture id
    so the table is stable across regenerations.
    """
    lines: list[str] = []
    lines.append("### XFAIL pin registry")
    lines.append("")
    if not KNOWN_DIVERGENCES:
        lines.append("_(empty — every fixture passes against the recorded BigQuery baseline)_")
        return "\n".join(lines)
    lines.append(
        f"All {len(KNOWN_DIVERGENCES)} entries in "
        f"[`tests/conformance/divergences.py`]"
        f"({_GITHUB_BLOB}/tests/conformance/divergences.py) — "
        "each rationale references an ADR or "
        f"[`out-of-scope.md`]({_GITHUB_BLOB}/docs/reference/out-of-scope.md) "
        "section so closure paths stay traceable."
    )
    lines.append("")
    lines.append("| Fixture id | Rationale (short) |")
    lines.append("|---|---|")
    for fixture_id in sorted(KNOWN_DIVERGENCES):
        rationale = _shorten_rationale(KNOWN_DIVERGENCES[fixture_id])
        # Pick the corpus root that contains the fixture so the link
        # resolves. SQL is the common case; HTTP / gRPC fall through.
        link = _resolve_fixture_link(fixture_id)
        lines.append(f"| [`{fixture_id}`]({link}) | {rationale} |")
    return "\n".join(lines)


def _resolve_fixture_link(fixture_id: str) -> str:
    """Return a clickable URL for ``fixture_id`` across SQL / HTTP / gRPC corpora.

    SQL fixtures live under ``tests/conformance/sql_corpus/<id>``;
    HTTP under ``tests/conformance/http_corpus/<id>``; gRPC under
    ``tests/conformance/grpc_corpus/<id>``. The function picks the
    first one that exists on disk and falls back to SQL — the SQL
    corpus is the largest and the common case.
    """
    for corpus_root in (
        "tests/conformance/sql_corpus",
        "tests/conformance/http_corpus",
        "tests/conformance/grpc_corpus",
    ):
        if (_REPO_ROOT / corpus_root / fixture_id).is_dir():
            return f"{_GITHUB_BLOB}/{corpus_root}/{fixture_id}"
    return f"{_GITHUB_BLOB}/tests/conformance/sql_corpus/{fixture_id}"


_SENTINEL_BLOCK_RE = re.compile(
    re.escape(SENTINEL_BEGIN) + r".*?" + re.escape(SENTINEL_END),
    re.DOTALL,
)


def _inject_into_file(existing: str, generated: str) -> str:
    """Replace the sentinel block in ``existing`` with ``generated``.

    If the document doesn't yet carry the sentinel block (first run
    against a hand-maintained doc), the block is appended at the end
    with a one-line break separator. Otherwise the block is replaced
    in place — preserving every byte outside the sentinels.
    """
    if SENTINEL_BEGIN in existing and SENTINEL_END in existing:
        return _SENTINEL_BLOCK_RE.sub(generated, existing, count=1)
    suffix = "\n" if not existing.endswith("\n") else ""
    return f"{existing}{suffix}\n{generated}\n"


def _load_fixtures() -> tuple[list[Fixture], list[Fixture], list[Fixture]]:
    """Return (SQL, HTTP, gRPC) fixture lists sorted by id."""
    sql = sorted(discover_fixtures(corpus_dir=CORPUS_DIR), key=lambda f: f.id)
    http = sorted(_discover_http_fixtures(), key=lambda f: f.id)
    grpc = sorted(_discover_grpc_fixtures(), key=lambda f: f.id)
    return sql, http, grpc


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns ``EXIT_CLEAN`` (0) on success."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="CI mode: regenerate in memory, exit non-zero on drift.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_PATH,
        help=f"Where to write (default: {OUTPUT_PATH.relative_to(_REPO_ROOT)}).",
    )
    args = parser.parse_args(argv)

    sql_fixtures, http_fixtures, grpc_fixtures = _load_fixtures()
    generated = render(sql_fixtures, http_fixtures, grpc_fixtures)

    existing = args.output.read_text(encoding="utf-8") if args.output.is_file() else ""
    updated = _inject_into_file(existing, generated)

    if args.check:
        if updated != existing:
            print(
                "Compatibility matrix is stale. Run `make compat-matrix` "
                "and commit the regenerated document.",
                file=sys.stderr,
            )
            return EXIT_DRIFT
        total = len(sql_fixtures) + len(http_fixtures) + len(grpc_fixtures)
        print(
            f"Compatibility matrix up to date ({total} fixtures, {len(KNOWN_DIVERGENCES)} XFAILs)."
        )
        return EXIT_CLEAN

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(updated, encoding="utf-8")
    total = len(sql_fixtures) + len(http_fixtures) + len(grpc_fixtures)
    try:
        display = str(args.output.relative_to(_REPO_ROOT))
    except ValueError:
        display = str(args.output)
    print(f"Wrote {display} ({total} fixtures vs {len(KNOWN_DIVERGENCES)} XFAILs).")
    return EXIT_CLEAN


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(main())
