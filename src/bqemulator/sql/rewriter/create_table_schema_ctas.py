"""Pre-translator: ``CREATE TABLE x (schema) AS SELECT …`` → bare CTAS with CAST projections.

BigQuery accepts a single statement that declares an explicit column
schema **and** populates the table from a ``SELECT``::

    CREATE OR REPLACE TABLE `p.ds.t` (id INT64, country STRING) AS
    SELECT 1 AS id, 'US' AS country;

DuckDB's parser does not — it accepts ``CREATE TABLE x (schema)`` *or*
``CREATE TABLE x AS SELECT …`` but not both, and rejects the combined
form with ``Parser Error: syntax error at or near "AS"``. SQLGlot's
BigQuery → DuckDB transpile preserves the combined form verbatim, so
the parser error surfaces at DuckDB execution time.

The rewriter strips the schema clause and wraps each ``SELECT``
projection in ``CAST(<value> AS <declared-type>) AS <declared-name>``.
The resulting bare CTAS produces a table with exactly the column
names and types the user declared in the schema clause, even when the
``SELECT`` literal would otherwise infer a wider or narrower type
(``SELECT 1`` infers ``INT64`` but ``CAST(1 AS NUMERIC)`` lands as
``NUMERIC``, matching BigQuery's behaviour).

The alternative shape — emit two statements (``CREATE TABLE x
(schema)`` then ``INSERT INTO x SELECT …``) — was rejected because
the rewriter would have to know how to insert two statements into a
caller that expects exactly one (the executor + scripting interpreter
both assume one statement per ``query.sql``). The cast-and-elide form
preserves single-statement semantics and is therefore safe inside
``BEGIN TRANSACTION`` blocks and procedural scripts.

Column-count mismatch — fewer schema columns than ``SELECT``
projections, or vice versa — is intentionally left alone. SQLGlot's
transpile will still hand the original combined form to DuckDB and
the user will see the same DuckDB parser error they saw before, which
is no worse than the pre-rewriter behaviour. Matching BQ's exact
wording for the "column count" / "type mismatch" / "duplicate column"
shapes is outside the scope of this rewriter; the bare CTAS path
surfaces those errors via the standard pipeline.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp


def rewrite_create_table_schema_ctas(bq_sql: str) -> str:
    """Convert combined ``CREATE TABLE x (schema) AS SELECT …`` to bare CTAS.

    Returns the input unchanged if SQLGlot cannot parse it (let
    downstream layers surface the error) or if no combined-form
    create-table appears in the AST.
    """
    try:
        tree = sqlglot.parse_one(bq_sql, read="bigquery")
    except Exception:  # noqa: BLE001 — let the translator surface the parse error
        return bq_sql

    modified = False
    for create in tree.find_all(exp.Create):
        if (create.args.get("kind") or "").upper() != "TABLE":
            continue
        schema = create.this
        if not isinstance(schema, exp.Schema):
            continue
        # CTAS body may be a bare ``SELECT`` or a ``UNION [ALL]`` chain
        # of SELECT clauses. DuckDB derives the table's column types
        # from the FIRST SELECT in the chain, so we only need to cast
        # that one.
        first_select = _first_select(create.expression)
        if first_select is None:
            continue
        col_defs = [e for e in schema.expressions if isinstance(e, exp.ColumnDef)]
        projections = first_select.expressions
        if not col_defs:
            continue
        if len(col_defs) != len(projections):
            # Column-count mismatch — leave the SQL alone so the user
            # sees whatever error the downstream parser produces.
            continue

        new_projections: list[exp.Expression] = []
        for col_def, proj in zip(col_defs, projections, strict=True):
            value = proj.this if isinstance(proj, exp.Alias) else proj
            kind = col_def.kind
            if kind is None:
                # Schema entry without a declared type — preserve the
                # projection unchanged so DuckDB infers the type.
                new_projections.append(proj.copy())
                continue
            cast_expr = exp.Cast(this=value.copy(), to=kind.copy())
            new_projections.append(
                exp.Alias(this=cast_expr, alias=col_def.this.copy()),
            )

        first_select.set("expressions", new_projections)

        # Replace the Schema(table, columns) wrapper with the bare
        # Table — DuckDB then sees ``CREATE [OR REPLACE] TABLE <ref>
        # AS SELECT CAST(...) AS col, ...`` which it accepts.
        table = schema.this.copy()
        create.set("this", table)
        modified = True

    if not modified:
        return bq_sql
    return tree.sql(dialect="bigquery")


def _first_select(body: exp.Expression | None) -> exp.Select | None:
    """Walk a CTAS body (``Select`` or nested ``Union``) to the first leaf ``Select``.

    BigQuery's ``CREATE TABLE x (schema) AS SELECT … UNION ALL SELECT
    … UNION ALL SELECT …`` parses as ``Create(expression=Union(this=
    Union(this=Select, expression=Select), expression=Select))``.
    DuckDB derives the column types of the resulting table from the
    first SELECT in the chain; the rewriter therefore only needs to
    cast that one's projections.
    """
    if body is None:
        return None
    if isinstance(body, exp.Select):
        return body
    if isinstance(body, exp.Union):
        return _first_select(body.this)
    return None


__all__ = ["rewrite_create_table_schema_ctas"]
