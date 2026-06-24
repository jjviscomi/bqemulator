#!/usr/bin/env python3
"""Generate the REST + gRPC inventory inside ``docs/reference/api-coverage.md``.

Mirrors the drift-gated generator pattern used by
:mod:`scripts.generate_function_mapping`,
:mod:`scripts.generate_coverage_matrix`, and
:mod:`scripts.generate_compatibility_matrix`.

Walks every ``@router.<verb>(...)`` decorator under
``src/bqemulator/api/routes/`` plus the root-level health router in
``src/bqemulator/api/health.py``, resolves each one against the
router's ``prefix=`` argument, and renders a grouped table under
sentinel comments. The gRPC inventory is parsed out of the
``_SERVICE`` + ``_<METHOD>`` constants at the top of every
``src/bqemulator/grpc_api/*_servicer.py`` file.

Usage::

    make api-coverage                       # regenerate + write to disk
    python scripts/generate_api_coverage.py --check   # CI drift gate

``--check`` regenerates in memory and exits non-zero on drift.
``make verify`` calls ``--check`` next to the existing matrix
drift gates so a route added without re-running ``make
api-coverage`` fails the PR rather than shipping stale.
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path
import re
import sys

_REPO_ROOT = Path(__file__).resolve().parent.parent

#: Where the snapshot lands. Hand-maintained narrative ABOVE the
#: sentinel block is preserved byte-for-byte; only the block itself
#: is rewritten by ``make api-coverage``.
OUTPUT_PATH = _REPO_ROOT / "docs" / "reference" / "api-coverage.md"

#: Sentinel comments wrap the auto-generated block. Must match the
#: strings in :data:`OUTPUT_PATH` exactly for the in-place rewriter
#: to find them; matched via ``re.escape`` + ``re.DOTALL``.
SENTINEL_BEGIN = "<!-- BEGIN AUTO-GENERATED API INVENTORY -->"
SENTINEL_END = "<!-- END AUTO-GENERATED API INVENTORY -->"

#: Distinct exit codes per failure mode so CI scripting can pin the
#: abort point (mirrors the convention used by ``release.py``).
EXIT_CLEAN = 0
EXIT_USAGE = 2
EXIT_DRIFT = 3

#: GitHub blob root used to render relative ``[file](.../blob/main/...)``
#: links. Mirrors the constant in ``generate_function_mapping.py``.
_GITHUB_BLOB = "https://github.com/jjviscomi/bqemulator/blob/main"

#: REST route source directory (relative to repo root).
_REST_ROUTES_DIR = _REPO_ROOT / "src" / "bqemulator" / "api" / "routes"

#: Health router lives outside ``routes/`` and uses no prefix —
#: rendered as a separate top-level group so the table doesn't
#: confuse `/healthz` with the prefixed BigQuery routes.
_HEALTH_ROUTER_PATH = _REPO_ROOT / "src" / "bqemulator" / "api" / "health.py"

#: gRPC servicer directory.
_GRPC_SERVICER_DIR = _REPO_ROOT / "src" / "bqemulator" / "grpc_api"


@dataclass(frozen=True)
class RestEndpoint:
    """One ``@router.<verb>(path)`` registration."""

    method: str  # GET / POST / PUT / PATCH / DELETE
    path: str  # full path including any ``prefix=`` from the router
    source_file: str  # relative path under src/ for the blob link


@dataclass(frozen=True)
class GrpcRpc:
    """One ``service/method`` pair dispatched by a generic-RPC handler."""

    service: str  # ``google.cloud.bigquery.storage.v1.BigQueryRead``
    method: str  # ``CreateReadSession``
    source_file: str  # relative path under src/ for the blob link


# ---------------------------------------------------------------------------
# REST parsing
# ---------------------------------------------------------------------------


def _extract_router_prefix(tree: ast.Module) -> str:
    """Return the ``prefix=`` argument passed to ``APIRouter(...)``.

    Empty string if no ``prefix=`` was supplied (root-level routes
    like ``/healthz``). The router is found by looking for a
    module-level assignment ``router = APIRouter(...)``.
    """
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not (
            len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "router"
        ):
            continue
        if not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        if not (isinstance(func, ast.Name) and func.id == "APIRouter"):
            continue
        for kw in node.value.keywords:
            if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                value = kw.value.value
                return value if isinstance(value, str) else ""
        return ""
    return ""


def _extract_rest_endpoints(path: Path) -> list[RestEndpoint]:
    """Return every ``@router.<verb>(\"path\")`` decorator in ``path``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    prefix = _extract_router_prefix(tree)
    rel = path.relative_to(_REPO_ROOT)
    endpoints: list[RestEndpoint] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            func = decorator.func
            # ``@router.get(...)`` shape: Call(func=Attribute(value=Name("router"), attr="get"))
            if not (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "router"
            ):
                continue
            verb = func.attr.upper()
            if verb not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                continue
            if not decorator.args:
                continue
            first = decorator.args[0]
            if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
                continue
            full_path = f"{prefix}{first.value}"
            endpoints.append(RestEndpoint(method=verb, path=full_path, source_file=str(rel)))
    return endpoints


def collect_rest_endpoints() -> list[RestEndpoint]:
    """Return every REST endpoint registered under ``api/`` (alphabetically)."""
    paths = sorted([*_REST_ROUTES_DIR.glob("*.py"), _HEALTH_ROUTER_PATH])
    endpoints: list[RestEndpoint] = []
    for path in paths:
        if path.name == "__init__.py":
            continue
        endpoints.extend(_extract_rest_endpoints(path))
    return endpoints


# ---------------------------------------------------------------------------
# gRPC parsing
# ---------------------------------------------------------------------------


_GRPC_SERVICE_RE = re.compile(r'^_SERVICE\s*=\s*"(/[^"]+)"', re.MULTILINE)
#: f-string method constants: ``_NAME = f"{_SERVICE}/Method"``.
_GRPC_METHOD_RE = re.compile(
    r'^_[A-Z_]+\s*=\s*f"\{_SERVICE\}/([A-Za-z][A-Za-z0-9]*)"',
    re.MULTILINE,
)


def _extract_grpc_rpcs(path: Path) -> list[GrpcRpc]:
    """Return every ``_SERVICE/<Method>`` declared in ``path``."""
    src = path.read_text(encoding="utf-8")
    service_match = _GRPC_SERVICE_RE.search(src)
    if service_match is None:
        return []
    service = service_match.group(1).lstrip("/")
    rel = path.relative_to(_REPO_ROOT)
    return [
        GrpcRpc(service=service, method=m.group(1), source_file=str(rel))
        for m in _GRPC_METHOD_RE.finditer(src)
    ]


def collect_grpc_rpcs() -> list[GrpcRpc]:
    """Return every gRPC RPC dispatched under ``grpc_api/``."""
    rpcs: list[GrpcRpc] = []
    for path in sorted(_GRPC_SERVICER_DIR.glob("*_servicer.py")):
        rpcs.extend(_extract_grpc_rpcs(path))
    return rpcs


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _group_rest(endpoints: list[RestEndpoint]) -> dict[str, dict[str, set[str]]]:
    """Bucket endpoints by ``(group, path) -> {methods}`` for the table.

    Group label is derived from the source file's basename
    (``health.py`` → "Health", ``datasets.py`` → "Datasets", etc.).
    """
    grouped: dict[str, dict[str, set[str]]] = {}
    for ep in endpoints:
        group = _group_label(ep.source_file)
        bucket = grouped.setdefault(group, {})
        bucket.setdefault(ep.path, set()).add(ep.method)
    return grouped


def _group_label(source_file: str) -> str:
    """Map a route file's basename to a display group label."""
    stem = Path(source_file).stem
    return {
        "health": "Health & metrics",
        "projects": "Projects",
        "datasets": "Datasets",
        "tables": "Tables",
        "tabledata": "TableData",
        "jobs": "Jobs",
        "routines": "Routines",
        "models": "Models",
        "row_access_policies": "RowAccessPolicies",
        "upload": "Upload host",
    }.get(stem, stem.title())


_VERB_ORDER = ["GET", "POST", "PUT", "PATCH", "DELETE"]


def render(rest: list[RestEndpoint], grpc: list[GrpcRpc]) -> str:
    """Build the Markdown block that lands between the sentinels."""
    lines: list[str] = [
        SENTINEL_BEGIN,
        "",
        "## REST endpoints",
        "",
        (
            "> **Auto-generated.** Edit route handlers under "
            f"[`src/bqemulator/api/routes/`]({_GITHUB_BLOB}/src/bqemulator/api/routes/) "
            "(or the root-level health router under "
            f"[`src/bqemulator/api/health.py`]({_GITHUB_BLOB}/src/bqemulator/api/health.py)) "
            "and run `make api-coverage` to regenerate this block. "
            "`make verify` calls `--check` to refuse merging a PR "
            "whose committed inventory has drifted from the live "
            "source. Endpoint counts in this block are facts about "
            "the codebase; ship-status (v1.0.0 release-quality "
            "across all surfaces) is asserted in the "
            "[compatibility matrix](compatibility-matrix.md) and "
            "gated by the conformance corpus on every PR."
        ),
        "",
        (
            f"- **Total REST endpoints**: {len(rest)} across "
            f"{len({Path(e.source_file).stem for e in rest})} route modules"
        ),
        "",
        _render_rest_table(rest),
        "",
        "## gRPC services",
        "",
        f"- **Total gRPC RPCs**: {len(grpc)} across {len({r.service for r in grpc})} services",
        "",
        _render_grpc_table(grpc),
        "",
        SENTINEL_END,
    ]
    return "\n".join(lines)


def _render_rest_table(endpoints: list[RestEndpoint]) -> str:
    """Render the REST inventory as a grouped Markdown table."""
    if not endpoints:
        return "_(no REST endpoints registered)_"
    grouped = _group_rest(endpoints)
    # Stable group order — preserve the user-facing ordering in
    # ``_group_label``'s dict, then any unmapped groups alphabetically.
    known_order = [
        "Health & metrics",
        "Projects",
        "Datasets",
        "Tables",
        "TableData",
        "Jobs",
        "Routines",
        "Models",
        "RowAccessPolicies",
        "Upload host",
    ]
    ordered: list[str] = [g for g in known_order if g in grouped]
    ordered.extend(sorted(g for g in grouped if g not in known_order))

    lines: list[str] = [
        "| Group | Path | Methods |",
        "|---|---|---|",
    ]
    for group in ordered:
        # Sort within group: shorter paths first (collection before item),
        # then alphabetical for stability.
        for path in sorted(grouped[group], key=lambda p: (p.count("/"), p)):
            verbs = " ".join(sorted(grouped[group][path], key=_VERB_ORDER.index))
            lines.append(f"| {group} | `{path}` | {verbs} |")
    return "\n".join(lines)


def _render_grpc_table(rpcs: list[GrpcRpc]) -> str:
    """Render the gRPC inventory as a per-service Markdown table."""
    if not rpcs:
        return "_(no gRPC services registered)_"
    by_service: dict[str, list[GrpcRpc]] = {}
    for rpc in rpcs:
        by_service.setdefault(rpc.service, []).append(rpc)
    lines: list[str] = [
        "| Service | RPC method |",
        "|---|---|",
    ]
    for service in sorted(by_service):
        lines.extend(
            f"| `{service}` | `{rpc.method}` |"
            for rpc in sorted(by_service[service], key=lambda r: r.method)
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Drift-check + write
# ---------------------------------------------------------------------------


_SENTINEL_BLOCK_RE = re.compile(
    re.escape(SENTINEL_BEGIN) + r".*?" + re.escape(SENTINEL_END),
    re.DOTALL,
)


def _inject_into_file(existing: str, generated: str) -> str:
    """Replace the sentinel block in-place, preserving everything outside it."""
    if SENTINEL_BEGIN in existing and SENTINEL_END in existing:
        return _SENTINEL_BLOCK_RE.sub(generated, existing, count=1)
    suffix = "\n" if existing and not existing.endswith("\n") else ""
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

    rest = collect_rest_endpoints()
    grpc = collect_grpc_rpcs()
    generated = render(rest, grpc)

    existing = args.output.read_text(encoding="utf-8") if args.output.is_file() else ""
    updated = _inject_into_file(existing, generated)

    if args.check:
        if updated != existing:
            print(
                "API coverage is stale. Run `make api-coverage` "
                "and commit the regenerated document.",
                file=sys.stderr,
            )
            return EXIT_DRIFT
        print(f"API coverage up to date ({len(rest)} REST endpoints + {len(grpc)} gRPC RPCs).")
        return EXIT_CLEAN

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(updated, encoding="utf-8")
    try:
        display = str(args.output.relative_to(_REPO_ROOT))
    except ValueError:
        display = str(args.output)
    print(f"Wrote {display} ({len(rest)} REST endpoints + {len(grpc)} gRPC RPCs).")
    return EXIT_CLEAN


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(main())
