"""Result-shape and ``ddlOperationPerformed`` helpers for DDL query jobs.

Real BigQuery's ``jobs.query`` response for a single DDL statement is
not the engine's raw status output — it follows a per-statement-type
contract pinned by the ``rest_crud/ddl_result_*`` conformance corpus:

* ``CREATE TABLE`` / ``CREATE TABLE AS SELECT`` / ``CREATE VIEW``
  return a **zero-row result set whose schema is the statement's
  analyzed output schema** — the declared column list when present
  (including the ``IF NOT EXISTS`` skip case, which reports the
  *statement's* columns, not the pre-existing table's), otherwise the
  created object's columns. ``NOT NULL`` columns surface as
  ``REQUIRED``; ``ARRAY`` columns as ``REPEATED``; ``STRUCT`` columns
  as ``RECORD`` with nested ``fields``.
* ``ALTER TABLE``, ``CREATE SCHEMA``, and ``DROP TABLE/VIEW/SCHEMA``
  return an empty schema and no rows.
* ``TRUNCATE TABLE`` behaves like DML: empty schema, no rows, and a
  ``numDmlAffectedRows`` count — with **no** ``ddlOperationPerformed``.

``ddlOperationPerformed`` reflects what actually happened, not the
statement's verb: ``CREATE`` for a fresh object (even under
``OR REPLACE`` / ``IF NOT EXISTS``), ``REPLACE`` when ``OR REPLACE``
replaced an existing object, ``SKIP`` when ``IF NOT EXISTS`` found an
existing object or ``DROP … IF EXISTS`` found nothing, ``DROP`` for an
actual drop, and ``ALTER`` for ``ALTER TABLE``.

The executor resolves the operation **before** running the statement
(existence must be observed pre-mutation) via
:func:`resolve_ddl_operation`, then shapes the stored result via
:func:`ddl_result_schema_fields` after the statement (and its catalog
sync) has completed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import sqlglot
from sqlglot import Expr, exp

from bqemulator.catalog.ddl_sync import (
    _resolve_dataset_parts,
    _split_target,
    _unwrap_table_target,
)

if TYPE_CHECKING:  # pragma: no cover
    from bqemulator.api.dependencies import AppContext
    from bqemulator.catalog.models import TableFieldSchema


#: SQLGlot DataType name → BigQuery wire-format type name. The keys
#: are the upper-case names SQLGlot emits when parsing BigQuery DDL
#: column declarations; values are the BQ REST schema's ``type`` field.
DDL_BQ_WIRE_TYPES: dict[str, str] = {
    "BIGINT": "INTEGER",
    "INT": "INTEGER",
    "INT64": "INTEGER",
    "TEXT": "STRING",
    "VARCHAR": "STRING",
    "STRING": "STRING",
    "DOUBLE": "FLOAT",
    "FLOAT": "FLOAT",
    "FLOAT64": "FLOAT",
    "BOOL": "BOOLEAN",
    "BOOLEAN": "BOOLEAN",
    "DATE": "DATE",
    "TIME": "TIME",
    "DATETIME": "DATETIME",
    "TIMESTAMP": "TIMESTAMP",
    "TIMESTAMPTZ": "TIMESTAMP",
    "BYTES": "BYTES",
    "BLOB": "BYTES",
    "VARBINARY": "BYTES",
    "DECIMAL": "NUMERIC",
    "NUMERIC": "NUMERIC",
    "BIGNUMERIC": "BIGNUMERIC",
    "JSON": "JSON",
    "GEOGRAPHY": "GEOGRAPHY",
    "GEOMETRY": "GEOGRAPHY",
    "INTERVAL": "INTERVAL",
}

#: Statement type → ``ddlOperationPerformed`` when the dynamic
#: resolution in :func:`resolve_ddl_operation` does not apply (the
#: versioning / row-access fast paths, dry-run previews, and any DDL
#: whose target cannot be resolved). ``TRUNCATE_TABLE`` is absent on
#: purpose: BigQuery reports it like DML (``numDmlAffectedRows``,
#: no ``ddlOperationPerformed``) — pinned by
#: ``rest_crud/ddl_result_truncate_table``.
DDL_OPERATION_BY_STATEMENT: dict[str, str] = {
    "CREATE_TABLE": "CREATE",
    "CREATE_TABLE_AS_SELECT": "CREATE",
    "CREATE_VIEW": "CREATE",
    "CREATE_FUNCTION": "CREATE",
    "CREATE_TABLE_FUNCTION": "CREATE",
    "CREATE_PROCEDURE": "CREATE",
    "CREATE_SCHEMA": "CREATE",
    "CREATE_SNAPSHOT_TABLE": "CREATE",
    "DROP_TABLE": "DROP",
    "DROP_VIEW": "DROP",
    "DROP_FUNCTION": "DROP",
    "DROP_TABLE_FUNCTION": "DROP",
    "DROP_PROCEDURE": "DROP",
    "DROP_SCHEMA": "DROP",
    "DROP_SNAPSHOT_TABLE": "DROP",
    "ALTER_TABLE": "ALTER",
    "CREATE_ROW_ACCESS_POLICY": "CREATE",
    "DROP_ROW_ACCESS_POLICY": "DROP",
}

#: Statement types whose target is a table-or-view relation.
_RELATION_CREATE_TYPES = frozenset(
    {"CREATE_TABLE", "CREATE_TABLE_AS_SELECT", "CREATE_VIEW"},
)
_RELATION_DROP_TYPES = frozenset({"DROP_TABLE", "DROP_VIEW"})


def ddl_operation_for(statement_type: str) -> str:
    """Return the static ``ddlOperationPerformed`` for a DDL statement type.

    Returns ``""`` for non-DDL statements (and for ``TRUNCATE_TABLE``,
    which BigQuery reports without the field) so the caller skips
    writing it.
    """
    return DDL_OPERATION_BY_STATEMENT.get(statement_type, "")


def resolve_ddl_operation(
    bq_sql: str,
    statement_type: str,
    project_id: str,
    ctx: AppContext,
) -> str:
    """Resolve ``ddlOperationPerformed`` for a single DDL statement.

    Must run **before** the statement executes: the value depends on
    whether the target existed pre-mutation (``REPLACE`` / ``SKIP``
    discrimination). Pinned by the
    ``rest_crud/ddl_result_create_or_replace_table[_fresh]``,
    ``ddl_result_create_table_if_not_exists[_fresh]``, and
    ``ddl_result_drop_table_if_exists_missing`` fixtures.

    Falls back to the static :func:`ddl_operation_for` mapping when the
    statement is not one of the dynamically-resolved forms or its
    target cannot be determined.
    """
    if statement_type in _RELATION_CREATE_TYPES or statement_type == "CREATE_SCHEMA":
        return _resolve_create_operation(bq_sql, statement_type, project_id, ctx)
    if statement_type in _RELATION_DROP_TYPES or statement_type == "DROP_SCHEMA":
        return _resolve_drop_operation(bq_sql, statement_type, project_id, ctx)
    return ddl_operation_for(statement_type)


def _resolve_create_operation(
    bq_sql: str,
    statement_type: str,
    project_id: str,
    ctx: AppContext,
) -> str:
    """Resolve CREATE / REPLACE / SKIP for a ``CREATE …`` statement."""
    tree = _parse_statement(bq_sql)
    if not isinstance(tree, exp.Create):
        return ddl_operation_for(statement_type)
    existed = _target_exists(tree, statement_type, project_id, ctx)
    if existed is None:
        return ddl_operation_for(statement_type)
    if not existed:
        return "CREATE"
    if tree.args.get("replace"):
        return "REPLACE"
    if tree.args.get("exists"):
        return "SKIP"
    return "CREATE"


def _resolve_drop_operation(
    bq_sql: str,
    statement_type: str,
    project_id: str,
    ctx: AppContext,
) -> str:
    """Resolve DROP / SKIP for a ``DROP …`` statement."""
    tree = _parse_statement(bq_sql)
    if not isinstance(tree, exp.Drop):
        return ddl_operation_for(statement_type)
    existed = _target_exists(tree, statement_type, project_id, ctx)
    if existed is None:
        return ddl_operation_for(statement_type)
    if existed:
        return "DROP"
    return "SKIP" if tree.args.get("exists") else "DROP"


def _parse_statement(bq_sql: str) -> Expr | None:
    """Parse ``bq_sql`` in the BigQuery dialect; ``None`` when unparseable."""
    try:
        return sqlglot.parse_one(bq_sql, read="bigquery")
    except Exception:  # noqa: BLE001 — best-effort resolution
        return None


def _target_exists(
    tree: exp.Expression,
    statement_type: str,
    project_id: str,
    ctx: AppContext,
) -> bool | None:
    """Catalog existence of the statement's target, or ``None`` if unresolvable."""
    target = _unwrap_table_target(tree.this)
    if target is None:
        return None
    if statement_type in {"CREATE_SCHEMA", "DROP_SCHEMA"}:
        resolved = _resolve_dataset_parts(target, project_id)
        if resolved is None:
            return None
        return ctx.catalog.get_dataset(*resolved) is not None
    p_id, d_id, t_id = _split_target(target, project_id)
    if not d_id or not t_id:
        return None
    return ctx.catalog.get_table(p_id, d_id, t_id) is not None


def ddl_result_schema_fields(
    bq_sql: str,
    project_id: str,
    ctx: AppContext,
) -> list[dict[str, Any]]:
    """Return the BQ REST ``schema.fields`` for a CREATE TABLE/VIEW job result.

    The statement's declared column list wins when it maps completely
    (this is what BigQuery reports even for the ``IF NOT EXISTS`` skip
    case, where the pre-existing table's columns differ — pinned by
    ``rest_crud/ddl_result_create_table_if_not_exists``). Otherwise the
    created object's catalog entry — introspected from DuckDB by the
    post-execution DDL sync — provides the fields (the CTAS and
    CREATE VIEW forms, whose schema comes from the SELECT body).
    Returns ``[]`` when neither source resolves.
    """
    tree = _parse_statement(bq_sql)
    if not isinstance(tree, exp.Create):
        return []
    declared = _declared_schema_fields(tree)
    if declared is not None:
        return declared
    target = _unwrap_table_target(tree.this)
    if target is None:
        return []
    p_id, d_id, t_id = _split_target(target, project_id)
    if not d_id or not t_id:
        return []
    return _catalog_schema_fields(p_id, d_id, t_id, ctx) or []


def _declared_schema_fields(tree: exp.Create) -> list[dict[str, Any]] | None:
    """Map the statement's declared column list to REST schema fields.

    Returns ``None`` when the statement has no column list (CTAS /
    CREATE VIEW) or any column's type cannot be mapped — the caller
    falls back to catalog introspection in that case rather than
    emitting a partial schema.
    """
    this = tree.this
    if not isinstance(this, exp.Schema):
        return None
    column_defs = [c for c in this.expressions or [] if isinstance(c, exp.ColumnDef)]
    if not column_defs:
        return None
    fields: list[dict[str, Any]] = []
    for column in column_defs:
        entry = _field_from_column_def(column)
        if entry is None:
            return None
        fields.append(entry)
    return fields


def _field_from_column_def(column: exp.ColumnDef) -> dict[str, Any] | None:
    """Render one declared column into a REST schema field, or ``None``."""
    name = column.name
    kind = column.args.get("kind")
    if not name or not isinstance(kind, exp.DataType):
        return None
    entry = _field_from_data_type(name, kind)
    if entry is None:
        return None
    if _has_not_null_constraint(column) and entry["mode"] == "NULLABLE":
        entry["mode"] = "REQUIRED"
    return entry


def _field_from_data_type(name: str, data_type: exp.DataType) -> dict[str, Any] | None:
    """Render a declared ``exp.DataType`` into a REST schema field.

    ``ARRAY<T>`` collapses to the element type with ``mode=REPEATED``;
    ``STRUCT<…>`` becomes ``type=RECORD`` with nested ``fields`` —
    matching the wire shape pinned by
    ``rest_crud/ddl_result_create_table_complex_types``. Returns
    ``None`` for any type outside the mapped BigQuery DDL surface.
    """
    type_name = data_type.this.name.upper() if data_type.this is not None else ""
    if type_name == "ARRAY":
        return _repeated_field(name, data_type)
    if type_name == "STRUCT":
        return _record_field(name, data_type)
    wire_type = DDL_BQ_WIRE_TYPES.get(type_name)
    if wire_type is None:
        return None
    return {"name": name, "type": wire_type, "mode": "NULLABLE"}


def _repeated_field(name: str, data_type: exp.DataType) -> dict[str, Any] | None:
    """Render an ``ARRAY<T>`` declared type as its REPEATED element field."""
    inner = data_type.expressions[0] if data_type.expressions else None
    if not isinstance(inner, exp.DataType):
        return None
    element = _field_from_data_type(name, inner)
    if element is None or element["mode"] == "REPEATED":
        # BigQuery forbids ARRAY<ARRAY<…>>; refuse rather than guess.
        return None
    element["mode"] = "REPEATED"
    return element


def _record_field(name: str, data_type: exp.DataType) -> dict[str, Any] | None:
    """Render a ``STRUCT<…>`` declared type as a RECORD field with nested fields."""
    nested: list[dict[str, Any]] = []
    for sub in data_type.expressions or []:
        if not isinstance(sub, exp.ColumnDef):
            return None
        sub_entry = _field_from_column_def(sub)
        if sub_entry is None:
            return None
        nested.append(sub_entry)
    return {"name": name, "type": "RECORD", "mode": "NULLABLE", "fields": nested}


def _has_not_null_constraint(column: exp.ColumnDef) -> bool:
    """True iff *column* carries a ``NOT NULL`` constraint."""
    for constraint in column.args.get("constraints") or ():
        if isinstance(constraint, exp.ColumnConstraint) and isinstance(
            constraint.kind,
            exp.NotNullColumnConstraint,
        ):
            return True
    return False


def _catalog_schema_fields(
    project_id: str,
    dataset_id: str,
    table_id: str,
    ctx: AppContext,
) -> list[dict[str, Any]] | None:
    """Render the catalog entry's schema into REST fields, or ``None``.

    The catalog entry for a just-created table or view was introspected
    from DuckDB by the post-execution DDL sync, so its field types are
    already wire-format names (``INTEGER`` / ``RECORD`` / …).
    """
    meta = ctx.catalog.get_table(project_id, dataset_id, table_id)
    if meta is None:
        return None
    return [_table_field_to_wire(field) for field in meta.schema_.fields]


def _table_field_to_wire(field: TableFieldSchema) -> dict[str, Any]:
    """Recursively render a catalog ``TableFieldSchema`` into a REST field."""
    entry: dict[str, Any] = {"name": field.name, "type": field.type, "mode": field.mode}
    if field.fields:
        entry["fields"] = [_table_field_to_wire(nested) for nested in field.fields]
    return entry


__all__ = [
    "DDL_BQ_WIRE_TYPES",
    "DDL_OPERATION_BY_STATEMENT",
    "ddl_operation_for",
    "ddl_result_schema_fields",
    "resolve_ddl_operation",
]
