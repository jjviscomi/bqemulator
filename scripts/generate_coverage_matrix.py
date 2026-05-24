#!/usr/bin/env python3
"""Generate the conformance coverage matrix.

Scans :data:`tests.conformance._surface_inventory.SURFACE` against the
current contents of ``tests/conformance/sql_corpus/`` and writes a
human-readable Markdown table to
``docs/reference/conformance-coverage-matrix.md``.

The matrix is **the** parity-tracking artefact for v1.0 — it surfaces
which BigQuery surface items the corpus exercises, at what depth, and
which items have zero coverage. Each session of fixture authoring
should target the largest "🔴 Uncovered" or "🟡 Sampled" cells.

Usage::

    make coverage-matrix          # regenerate + write to disk
    python scripts/generate_coverage_matrix.py --check  # CI: fail if stale

The ``--check`` mode regenerates the matrix in memory, compares to the
committed copy, and exits non-zero if they differ. ``make verify``
(and CI) calls this to prevent the matrix drifting from the corpus.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from collections.abc import Iterable
import json
from pathlib import Path
import sys

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tests.conformance._corpus import (  # noqa: E402
    CORPUS_DIR,
    Fixture,
    VariationTag,
    classify_variation,
    discover_fixtures,
)
from tests.conformance._surface_inventory import (  # noqa: E402
    SURFACE,
    SurfaceCategory,
    SurfaceItem,
    all_items,
)

#: Where the generated matrix lands.
OUTPUT_PATH = _REPO_ROOT / "docs" / "reference" / "conformance-coverage-matrix.md"

#: Depth-tier thresholds. The 🟢🟢 "deep" tier is intentionally a high
#: bar so the matrix can communicate progress even after the corpus
#: roughly doubles from its v1.0 size.
TIER_THRESHOLDS = (
    (0, "🔴 Uncovered", "uncovered"),
    (1, "🟡 Sampled", "sampled"),
    (3, "🟢 Covered", "covered"),
    (6, "🟢🟢 Deep", "deep"),
)

#: Comparison-mode result codes (mirrors sys.exit semantics).
EXIT_CLEAN = 0
EXIT_DRIFT = 1

#: How many "🔴 Uncovered" rows the "Gaps" section enumerates. Picked
#: to fit one screen of a typical PR-review viewport.
GAP_PREVIEW_ROWS = 30

#: How many fixture-id links each per-item cell shows before truncating.
FIXTURE_LINK_LIMIT = 5

#: Minimum fixture count for a surface to qualify for the "Variation
#: depth" report. Below this threshold, the surface is already flagged
#: as 🟡 Sampled / 🔴 Uncovered by the depth tier and additional variation
#: signal is noise. Set at 3 so the report identifies the "broad but
#: shallow" cohort — surfaces with enough fixtures to *look* covered
#: but whose tags collapse to ``{HAPPY_PATH}``.
VARIATION_DEPTH_MIN_FIXTURES = 3

#: Order in which variation tags appear in the per-row histogram. Kept
#: stable across runs so the matrix diff between sessions is stable
#: when only fixture counts change. ``HAPPY_PATH`` leads because it is
#: the default tag and reading the histogram from left to right
#: surfaces "is this fixture set mostly happy-path?" at a glance.
#: ``TIMEZONE`` lands at the tail so the left-to-right reading order
#: is stable as new tags are appended.
_VARIATION_DISPLAY_ORDER: tuple[VariationTag, ...] = (
    VariationTag.HAPPY_PATH,
    VariationTag.NULL_INPUT,
    VariationTag.EMPTY_INPUT,
    VariationTag.BOUNDARY_VALUE,
    VariationTag.UNICODE,
    VariationTag.ERROR_PATH,
    VariationTag.TIMEZONE,
)

#: Short labels used in the histogram cell. The full tag name (e.g.
#: ``boundary_value``) would make the cell too wide for the per-category
#: table, so we render the histogram as ``happy×3 / null×1`` etc.
_VARIATION_SHORT_LABEL: dict[VariationTag, str] = {
    VariationTag.HAPPY_PATH: "happy",
    VariationTag.NULL_INPUT: "null",
    VariationTag.EMPTY_INPUT: "empty",
    VariationTag.BOUNDARY_VALUE: "bound",
    VariationTag.UNICODE: "unicode",
    VariationTag.ERROR_PATH: "error",
    VariationTag.TIMEZONE: "tz",
}


def _tier(count: int) -> tuple[str, str]:
    """Return (label, slug) for the depth tier of ``count`` fixtures."""
    label, slug = TIER_THRESHOLDS[0][1], TIER_THRESHOLDS[0][2]
    for threshold, threshold_label, threshold_slug in TIER_THRESHOLDS:
        if count >= threshold:
            label, slug = threshold_label, threshold_slug
    return label, slug


def _fixture_text(fixture: Fixture) -> str:
    """Return the concatenated SQL + REST + parameters + expected text the detector scans.

    The detector regexes are matched against SQL plus the serialized
    ``setup_rest.json`` (so REST-only surfaces like
    ``rowAccessPolicies`` are detectable) plus the ``parameters.json``
    payload (so parameter-bound wire-format surfaces are detectable)
    plus the ``expected.json`` (so error-shape surfaces match against
    the recorded ``error.reason`` value). Joining with newlines gives
    the regexes sensible boundaries.
    """
    parts: list[str] = []
    if fixture.setup_sql:
        parts.append(fixture.setup_sql)
    parts.append(fixture.query_sql)
    parts.extend(json.dumps(op) for op in fixture.setup_rest)
    if fixture.parameters is not None:
        parts.append(json.dumps(fixture.parameters))
    if fixture.expected_path.is_file():
        parts.append(fixture.expected_path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def _coverage(
    items: Iterable[SurfaceItem],
    fixtures: Iterable[Fixture],
) -> dict[str, list[str]]:
    """Map ``item.id`` → sorted list of fixture ids exercising the item."""
    fixtures = list(fixtures)
    cache: dict[str, str] = {f.id: _fixture_text(f) for f in fixtures}
    hits: dict[str, list[str]] = defaultdict(list)
    for item in items:
        pattern = item.detect
        for fixture in fixtures:
            if pattern.search(cache[fixture.id]):
                hits[item.id].append(fixture.id)
        hits[item.id].sort()
    return hits


def _variation_tags(fixtures: Iterable[Fixture]) -> dict[str, frozenset[VariationTag]]:
    """Map ``fixture.id`` → its variation-tag set, classified once at startup.

    The classification is pure (see
    :func:`tests.conformance._corpus.classify_variation`) and produces
    a frozen set per fixture — multiple tags can fire simultaneously,
    or ``HAPPY_PATH`` alone fires when no specific tag matches.
    """
    return {f.id: classify_variation(f) for f in fixtures}


def _variation_histogram(
    fixture_ids: Iterable[str],
    variation_tags: dict[str, frozenset[VariationTag]],
) -> dict[VariationTag, int]:
    """Tally tag occurrences across ``fixture_ids``.

    A fixture with the tag set ``{NULL_INPUT, UNICODE}`` contributes
    +1 to each of the ``NULL_INPUT`` and ``UNICODE`` buckets — so the
    histogram total can exceed the fixture count when multi-tagged
    fixtures are present. Empty input returns an empty dict.
    """
    histogram: dict[VariationTag, int] = dict.fromkeys(_VARIATION_DISPLAY_ORDER, 0)
    has_any = False
    for fid in fixture_ids:
        tags = variation_tags.get(fid)
        if tags is None:
            continue
        has_any = True
        for tag in tags:
            histogram[tag] = histogram.get(tag, 0) + 1
    if not has_any:
        return {}
    return histogram


def _format_variation_histogram(histogram: dict[VariationTag, int]) -> str:
    """Render a tag histogram as ``happy×3 / null×1 / empty×1`` etc.

    Tags with a zero count are omitted so the cell stays compact. Tag
    order follows :data:`_VARIATION_DISPLAY_ORDER`. An empty histogram
    (no fixtures hit the surface) renders as ``—`` to match the
    fixture-link cell's empty marker.
    """
    if not histogram:
        return "—"
    parts: list[str] = []
    for tag in _VARIATION_DISPLAY_ORDER:
        count = histogram.get(tag, 0)
        if count:
            parts.append(f"{_VARIATION_SHORT_LABEL[tag]}×{count}")
    if not parts:
        return "—"
    return " / ".join(parts)


def _summary(hits: dict[str, list[str]]) -> dict[str, int]:
    """Count items per depth tier, excluding non-deterministic items.

    Non-deterministic items (ADR 0022 §1.2 / §7) are permanently
    excluded from the corpus by design — counting them as
    "🔴 Uncovered" would make the gap target unreachable. They are
    surfaced separately in the "Excluded (non-deterministic)"
    subsection so the reader still sees them, but they don't pollute
    the tier counts that drive the next session's authoring focus.
    """
    totals = {slug: 0 for _, _, slug in TIER_THRESHOLDS}
    deterministic_items = [item for item in all_items() if not item.nondeterministic]
    for item in deterministic_items:
        _, slug = _tier(len(hits.get(item.id, [])))
        totals[slug] += 1
    totals["total_items"] = len(deterministic_items)
    totals["nondeterministic"] = sum(1 for item in all_items() if item.nondeterministic)
    totals["all_items"] = len(all_items())
    return totals


#: Project's GitHub repo base for absolute-link references. Uses the same
#: convention as the source-file links in ADR 0023.
_GITHUB_BLOB = "https://github.com/jjviscomi/bqemulator/blob/main"


def _format_fixture_links(
    fixture_ids: list[str],
    *,
    limit: int = FIXTURE_LINK_LIMIT,
) -> str:
    """Render ``fixture_ids`` as a comma-separated list of clickable links."""
    if not fixture_ids:
        return "—"
    shown = fixture_ids[:limit]
    rendered = ", ".join(
        f"[`{fid}`]({_GITHUB_BLOB}/tests/conformance/sql_corpus/{fid})" for fid in shown
    )
    if len(fixture_ids) > limit:
        rendered += f", … (+{len(fixture_ids) - limit} more)"
    return rendered


def render(
    hits: dict[str, list[str]],
    fixtures: list[Fixture],
    variation_tags: dict[str, frozenset[VariationTag]] | None = None,
) -> str:
    """Build the full Markdown document.

    ``variation_tags`` carries the per-fixture taxonomy classification.
    When provided, the matrix gains a "Variation" column on every
    per-category row and a top-level "Variation depth" report
    enumerating broad-but-shallow surfaces. When omitted (legacy / test
    helper callers), the matrix renders without the variation axis.
    """
    if variation_tags is None:
        variation_tags = _variation_tags(fixtures)
    totals = _summary(hits)
    sections: list[str] = []
    sections.append(_render_header(totals, len(fixtures)))
    sections.append(_render_what_this_measures())
    sections.append(_render_summary(totals))
    excluded_section = _render_excluded()
    if excluded_section:
        sections.append(excluded_section)
    sections.append(_render_variation_depth(hits, variation_tags))
    sections.append(_render_gaps(hits))
    sections.append(_render_per_category(hits, variation_tags))
    sections.append(_render_see_also())
    return "\n".join(sections) + "\n"


def _render_header(totals: dict[str, int], fixture_count: int) -> str:
    """Document title + auto-gen banner + corpus/inventory stats."""
    inventory_line = (
        f"- **Inventory**: {totals['all_items']} surface items across {len(SURFACE)} categories"
    )
    if totals["nondeterministic"]:
        inventory_line += (
            f" ({totals['nondeterministic']} flagged "
            "non-deterministic — excluded from the corpus by ADR 0022, "
            "tracked under "
            "[Excluded (non-deterministic)](#excluded-non-deterministic-see-adr-0022))"
        )
    return "\n".join(
        (
            "# Conformance coverage matrix",
            "",
            (
                "> **Auto-generated.** Edit "
                f"[`tests/conformance/_surface_inventory.py`]"
                f"({_GITHUB_BLOB}/tests/conformance/_surface_inventory.py) "
                "to add surface items, then run ``make coverage-matrix`` "
                "to regenerate this document. The CI gate (``--check``) "
                "refuses to merge a PR whose committed matrix has "
                "drifted from the inventory or the corpus."
            ),
            "",
            inventory_line,
            (
                f"- **Corpus**: {fixture_count} fixtures under "
                f"[`tests/conformance/sql_corpus/`]"
                f"({_GITHUB_BLOB}/tests/conformance/sql_corpus)"
            ),
        )
    )


def _render_what_this_measures() -> str:
    """The "What this measures" intro section."""
    lines: list[str] = []
    lines.append("")
    lines.append("## What this measures")
    lines.append("")
    lines.append(
        "Each row in the per-category tables below is one BigQuery "
        "surface item (a function, a statement form, a wire-format "
        "shape, an error reason). The ``Count`` column is the number "
        "of fixtures in the conformance corpus that exercise the item, "
        "via a static regex match against the fixture's SQL + "
        "setup_rest + expected.json. The ``Tier`` column places the "
        "item in a depth bucket:"
    )
    lines.append("")
    for threshold, label, _ in TIER_THRESHOLDS:
        if threshold == 0:
            lines.append(f"- {label} -- 0 fixtures touch this surface.")
            continue
        next_threshold = next((t for t, _, _ in TIER_THRESHOLDS if t > threshold), None)
        if next_threshold is None:
            lines.append(f"- {label} -- >= {threshold} fixtures.")
        else:
            lines.append(f"- {label} -- {threshold} to {next_threshold - 1} fixtures.")
    lines.append("")
    lines.append(
        "Detection is *coarse*: a fixture matches an item if its text "
        "contains the detector pattern, irrespective of whether the "
        "item is the fixture's *primary* surface. A fixture authored "
        "to exercise ``REGEXP_EXTRACT`` will likely also count under "
        "``SELECT`` and ``STRING`` -- depth measures broad exposure, "
        "not test-purpose isolation."
    )
    return "\n".join(lines)


def _render_summary(totals: dict[str, int]) -> str:
    """The "Summary" tier-count table."""
    lines: list[str] = []
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    if totals["nondeterministic"]:
        lines.append(
            "Tier counts below exclude the "
            f"{totals['nondeterministic']} surface items flagged as "
            "non-deterministic (RAND, CURRENT_DATE / CURRENT_DATETIME / "
            "CURRENT_TIME / CURRENT_TIMESTAMP, SESSION_USER, "
            "GENERATE_UUID, TABLESAMPLE, FOR SYSTEM_TIME AS OF). Those "
            "items are permanently excluded from the corpus by "
            "[ADR 0022](../adr/0022-conformance-corpus-design.md) §1.2 "
            "/ §7 and are tracked separately under [Excluded "
            "(non-deterministic)](#excluded-non-deterministic-see-adr-0022)."
        )
        lines.append("")
    lines.append("| Tier | Count | Share |")
    lines.append("|---|---:|---:|")
    total = totals["total_items"]
    for _, label, slug in TIER_THRESHOLDS:
        share = 100.0 * totals[slug] / total if total else 0.0
        lines.append(f"| {label} | {totals[slug]} | {share:.1f}% |")
    lines.append(f"| **Total** | **{total}** | 100.0% |")
    return "\n".join(lines)


def _render_excluded() -> str:
    """List the items excluded from the corpus by ADR 0022."""
    excluded: list[tuple[SurfaceCategory, SurfaceItem]] = [
        (cat, item) for cat in SURFACE for item in cat.items if item.nondeterministic
    ]
    if not excluded:
        return ""
    lines: list[str] = []
    lines.append("")
    lines.append("## Excluded (non-deterministic — see ADR 0022)")
    lines.append("")
    lines.append(
        "These surface items are permanently outside the conformance "
        "corpus because their output cannot be reproduced fixture-to-"
        "fixture (wall-clock, RNG, session state, or random sampling). "
        "They are exercised in adjacent test tiers — see the per-item "
        "notes for the canonical location."
    )
    lines.append("")
    lines.append("| Category | Item | Why excluded |")
    lines.append("|---|---|---|")
    for cat, item in excluded:
        note = item.notes or "Non-deterministic — see ADR 0022 §1.2 / §7."
        lines.append(f"| {cat.name} | [`{item.name}`]({item.bq_docs}) | {note} |")
    return "\n".join(lines)


def _render_variation_depth(
    hits: dict[str, list[str]],
    variation_tags: dict[str, frozenset[VariationTag]],
) -> str:
    """The "Variation depth" report — broad-but-shallow surfaces.

    Lists every deterministic surface item whose fixture set has
    ``>= VARIATION_DEPTH_MIN_FIXTURES`` fixtures BUT whose union of
    variation tags is exactly ``{HAPPY_PATH}``. These are the surfaces
    that *look* well covered by fixture count, but every fixture sits in
    the happy path — the typical BQ-vs-DuckDB divergence (NULL
    propagation, empty inputs, ±Inf / NaN, timezone arithmetic, Unicode
    case-folding, error-shape parity) is reliably missed.

    Sorted by fixture count descending so the highest-value targets
    surface first. This is the picklist edge-case fixtures are
    authored against.
    """
    lines: list[str] = []
    lines.append("")
    lines.append("## Variation depth — broad-but-shallow surfaces")
    lines.append("")
    lines.append(
        "These surfaces have **at least "
        f"{VARIATION_DEPTH_MIN_FIXTURES} fixtures**, but every fixture "
        "sits in the **happy path** — the variation taxonomy collapses "
        "to ``{happy_path}``. They look covered by fixture count, but the "
        "typical BigQuery-vs-DuckDB divergence (NULL propagation, empty "
        "inputs, ±Inf / NaN, timezone arithmetic, Unicode case-folding, "
        "error-shape parity) lives in scenarios a happy-path-only sweep "
        "reliably misses. Add edge-case fixtures targeting these "
        "scenarios to close the gap. The taxonomy lives in "
        "[`tests/conformance/_corpus.py`]("
        + _GITHUB_BLOB
        + "/tests/conformance/_corpus.py) and is locked by "
        "[ADR 0022](../adr/0022-conformance-corpus-design.md) "
        '§"Variation taxonomy".'
    )
    lines.append("")
    lines.append("| Category | Item | Fixtures | Variation | BQ docs |")
    lines.append("|---|---|---:|---|---|")
    rows: list[tuple[int, str, str, str, str]] = []
    for cat in SURFACE:
        for item in cat.items:
            if item.nondeterministic:
                continue
            fixture_ids = hits.get(item.id, [])
            count = len(fixture_ids)
            if count < VARIATION_DEPTH_MIN_FIXTURES:
                continue
            histogram = _variation_histogram(fixture_ids, variation_tags)
            # Broad-but-shallow: every fixture's tag set is exactly
            # {HAPPY_PATH}. Equivalently: no non-happy tag has a
            # positive count.
            non_happy_total = sum(
                histogram.get(tag, 0)
                for tag in _VARIATION_DISPLAY_ORDER
                if tag is not VariationTag.HAPPY_PATH
            )
            if non_happy_total > 0:
                continue
            rows.append(
                (
                    count,
                    cat.name,
                    item.name,
                    _format_variation_histogram(histogram),
                    item.bq_docs,
                )
            )
    # Sort by fixture count desc, then by category + item name asc so
    # ties are stable across regenerations.
    rows.sort(key=lambda row: (-row[0], row[1], row[2]))
    if not rows:
        lines.append(
            "| _(no broad-but-shallow surfaces — every surface with >= "
            f"{VARIATION_DEPTH_MIN_FIXTURES} fixtures exercises at least "
            "one non-happy-path variation)_ |  |  |  |  |"
        )
    else:
        for count, cat_name, item_name, hist, docs in rows:
            lines.append(f"| {cat_name} | `{item_name}` | {count} | {hist} | [BQ ref]({docs}) |")
    return "\n".join(lines)


def _render_gaps(hits: dict[str, list[str]]) -> str:
    """The "Top N uncovered items" gap table."""
    lines: list[str] = []
    lines.append("")
    lines.append(f"## Gaps -- top {GAP_PREVIEW_ROWS} 0-fixture surface items")
    lines.append("")
    lines.append(
        "The fastest single-session improvements come from these "
        "uncovered cells. Each is a candidate fixture-authoring target."
    )
    lines.append("")
    lines.append("| Category | Item | BQ docs |")
    lines.append("|---|---|---|")
    gap_count = 0
    for cat in SURFACE:
        for item in cat.items:
            if hits.get(item.id):
                continue
            if item.nondeterministic:
                continue
            lines.append(f"| {cat.name} | `{item.name}` | [BQ ref]({item.bq_docs}) |")
            gap_count += 1
            if gap_count >= GAP_PREVIEW_ROWS:
                break
        if gap_count >= GAP_PREVIEW_ROWS:
            break
    if gap_count == 0:
        lines.append(
            "| _(no uncovered items -- every surface item has at least 1 fixture)_ |  |  |"
        )
    return "\n".join(lines)


def _render_per_category(
    hits: dict[str, list[str]],
    variation_tags: dict[str, frozenset[VariationTag]],
) -> str:
    """Concatenate the per-category sections."""
    lines: list[str] = ["", "## Per-category coverage", ""]
    for cat in SURFACE:
        lines.extend(_render_category(cat, hits, variation_tags))
    return "\n".join(lines)


def _render_see_also() -> str:
    """The "See also" footer with related-doc links."""
    return "\n".join(
        (
            "## See also",
            "",
            (
                "- [ADR 0022 -- Conformance corpus design]"
                "(../adr/0022-conformance-corpus-design.md) -- what makes "
                "a fixture qualify."
            ),
            (
                "- [ADR 0023 -- Conformance divergence baseline]"
                "(../adr/0023-conformance-divergence-baseline.md) -- "
                "how divergent fixtures are pinned."
            ),
            (
                f"- [Conformance corpus README]"
                f"({_GITHUB_BLOB}/tests/conformance/sql_corpus/README.md) "
                "-- fixture authoring contract."
            ),
            "",
        )
    )


def _render_category(
    cat: SurfaceCategory,
    hits: dict[str, list[str]],
    variation_tags: dict[str, frozenset[VariationTag]],
) -> list[str]:
    """Render one category's section.

    The "covered / total" denominator excludes non-deterministic items —
    those are tracked separately in the "Excluded (non-deterministic)"
    section per ADR 0022 §1.2 / §7. Items in the per-category table are
    annotated with " (excluded — non-deterministic)" so the reader
    still sees them in context.

    The ``Variation`` column carries the per-fixture taxonomy
    histogram — see :data:`_VARIATION_DISPLAY_ORDER` for the column
    ordering and the classifier in
    :func:`tests.conformance._corpus.classify_variation` for the
    detection contract.
    """
    out: list[str] = []
    deterministic_items = [item for item in cat.items if not item.nondeterministic]
    covered = sum(1 for item in deterministic_items if len(hits.get(item.id, [])) > 0)
    out.append(f"### {cat.name}")
    out.append("")
    out.append(
        f"[BigQuery reference]({cat.bq_docs}) -- "
        f"**{covered} / {len(deterministic_items)} items covered**"
    )
    out.append("")
    if cat.description:
        out.append(f"> {cat.description}")
        out.append("")
    out.append("| Item | Count | Tier | Variation | Fixtures |")
    out.append("|---|---:|:---:|---|---|")
    for item in cat.items:
        fixtures = hits.get(item.id, [])
        count = len(fixtures)
        if item.nondeterministic:
            label = "⚪ Excluded"
            adr_link = "_see [ADR 0022](../adr/0022-conformance-corpus-design.md) §1.2 / §7_"
            row = (
                f"| [`{item.name}`]({item.bq_docs}) "
                "_(excluded — non-deterministic)_ "
                f"| n/a | {label} | n/a | {adr_link} |"
            )
            out.append(row)
            if item.notes:
                out.append(f"|  |  |  |  | _{item.notes}_ |")
            continue
        label, _ = _tier(count)
        fixture_cell = _format_fixture_links(fixtures)
        histogram = _variation_histogram(fixtures, variation_tags)
        variation_cell = _format_variation_histogram(histogram)
        # Italicize the gap rows for at-a-glance scanning.
        if count == 0:
            row = f"| [`{item.name}`]({item.bq_docs}) | 0 | {label} | — | _gap_ |"
        else:
            row = (
                f"| [`{item.name}`]({item.bq_docs}) | {count} | {label} | "
                f"{variation_cell} | {fixture_cell} |"
            )
        out.append(row)
        if item.notes:
            out.append(f"|  |  |  |  | _{item.notes}_ |")
    out.append("")
    return out


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

    fixtures = sorted(discover_fixtures(corpus_dir=CORPUS_DIR), key=lambda f: f.id)
    hits = _coverage(all_items(), fixtures)
    variation_tags = _variation_tags(fixtures)
    rendered = render(hits, fixtures, variation_tags)

    if args.check:
        existing = args.output.read_text(encoding="utf-8") if args.output.is_file() else ""
        if rendered != existing:
            print(
                "Coverage matrix is stale. Run ``make coverage-matrix`` "
                "and commit the regenerated document.",
                file=sys.stderr,
            )
            return EXIT_DRIFT
        print(f"Coverage matrix up to date ({len(fixtures)} fixtures).")
        return EXIT_CLEAN

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(
        f"Wrote {args.output.relative_to(_REPO_ROOT)} "
        f"({len(fixtures)} fixtures vs {len(all_items())} inventory items)."
    )
    return EXIT_CLEAN


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(main())
