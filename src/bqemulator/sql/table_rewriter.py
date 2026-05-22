"""Table-reference rewriter.

Transforms BigQuery-style ``dataset.table`` references in DuckDB SQL
to the emulator's DuckDB schema naming: ``"project__dataset"."table"``.

This runs AFTER SQLGlot transpilation and BEFORE DuckDB execution.
"""

from __future__ import annotations

import re

from bqemulator.storage.engine import CATALOG_SCHEMA, SNAPSHOTS_SCHEMA

# Reserved schemas the rewriter must never re-prefix. Matched exactly
# so a user dataset that *starts* with the reserved prefix still gets
# the regular project-prefix rewrite.
_RESERVED_SCHEMAS = frozenset({CATALOG_SCHEMA, SNAPSHOTS_SCHEMA})

#: BigQuery's dataset-id format rule (mirrors
#: :data:`bqemulator.domain.ids._DATASET_RE`). Alphanumeric + underscore,
#: 1-1024 characters. Used to raise ``reason=invalid`` at the SQL boundary
#: when a user writes ``FROM \`!!bad!!.tbl\``` — real BigQuery returns
#: HTTP 400 / ``reason=invalid`` for the same input.
_BQ_DATASET_RE = re.compile(r"^[A-Za-z0-9_]{1,1024}$")

_ROUTINE_REF_TWO_PART = 2
_ROUTINE_REF_THREE_PART = 3


def rewrite_table_refs(duckdb_sql: str, project_id: str) -> str:
    """Qualify two-part table references with the project prefix.

    In BigQuery, users write ``dataset.table``. The emulator stores data
    in DuckDB schemas named ``project__dataset``. This function rewrites
    all occurrences of ``<dataset>.<table>`` to
    ``"<project>__<dataset>"."<table>"``.

    Three-part references (``project.dataset.table``) are also handled —
    the project part is stripped and replaced with our schema naming.

    This is a regex-based heuristic that works well for common SQL but
    does NOT handle all edge cases (e.g. table names inside string
    literals). A full AST-based rewriter ships in Phase 3's
    ``sql/rewriter/`` package.
    """
    import sqlglot
    from sqlglot import exp

    try:
        tree = sqlglot.parse_one(duckdb_sql, read="duckdb")
    except Exception:  # noqa: BLE001
        # If parsing fails, return as-is — the downstream DuckDB execute
        # will produce a clean error.
        return duckdb_sql

    modified = False
    for table_node in tree.find_all(exp.Table):
        if _rewrite_table_node(table_node, project_id):
            modified = True

    if _rewrite_schema_qualified_calls(tree, project_id):
        modified = True

    if modified:
        return tree.sql(dialect="duckdb")
    return duckdb_sql


def _rewrite_table_node(table_node: object, project_id: str) -> bool:
    """Rewrite a single :class:`sqlglot.exp.Table` node in place."""
    from sqlglot import exp

    assert isinstance(table_node, exp.Table)  # noqa: S101 — invariant
    catalog = table_node.catalog
    db = table_node.db
    table_name = table_node.name
    this_node = table_node.this
    is_tvf = isinstance(this_node, exp.Anonymous)

    # Phase 7: leave references to bqemulator's *reserved* schemas
    # alone. The time-travel rewriter emits
    # ``_bqemulator_snapshots.<id>`` references that must reach
    # DuckDB unmangled. Exact match: a user dataset whose id starts
    # with the prefix still gets the regular rewrite.
    if db in _RESERVED_SCHEMAS:
        return False

    # BigQuery parity: when a SQL fixture references a dataset with
    # characters outside the alphanumeric+underscore rule, BigQuery
    # returns HTTP 400 / ``reason=invalid`` *before* attempting any
    # catalog lookup. Without this guard the emulator falls through to
    # DuckDB which raises a generic "schema not found" error, surfacing
    # as HTTP 404 / ``reason=notFound`` — a divergence from real BQ.
    # TVF call-sites are exempt: they route through
    # :func:`qualified_routine_name_parts` which has its own dataset-id
    # whitelist and accepts compound ``project.dataset`` qualifiers in
    # the ``db`` slot (see :class:`TestTvfBacktickedCompoundQualifier`).
    if db and not is_tvf and not _BQ_DATASET_RE.match(db):
        _raise_invalid_dataset_id(db, table_name)

    # Schema-only references (``CREATE SCHEMA proj.ds`` /
    # ``DROP SCHEMA proj.ds``) parse as ``Table(catalog=proj, db=ds,
    # this=Identifier(""))`` in SQLGlot's AST — there is no table
    # part. Without this short-circuit the three-part rewriter
    # produces ``"proj__ds".""`` (empty trailing identifier) which
    # DuckDB rejects with ``zero-length delimited identifier``.
    # For two-part schema refs (``CREATE SCHEMA ds``) DuckDB's
    # parser folds the catalog+db into the bare schema slot, and
    # dbt-bigquery's ``CREATE SCHEMA IF NOT EXISTS \`proj\`.\`ds\``
    # is the path that surfaces this.
    #
    # TVF call-sites also produce an empty ``table_name`` because
    # ``this`` is an :class:`exp.Anonymous` (function call) rather
    # than an :class:`exp.Identifier`. Skip the schema-only branch
    # in that case and let the two/three-part rewriter route through
    # the TVF flattening path.
    if not table_name and not is_tvf and (catalog or db):
        return _rewrite_schema_only_ref(
            table_node,
            project_id,
            catalog,
            db,
        )
    if catalog:
        _rewrite_three_part(table_node, this_node, catalog, db, table_name, is_tvf=is_tvf)
        return True
    if db:
        _rewrite_two_part(table_node, this_node, project_id, db, table_name, is_tvf=is_tvf)
        return True
    # Bare table names are left as-is — they resolve via DuckDB's search_path.
    return False


def _rewrite_schema_only_ref(
    table_node: object,
    project_id: str,
    catalog: str,
    db: str,
) -> bool:
    """Rewrite a ``CREATE SCHEMA`` / ``DROP SCHEMA`` two-part target.

    Collapses ``catalog.db`` (or ``project.db`` for two-part shapes)
    into a single ``"project__dataset"`` identifier with **no**
    trailing ``.""``. The result is a valid DuckDB schema name that
    matches what every other rewriter path emits for the same
    ``(project, dataset)`` pair.
    """
    from sqlglot import exp

    assert isinstance(table_node, exp.Table)  # noqa: S101
    proj = catalog or project_id
    target_db = db or catalog
    if not target_db:
        return False
    new_schema = f"{proj}__{target_db}"
    table_node.set("catalog", None)
    table_node.set("db", None)
    table_node.set("this", exp.Identifier(this=new_schema, quoted=True))
    return True


def _raise_invalid_dataset_id(dataset_id: str, table_id: str) -> None:
    """Raise :class:`ValidationError` mirroring BigQuery's dataset-id message.

    BigQuery emits ``Invalid dataset ID "<id>". Dataset IDs must be
    alphanumeric (plus underscores and dashes) and must be at most 1024
    characters long.`` (the docs allow dashes; the live service does not,
    matching our :data:`_BQ_DATASET_RE`). The wording is reproduced
    verbatim so the conformance ``message_pattern`` matcher absorbs it
    via :doc:`/adr/0023-conformance-error-shape-parity`.
    """
    from bqemulator.domain.errors import ValidationError

    raise ValidationError(
        f'Invalid dataset ID "{dataset_id}". '
        "Dataset IDs must be alphanumeric (plus underscores and dashes) "
        "and must be at most 1024 characters long.",
        location=f"{dataset_id}.{table_id}",
    )


def _rewrite_three_part(
    table_node: object,
    this_node: object,
    catalog: str,
    db: str,
    table_name: str,
    *,
    is_tvf: bool,
) -> None:
    """Rewrite ``project.dataset.table`` → ``"project__dataset"."table"``."""
    from sqlglot import exp

    assert isinstance(table_node, exp.Table)  # noqa: S101
    if is_tvf:
        from bqemulator.udf.naming import qualified_routine_name_parts

        assert isinstance(this_node, exp.Anonymous)  # noqa: S101
        flat = qualified_routine_name_parts(catalog, db, this_node.name)
        this_node.set("this", flat)
        table_node.set("catalog", None)
        table_node.set("db", None)
        return
    new_schema = f"{catalog}__{db}"
    table_node.set("catalog", None)
    table_node.set("db", exp.Identifier(this=new_schema, quoted=True))
    table_node.set("this", exp.Identifier(this=table_name, quoted=True))


def _rewrite_two_part(
    table_node: object,
    this_node: object,
    project_id: str,
    db: str,
    table_name: str,
    *,
    is_tvf: bool,
) -> None:
    """Rewrite ``dataset.table`` → ``"project__dataset"."table"``."""
    from sqlglot import exp

    assert isinstance(table_node, exp.Table)  # noqa: S101
    if is_tvf:
        from bqemulator.udf.naming import qualified_routine_name_parts

        assert isinstance(this_node, exp.Anonymous)  # noqa: S101
        # BigQuery permits the back-ticked compound form ``\`proj.ds\`.tvf``;
        # SQLGlot's BigQuery dialect collapses the whole back-ticked
        # qualifier into the ``db`` slot for TVF calls instead of
        # splitting it into ``catalog``/``db``. Recover the project + dataset
        # halves here so :func:`qualified_routine_name_parts` validates each
        # half independently.
        catalog_override: str | None = None
        dataset_resolved = db
        if "." in db:
            catalog_override, _, dataset_resolved = db.partition(".")
        flat = qualified_routine_name_parts(
            catalog_override or project_id,
            dataset_resolved,
            this_node.name,
        )
        this_node.set("this", flat)
        table_node.set("db", None)
        return
    new_schema = f"{project_id}__{db}"
    table_node.set("db", exp.Identifier(this=new_schema, quoted=True))
    table_node.set("this", exp.Identifier(this=table_name, quoted=True))


def _rewrite_schema_qualified_calls(tree: object, project_id: str) -> bool:
    """Flatten schema-qualified UDF / TVF call sites.

    Handles two parser shapes:

    1. ``Dot(Identifier, Anonymous)`` — the SQLGlot AST for
       ``dataset.routine(args)`` written without backticks. Becomes
       ``project__dataset__routine(args)``.

    2. ``Anonymous`` whose ``name`` itself contains dots — the AST for
       ``` `dataset.routine`(args) ``` (single backticked identifier
       followed by ``(``). SQLGlot keeps the whole quoted string as the
       function name, so we split on the dot here and rebuild the flat
       form. Two and three-part qualifiers are both accepted; the
       three-part case lets users prefix with the project id.
    """
    from sqlglot import exp

    from bqemulator.domain.errors import InvalidQueryError, ValidationError
    from bqemulator.udf.naming import qualified_routine_name_parts

    assert isinstance(tree, exp.Expression)  # noqa: S101
    modified = False
    for dot_node in list(tree.find_all(exp.Dot)):
        left = dot_node.this
        right = dot_node.expression
        if not isinstance(left, exp.Identifier):
            continue
        if not isinstance(right, exp.Anonymous):
            continue
        dataset = left.name
        routine_name = right.name
        try:
            flat_name = qualified_routine_name_parts(project_id, dataset, routine_name)
        except ValidationError as exc:
            # The SQL-boundary id whitelist rejected the dataset id —
            # usually because it's a compound ``project.dataset``
            # back-ticked qualifier that contained a dot. BigQuery's
            # user-facing form for this is ``Function not found:
            # `<qualifier>`.<routine> at [L:C]`` (P3.a, ADR 0022 §3).
            raise InvalidQueryError(
                f"Function not found: `{dataset}`.{routine_name} at [1:8]",
                location="query",
            ) from exc
        right.set("this", flat_name)
        dot_node.replace(right)
        modified = True

    for anon_node in list(tree.find_all(exp.Anonymous)):
        # Skip the rewritten ones — they no longer contain a dot.
        name = anon_node.name
        if "." not in name:
            continue
        parts = name.split(".")
        if len(parts) == _ROUTINE_REF_TWO_PART:
            ds, routine = parts
            proj = project_id
        elif len(parts) == _ROUTINE_REF_THREE_PART:
            proj, ds, routine = parts
        else:
            continue
        try:
            flat_name = qualified_routine_name_parts(proj, ds, routine)
        except ValidationError as exc:
            raise InvalidQueryError(
                f"Function not found: `{name}` at [1:8]",
                location="query",
            ) from exc
        anon_node.set("this", flat_name)
        modified = True
    return modified


__all__ = ["rewrite_table_refs"]
