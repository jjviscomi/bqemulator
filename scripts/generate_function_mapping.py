#!/usr/bin/env python3
"""Generate the BigQuery → DuckDB rule mapping inside ``sql-function-mapping.md``.

P4.b (2026-05-21) — closes the doc-drift class for the hand-curated
92-rule listing that lives inside
``docs/reference/sql-function-mapping.md``. Walks every rule
registered with :func:`bqemulator.sql.rules.get_all_rules` plus every
public ``rewrite_*`` / ``expand_*`` function exported from
:mod:`bqemulator.sql.rewriter`, extracts a one-line summary from each
class / function docstring, and renders a Markdown block wedged
between two sentinel comments at the bottom of the committed
document. The narrative ABOVE the sentinel block (intro prose with
"92 rules" + the INFORMATION_SCHEMA rewriter mapping at the tail of
the file) carries operator-judgement context that can't be derived
mechanically; it is left untouched.

Usage::

    make function-mapping       # regenerate + write to disk
    python scripts/generate_function_mapping.py --check  # CI gate

The ``--check`` mode regenerates in memory, compares to the
committed copy under the sentinel block, and exits non-zero if they
differ. ``make verify`` (and the per-PR ``docs-drift-check`` job in
ci.yml) calls this to prevent the snapshot drifting from the live
rule registry.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
import importlib
from pathlib import Path
import pkgutil
import re
import sys
from types import ModuleType
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from bqemulator.sql import rewriter as _rewriter_pkg  # noqa: E402
from bqemulator.sql.rules import get_all_rules  # noqa: E402

#: Where the snapshot lands. The file's hand-maintained narrative
#: lives above the sentinels; the script only rewrites between them.
OUTPUT_PATH = _REPO_ROOT / "docs" / "reference" / "sql-function-mapping.md"

#: Sentinel comments wrap the auto-generated block. Anything outside
#: this pair is hand-maintained. The exact strings must match the
#: committed copy of ``sql-function-mapping.md`` — see the regex in
#: :func:`_inject_into_file` for the substitution contract.
SENTINEL_BEGIN = "<!-- BEGIN AUTO-GENERATED RULE REGISTRY -->"
SENTINEL_END = "<!-- END AUTO-GENERATED RULE REGISTRY -->"

#: Comparison-mode result codes (mirrors sys.exit semantics).
EXIT_CLEAN = 0
EXIT_DRIFT = 1

#: GitHub blob base for source-file links — kept in sync with the
#: generator at :mod:`scripts.generate_coverage_matrix`.
_GITHUB_BLOB = "https://github.com/jjviscomi/bqemulator/blob/main"

#: Module-name → category-display-name mapping. The display order is
#: the order rules + rewriter functions appear in the rendered table.
#: New rule modules added later land under "Other" until this dict is
#: extended.
_RULE_CATEGORY_DISPLAY: dict[str, str] = {
    "aggregate_types": "Aggregate / window",
    "array_helpers": "Array helpers",
    "datetime_semantics": "Date / time / timestamp",
    "interval_rules": "Interval helpers",
    "iso_date_parts": "ISO date parts",
    "json_helpers": "JSON helpers",
    "misc_helpers": "Math / numeric / misc",
    "numeric_types": "Numeric type helpers",
    "range_rules": "RANGE<T> constructors",
    "reciprocal_trig": "Reciprocal trig",
    "safe_math": "SAFE.X arithmetic",
    "spatial": "GEOGRAPHY (spheroidal)",
    "string_helpers": "String / bytes helpers",
}

#: Rewriter modules whose ``rewrite_*`` / ``expand_*`` functions we
#: surface in the auto-table. The INFORMATION_SCHEMA rewriter is
#: deliberately excluded — that family has its own hand-maintained
#: per-view table in ``sql-function-mapping.md`` that the script
#: leaves alone.
_REWRITER_CATEGORY_DISPLAY: dict[str, str] = {
    "aggregate_variants": "Pre-translator (aggregate variants)",
    "collate_specifier": "Pre-translator (COLLATE)",
    "create_table_schema_ctas": "Pre-translator (CTAS schema)",
    "datetime_helpers": "Pre-translator (date / time)",
    "decimal_literals": "Pre-translator (decimal literals)",
    "default_dataset": "Pre-translator (default dataset)",
    "division_by_zero": "Pre-translator (division by zero)",
    "json_helpers": "Pre-translator (JSON helpers)",
    "legacy_sql": "Pre-translator (legacy SQL)",
    "numeric_literals": "Pre-translator (numeric literals)",
    "partition_pseudo_columns": "Pre-translator (partition pseudo-columns)",
    "range_sessionize": "Pre-translator (RANGE_SESSIONIZE)",
    "row_access_filter": "Pre-translator (row-access)",
    "safe_helpers": "Pre-translator (SAFE.X prefix)",
    "sha512": "Pre-translator (SHA-512)",
    "specialized_types": "Pre-translator (RANGE/INTERVAL literals)",
    "string_helpers": "Pre-translator (string helpers)",
    "struct_helpers": "Pre-translator (STRUCT helpers)",
    "timestamp_iso_helpers": "Pre-translator (TIMESTAMP ISO helpers)",
    "unnest_offset": "Pre-translator (UNNEST WITH OFFSET)",
    "unnest_struct": "Pre-translator (UNNEST STRUCT aliases)",
    "wildcard_expander": "Pre-translator (wildcard tables)",
}

#: Modules under :mod:`bqemulator.sql.rewriter` that the auto-section
#: deliberately skips. ``information_schema`` carries its own per-
#: view table in the hand-maintained section of the document.
_REWRITER_SKIP: frozenset[str] = frozenset({"information_schema"})


@dataclass(slots=True, frozen=True)
class _MappedRule:
    """One entry in the rendered table — auto-extracted summary + provenance."""

    bq_surface: str
    duckdb_equiv: str
    rule_name: str
    module: str
    category: str
    kind: str  # "rule" or "rewriter"

    @property
    def sort_key(self) -> tuple[str, str, str]:
        """Stable sort: by category, then by rule name."""
        return (self.category, self.kind, self.rule_name)


_ARROW_SPLITTERS: tuple[str, ...] = (" → ", " -> ", " => ")


_RST_DOUBLE_BACKTICK_RE = re.compile(r"``([^`]+)``")


def _normalise_docstring_fragment(text: str) -> str:
    """Convert RST ``…`` code spans to Markdown ``…`` code spans.

    Docstrings throughout the rule registry use the Sphinx / RST
    convention of double-backticks for inline code (so the docstrings
    render cleanly in mkdocstrings). Markdown tables want single-
    backtick code spans, so we walk every ``foo`` → ``foo`` (drops one
    backtick per side). Stray trailing periods are stripped so the
    cell text doesn't end with redundant punctuation.
    """
    text = _RST_DOUBLE_BACKTICK_RE.sub(r"`\1`", text)
    return text.strip().rstrip(".")


def _split_docstring(doc: str) -> tuple[str, str]:
    """Return (bq_surface, duckdb_equiv) parsed from the docstring's first line.

    The convention is ``X → Y`` (or ``X -> Y``). When the docstring
    has no arrow, the entire first line lands in the BQ-surface column
    and DuckDB-equiv is left empty — that's the contract the renderer
    falls back on for rules whose docstring describes a categorical
    rewrite rather than a function-for-function mapping.
    """
    first_line = (doc or "").strip().split("\n", 1)[0]
    for splitter in _ARROW_SPLITTERS:
        if splitter in first_line:
            lhs, _, rhs = first_line.partition(splitter)
            return _normalise_docstring_fragment(lhs), _normalise_docstring_fragment(rhs)
    return _normalise_docstring_fragment(first_line), ""


def _collect_rules() -> list[_MappedRule]:
    """Return one :class:`_MappedRule` per registered :class:`TranslationRule`."""
    out: list[_MappedRule] = []
    for r in get_all_rules():
        cls = type(r)
        module = cls.__module__.rsplit(".", 1)[-1]
        category = _RULE_CATEGORY_DISPLAY.get(module, "Other")
        bq, ddb = _split_docstring(cls.__doc__ or "")
        out.append(
            _MappedRule(
                bq_surface=bq,
                duckdb_equiv=ddb,
                rule_name=getattr(r, "name", "") or cls.__name__,
                module=module,
                category=category,
                kind="rule",
            )
        )
    return out


def _collect_rewriters() -> list[_MappedRule]:
    """Return one :class:`_MappedRule` per public rewriter function.

    Walks every module under :mod:`bqemulator.sql.rewriter`, collects
    every callable whose name starts with ``rewrite_`` / ``expand_`` /
    ``qualify_`` (the three prefixes the pipeline uses), and extracts
    a summary from the function's docstring. The ``information_schema``
    module is skipped via :data:`_REWRITER_SKIP` — that family carries
    its own hand-maintained table.
    """
    out: list[_MappedRule] = []
    for module_info in pkgutil.iter_modules(_rewriter_pkg.__path__):
        if module_info.name.startswith("_") or module_info.name in _REWRITER_SKIP:
            continue
        module = importlib.import_module(f"{_rewriter_pkg.__name__}.{module_info.name}")
        category = _REWRITER_CATEGORY_DISPLAY.get(module_info.name, "Other rewriter")
        for fn in _iter_public_rewriter_functions(module):
            bq, ddb = _split_docstring(fn.__doc__ or "")
            out.append(
                _MappedRule(
                    bq_surface=bq,
                    duckdb_equiv=ddb,
                    rule_name=fn.__name__,
                    module=module_info.name,
                    category=category,
                    kind="rewriter",
                )
            )
    return out


def _iter_public_rewriter_functions(module: ModuleType) -> Iterable[Callable[..., Any]]:
    """Yield callables whose name signals a public rewriter entry point."""
    for name in dir(module):
        if name.startswith("_"):
            continue
        if not name.startswith(("rewrite_", "expand_", "qualify_")):
            continue
        obj = getattr(module, name)
        if not callable(obj):
            continue
        # Only surface functions defined in this module (not re-exports).
        if getattr(obj, "__module__", None) != getattr(module, "__name__", None):
            continue
        yield obj


def _group_by_category(rules: Iterable[_MappedRule]) -> dict[str, list[_MappedRule]]:
    """Bucket ``rules`` by their display category in stable order."""
    grouped: dict[str, list[_MappedRule]] = defaultdict(list)
    for r in rules:
        grouped[r.category].append(r)
    for bucket in grouped.values():
        bucket.sort(key=lambda r: r.sort_key)
    return grouped


def render(rules: list[_MappedRule], rewriters: list[_MappedRule]) -> str:
    """Build the Markdown block that lands between the sentinels."""
    lines: list[str] = []
    lines.append(SENTINEL_BEGIN)
    lines.append("")
    lines.append("## Rule + rewriter registry")
    lines.append("")
    lines.append(
        "> **Auto-generated.** Edit translation rules under "
        f"[`src/bqemulator/sql/rules/`]({_GITHUB_BLOB}/src/bqemulator/sql/rules/) "
        "or rewriters under "
        f"[`src/bqemulator/sql/rewriter/`]({_GITHUB_BLOB}/src/bqemulator/sql/rewriter/), "
        "then run `make function-mapping` to regenerate this block. "
        "The CI gate (`--check`) refuses to merge a PR whose committed "
        "registry has drifted from the live source. Per-rule "
        "docstring summaries are extracted as the cell text — if a "
        "cell reads wrong, edit the rule's docstring."
    )
    lines.append("")
    lines.append(
        f"- **Registered rules**: {len(rules)} ({len({r.module for r in rules})} rule modules)"
    )
    lines.append(
        f"- **Rewriter functions**: {len(rewriters)} "
        f"({len({r.module for r in rewriters})} rewriter modules; "
        "the INFORMATION_SCHEMA rewriter has its own hand-maintained "
        "per-view table below)"
    )
    lines.append("")
    lines.append("### Translation rules (post-transpile AST passes)")
    lines.append("")
    lines.append(_render_table(rules))
    lines.append("")
    lines.append("### Pre-translator rewriters (run before SQLGlot transpile)")
    lines.append("")
    lines.append(_render_table(rewriters))
    lines.append("")
    lines.append(SENTINEL_END)
    return "\n".join(lines)


def _render_table(entries: list[_MappedRule]) -> str:
    """Render the ``entries`` as a per-category Markdown table.

    Cells whose value is empty are filled with ``—`` so the column
    layout stays well-formed under MkDocs strict mode.
    """
    if not entries:
        return "_(no entries — the source surface is empty)_"
    lines: list[str] = []
    lines.append("| Category | BigQuery surface | DuckDB equivalent | Rule / function |")
    lines.append("|---|---|---|---|")
    grouped = _group_by_category(entries)
    # Stable category order: every category present in the display
    # dicts first (in dict-iteration order), then "Other" buckets.
    known_order: list[str] = []
    known_order.extend(_RULE_CATEGORY_DISPLAY.values())
    known_order.extend(_REWRITER_CATEGORY_DISPLAY.values())
    seen: set[str] = set()
    ordered_categories: list[str] = []
    for cat in known_order:
        if cat in grouped and cat not in seen:
            ordered_categories.append(cat)
            seen.add(cat)
    ordered_categories.extend(sorted(c for c in grouped if c not in seen))
    for cat in ordered_categories:
        for rule in grouped[cat]:
            bq = _escape_cell(rule.bq_surface) or "—"
            ddb = _escape_cell(rule.duckdb_equiv) or "—"
            name = f"`{rule.rule_name}`"
            lines.append(f"| {cat} | {bq} | {ddb} | {name} |")
    return "\n".join(lines)


_CELL_BAR_RE = re.compile(r"\|")


def _escape_cell(text: str) -> str:
    """Escape ``|`` so a docstring fragment can't break out of the table cell.

    Markdown tables use the pipe character as the column separator;
    a docstring that contains a literal ``|`` (e.g. a SQL OR expression
    or a bitwise operator) would corrupt the rendered table. The
    escape is GitHub-flavoured Markdown's ``\\|`` form.
    """
    return _CELL_BAR_RE.sub(r"\\|", text)


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

    rules = _collect_rules()
    rewriters = _collect_rewriters()
    generated = render(rules, rewriters)

    existing = args.output.read_text(encoding="utf-8") if args.output.is_file() else ""
    updated = _inject_into_file(existing, generated)

    if args.check:
        if updated != existing:
            print(
                "Function mapping is stale. Run `make function-mapping` "
                "and commit the regenerated document.",
                file=sys.stderr,
            )
            return EXIT_DRIFT
        print(
            f"Function mapping up to date ({len(rules)} rules + "
            f"{len(rewriters)} rewriter functions)."
        )
        return EXIT_CLEAN

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(updated, encoding="utf-8")
    try:
        display = str(args.output.relative_to(_REPO_ROOT))
    except ValueError:
        display = str(args.output)
    print(f"Wrote {display} ({len(rules)} rules + {len(rewriters)} rewriter functions).")
    return EXIT_CLEAN


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(main())
