"""Pre-translator: qualify unqualified table refs using ``defaultDataset``.

BigQuery's ``QueryJobConfig.default_dataset`` is a project + dataset
reference the parser consults whenever a table reference in the query
body is unqualified (i.e. the SQL contains ``FROM products`` rather
than ``FROM project.dataset.products``). The qualification happens at
parse time and is transparent to the caller — the resolved
fully-qualified name is what BigQuery uses for catalog lookups, IAM
checks, and statistics tracking. Mixed qualification works
(``FROM users AS u JOIN dataset_b.orders AS o`` is valid — only the
unqualified leaves are rewritten).

This rewriter walks the AST and rewrites every ``exp.Table`` node
with no ``db`` / ``catalog`` components to the fully-qualified form
``<project>.<dataset>.<table>``. It skips:

* References to CTE names (the CTE binds inside the query and
  shadows any same-named table).
* References to subquery aliases (the subquery binds the name).
* References to procedural variables (DECLARE'd identifiers shadow
  table names).

The rewriter is conservative: when in doubt about whether an
unqualified name is a table or a CTE/alias/variable, it leaves the
name alone. The trade-off is that occasionally a user could write
``WITH products AS (SELECT … FROM products)`` where the recursive
inner ``products`` would actually resolve to the default-dataset
table — this is a CTE-shadowing edge case BigQuery handles by lexical
scoping, which we replicate by NOT qualifying inside a CTE definition
that shadows the name.
"""

from __future__ import annotations

import sqlglot
from sqlglot import expressions as exp


def qualify_unqualified_tables(
    bq_sql: str,
    *,
    default_project: str,
    default_dataset: str,
) -> str:
    """Rewrite unqualified table refs in ``bq_sql`` to the default project + dataset.

    Args:
        bq_sql: The BigQuery query text (single statement or multi-
            statement script).
        default_project: The default project for unqualified refs
            (e.g. ``"your-bigquery-project"``).
        default_dataset: The default dataset within ``default_project``.

    Returns:
        Rewritten SQL with every bare table reference qualified to
        ``<default_project>.<default_dataset>.<table>``. Already-
        qualified references are preserved verbatim. CTE names and
        subquery aliases are NOT qualified — they bind inside the
        query and shadow same-named tables.
    """
    if not default_project or not default_dataset:
        return bq_sql

    try:
        statements = sqlglot.parse(bq_sql, read="bigquery")
    except sqlglot.errors.ParseError:
        # If the SQL doesn't parse, leave it untouched — the downstream
        # translator will surface the parse error with the right shape.
        return bq_sql

    rewritten: list[str] = []
    for statement in statements:
        if statement is None:
            continue
        cte_names = _collect_cte_names(statement)
        _qualify_tables(
            statement,
            default_project=default_project,
            default_dataset=default_dataset,
            shadowed_names=cte_names,
        )
        rewritten.append(statement.sql(dialect="bigquery"))

    return ";\n".join(rewritten)


def _collect_cte_names(root: exp.Expression) -> set[str]:
    """Collect every CTE alias name defined anywhere in the AST.

    CTE bindings are lexically scoped in standard SQL, but BigQuery's
    parser flattens them so a top-level CTE shadows the name
    everywhere in the query. We use the same flat-shadow heuristic
    here — a CTE named ``products`` shadows the unqualified
    ``products`` reference in the whole query.
    """
    names: set[str] = set()
    for cte in root.find_all(exp.CTE):
        alias = cte.alias_or_name
        if alias:
            names.add(alias)
    return names


def _qualify_tables(
    root: exp.Expression,
    *,
    default_project: str,
    default_dataset: str,
    shadowed_names: set[str],
) -> None:
    """Walk ``root`` and qualify every unqualified ``exp.Table`` node."""
    for table in root.find_all(exp.Table):
        if _is_qualified(table):
            continue
        bare_name = table.name
        if not bare_name or bare_name in shadowed_names:
            continue
        table.set("db", exp.to_identifier(default_dataset))
        table.set("catalog", exp.to_identifier(default_project))


def _is_qualified(table: exp.Table) -> bool:
    """True when ``table`` already carries a dataset (db) or project (catalog)."""
    return bool(table.args.get("db") or table.args.get("catalog"))


__all__ = ["qualify_unqualified_tables"]
