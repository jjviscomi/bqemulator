"""Row access policy enforcement rewriter.

Runs as a *pre-translator* pass over BigQuery SQL (alongside the
time-travel rewriter and INFORMATION_SCHEMA expander). For each
``Table`` reference it:

1. Looks up the table in the catalog.
2. If the table is a ``VIEW``, recursively rewrites the view body
   under the *authorized-view bypass* rules from ADR 0018, then
   substitutes the rewritten body inline as a derived subquery.
3. Otherwise applies the caller's matching row access policies by
   wrapping the reference in a ``(SELECT * FROM ref WHERE …) alias``
   subquery. When the table has policies but none match the caller,
   the rewriter wraps with ``WHERE FALSE`` (BigQuery's "absence is
   denial" rule).

The pass short-circuits when the catalog has zero policies — Phase
7's hot path stays cheap for every project that hasn't enabled
RAP. See ADR 0018 for the full design and matching rules.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlglot
from sqlglot import exp

from bqemulator.observability.logging_ import get_logger
from bqemulator.row_access.matcher import GranteeMatcher
from bqemulator.storage.engine import CATALOG_SCHEMA, SNAPSHOTS_SCHEMA

if TYPE_CHECKING:  # pragma: no cover
    from bqemulator.catalog.models import RowAccessPolicyMeta
    from bqemulator.catalog.repository import CatalogRepository
    from bqemulator.row_access.identity import CallerIdentity

_log = get_logger(__name__)

# Cap recursion when expanding view → view → view chains. The catalog
# would already reject cyclic VIEW definitions at create-time, but a
# deep nesting is still worth bounding so a malformed catalog can't
# spin the rewriter forever.
_MAX_VIEW_DEPTH = 8

_RESERVED_SCHEMAS = frozenset({CATALOG_SCHEMA, SNAPSHOTS_SCHEMA})


def rewrite_for_row_access(
    bq_sql: str,
    *,
    project_id: str,
    caller: CallerIdentity,
    catalog: CatalogRepository,
) -> str:
    """Apply caller-bound row access policies to ``bq_sql``.

    The rewriter parses with SQLGlot in BigQuery dialect, walks every
    ``Table`` node, expands VIEW references (so authorized-view bypass
    can be checked), and wraps protected *read* references in derived
    subqueries with the policy's filter.

    Always runs the parse + walk pass so regular views (with no
    policies anywhere) are also expanded. The walk itself is O(n) in
    the number of tables in the SQL and pays one cached catalog
    lookup per reference, so the cost is dominated by the parse — the
    same cost the time-travel rewriter already pays.

    DML write *targets* (the destination table of INSERT / UPDATE /
    DELETE / MERGE) are NEVER rewritten — wrapping them in a subquery
    would break the underlying SQL grammar and BigQuery itself only
    applies RAP to reads. The emulator's WHERE clauses inside DML
    bodies do receive RAP enforcement on the rows they reference.
    """
    try:
        tree = sqlglot.parse_one(bq_sql, read="bigquery")
    except Exception:  # noqa: BLE001 — let downstream layers surface the parse error
        return bq_sql

    matcher = GranteeMatcher(caller)
    write_targets = _collect_dml_targets(tree)
    write_targets.update(_collect_ddl_targets(tree))
    rewriter = _Rewriter(
        project_id=project_id,
        catalog=catalog,
        matcher=matcher,
        write_targets=write_targets,
    )

    modified = rewriter.rewrite_tree(tree, depth=0)
    if not modified:
        return bq_sql
    return tree.sql(dialect="bigquery")


def _collect_ddl_targets(tree: exp.Expression) -> set[int]:
    """Return the ``id()`` of every Table node that is a DDL target.

    ``CREATE TABLE foo (...)`` names a *new* table — the ``foo``
    reference is not a read of an existing protected table, and
    wrapping it in a row-access subquery corrupts the DDL grammar
    (``CREATE TABLE (SELECT * FROM foo WHERE FALSE) AS foo (...)``).
    Mirrors :func:`_collect_dml_targets` for the DDL surface.

    ``DROP VIEW foo`` / ``DROP TABLE foo`` similarly name an existing
    object whose row-access status is irrelevant to the drop — the
    rewriter must not rewrite the target into a view-expansion
    subquery (which would yield invalid ``DROP VIEW (SELECT … FROM
    base_table)`` SQL once the new ``sync_created_view`` helper has
    populated the catalog with ``table_type='VIEW'``).

    Subqueries inside ``CREATE TABLE ... AS SELECT`` are NOT marked —
    we want the rewriter to walk into the SELECT body normally.
    """
    targets: set[int] = set()
    for node in tree.find_all(exp.Create):
        head = node.this
        # ``exp.Create.this`` is normally a ``Schema(Table, fields)``
        # for ``CREATE TABLE name (...)`` or just the ``Table`` for
        # ``CREATE TABLE name AS SELECT``. Both shapes are write
        # targets, never reads.
        if isinstance(head, exp.Schema):
            inner = head.this
            if isinstance(inner, exp.Table):
                targets.add(id(inner))
        elif isinstance(head, exp.Table):
            targets.add(id(head))
    for drop_node in tree.find_all(exp.Drop):
        head = drop_node.this
        if isinstance(head, exp.Table):
            targets.add(id(head))
    return targets


def _collect_dml_targets(tree: exp.Expression) -> set[int]:
    """Return the ``id()`` of every Table node that is a DML write target.

    For INSERT / UPDATE / DELETE / MERGE the target table appears as
    the statement's ``this`` argument (or wrapped in a Schema node for
    INSERT). We walk every DML node and remember the target Table's
    object id; the rewriter then skips those during the rewrite pass.
    """
    targets: set[int] = set()
    truncate_cls = getattr(exp, "TruncateTable", None)
    dml_types: tuple[type, ...] = (exp.Insert, exp.Update, exp.Delete, exp.Merge)
    if truncate_cls is not None:
        dml_types = (*dml_types, truncate_cls)
    for node in tree.find_all(*dml_types):  # type: exp.Expression
        target = getattr(node, "this", None)
        if isinstance(target, exp.Table):
            targets.add(id(target))
        elif isinstance(target, exp.Schema):
            inner = target.this
            if isinstance(inner, exp.Table):
                targets.add(id(inner))
    return targets


class _Rewriter:
    """One pass of the rewriter — stateful so we can avoid re-walking."""

    def __init__(
        self,
        *,
        project_id: str,
        catalog: CatalogRepository,
        matcher: GranteeMatcher,
        write_targets: set[int] | None = None,
    ) -> None:
        self._project_id = project_id
        self._catalog = catalog
        self._matcher = matcher
        self._write_targets: set[int] = write_targets or set()
        # Cache policy lookups so a query that joins the same table to
        # itself does the catalog lookup once. The cache is keyed on
        # (project, dataset, table) and stores the *applicable* policy
        # tuple under the current caller (since matching is per-caller
        # and the matcher is constructed per-rewrite).
        self._policy_cache: dict[
            tuple[str, str, str],
            tuple[RowAccessPolicyMeta, ...],
        ] = {}
        self._has_any_policy_cache: dict[tuple[str, str, str], bool] = {}

    def rewrite_tree(
        self,
        tree: exp.Expression,
        *,
        depth: int,
    ) -> bool:
        """Walk ``tree`` and rewrite every protected table reference.

        Returns True iff at least one node was replaced.
        """
        modified = False
        # Snapshot the table nodes up front; we rewrite in-place and
        # ``find_all`` would otherwise re-yield the rewritten subtrees.
        for table_node in list(tree.find_all(exp.Table)):
            if self._rewrite_table(table_node, depth=depth):
                modified = True
        return modified

    def _rewrite_table(
        self,
        node: exp.Table,
        *,
        depth: int,
    ) -> bool:
        """Rewrite one ``Table`` node, returning True iff replaced."""
        # DML write targets are not read references — never wrap them.
        if id(node) in self._write_targets:
            return False

        proj, dataset, table = self._resolve_qualified(node)
        if proj is None or dataset is None or table is None:
            return False

        # Reserved schemas (catalog, snapshots) are live catalog reads,
        # not user tables; row-access enforcement does not apply.
        if dataset in _RESERVED_SCHEMAS:
            return False

        meta = self._catalog.get_table(proj, dataset, table)

        # Row-level access policies apply UNIVERSALLY through views —
        # there is no "authorized-view RAP bypass" in BigQuery. The
        # access[] entry on the base dataset only confers IAM-level
        # read access on the underlying data; row-level enforcement
        # is independent and is checked against the calling user for
        # every base-table reference, regardless of nesting level.
        # See ADR 0018 (revised 2026-05-18) for the closure of the
        # 5 ``authz_view_*`` conformance fixtures whose recordings
        # confirmed BQ's behaviour empirically.
        if meta is not None and meta.table_type == "VIEW" and meta.view_query:
            return self._expand_view(
                node,
                view_project=proj,
                view_dataset=dataset,
                view_table=table,
                view_body=meta.view_query,
                depth=depth,
            )

        # Leaf table — apply caller-bound policies if any.
        applicable, has_any = self._policies_for(proj, dataset, table)
        if not has_any:
            return False
        return self._wrap_with_filter(node, applicable, has_any=True)

    def _policies_for(
        self,
        proj: str,
        dataset: str,
        table: str,
    ) -> tuple[tuple[RowAccessPolicyMeta, ...], bool]:
        """Return (applicable_policies, table_has_any_policy)."""
        key = (proj, dataset, table)
        if key in self._policy_cache:
            return self._policy_cache[key], self._has_any_policy_cache[key]
        all_for_table = self._catalog.list_row_access_policies(
            proj,
            dataset,
            table,
        )
        applicable = self._matcher.applicable_policies(all_for_table)
        self._policy_cache[key] = applicable
        self._has_any_policy_cache[key] = bool(all_for_table)
        return applicable, bool(all_for_table)

    def _expand_view(
        self,
        node: exp.Table,
        *,
        view_project: str,
        view_dataset: str,
        view_table: str,
        view_body: str,
        depth: int,
    ) -> bool:
        """Replace a view reference with its rewritten body as a derived subquery."""
        if depth >= _MAX_VIEW_DEPTH:
            _log.warning(
                "row_access.view_depth_capped",
                view=f"{view_project}.{view_dataset}.{view_table}",
                depth=depth,
            )
            return False
        try:
            body_tree = sqlglot.parse_one(view_body, read="bigquery")
        except Exception:  # noqa: BLE001 — leave the original view ref alone
            return False

        # Recursively rewrite the body. Base-table references inside
        # the view body get the same caller-bound RAP treatment they
        # would get if the user queried them directly — BigQuery does
        # not bypass row-level security for authorized views (ADR 0018
        # revised 2026-05-18).
        self.rewrite_tree(body_tree, depth=depth + 1)

        # Wrap the (possibly modified) body as a derived subquery and
        # preserve the user's alias (or default to the view's id).
        alias_name = self._alias_for_node(node, default=view_table)
        subquery = exp.Subquery(
            this=body_tree,
            alias=exp.TableAlias(this=exp.Identifier(this=alias_name)),
        )
        node.replace(subquery)
        return True

    def _wrap_with_filter(
        self,
        node: exp.Table,
        applicable: tuple[RowAccessPolicyMeta, ...],
        *,
        has_any: bool,
    ) -> bool:
        """Wrap ``node`` in a derived subquery applying matching policies."""
        if not has_any:
            return False
        alias_name = self._alias_for_node(node, default=node.name)
        # Build "SELECT * FROM <node copy>" with the appropriate WHERE.
        inner_table = node.copy()
        # Drop alias on the inner copy — we'll re-introduce it on the
        # outer subquery so identifier resolution sees the same name.
        inner_table.set("alias", None)
        select = exp.Select().select(exp.Star()).from_(inner_table)

        if not applicable:
            # Table has policies but none match the caller — return
            # zero rows (BigQuery's "no access" fallback).
            select = select.where(exp.false())
        else:
            combined = self._or_combine_filters(applicable)
            select = select.where(combined)

        subquery = exp.Subquery(
            this=select,
            alias=exp.TableAlias(this=exp.Identifier(this=alias_name)),
        )
        node.replace(subquery)
        return True

    def _or_combine_filters(
        self,
        policies: tuple[RowAccessPolicyMeta, ...],
    ) -> exp.Expression:
        """OR-combine the policy filters; each predicate parses afresh."""
        clauses: list[exp.Expression] = []
        for policy in policies:
            try:
                parsed = sqlglot.parse_one(
                    f"SELECT * FROM t WHERE ({policy.filter_predicate})",
                    read="bigquery",
                )
            except Exception:  # noqa: BLE001
                # An unparseable policy filter is a bug — but rather
                # than corrupt the rewrite, treat it as denying access.
                _log.warning(
                    "row_access.unparseable_filter",
                    project=policy.project_id,
                    dataset=policy.dataset_id,
                    table=policy.table_id,
                    policy=policy.policy_id,
                )
                clauses.append(exp.false())
                continue
            where = parsed.args.get("where")
            if where is None or where.this is None:
                clauses.append(exp.false())
                continue
            # Strip the wrapping Where node — we want just the boolean.
            clauses.append(where.this.copy())
        if not clauses:
            return exp.false()
        if len(clauses) == 1:
            return clauses[0]
        # ``exp.or_`` returns a ``Condition`` subclass; reassign through
        # an Expression-typed local so mypy stops inferring narrower.
        combined: exp.Expression = clauses[0]
        for clause in clauses[1:]:
            combined = exp.Expression(this=exp.or_(combined, clause))
            # Unwrap the synthetic Expression wrapper introduced above.
            inner = combined.args.get("this")
            if isinstance(inner, exp.Expression):
                combined = inner
        return combined

    def _resolve_qualified(
        self,
        node: exp.Table,
    ) -> tuple[str | None, str | None, str | None]:
        """Return (project, dataset, table) for a Table node, or Nones."""
        # Skip Anonymous wrappers (function-call form).
        if isinstance(node.this, exp.Anonymous):
            return None, None, None
        name = node.name
        dataset = node.db
        if not name or not dataset:
            return None, None, None
        proj = node.catalog or self._project_id
        return proj, dataset, name

    @staticmethod
    def _alias_for_node(node: exp.Table, *, default: str) -> str:
        """Return the user's alias for ``node``, or ``default``."""
        alias = node.args.get("alias")
        if isinstance(alias, exp.TableAlias):
            inner = alias.this
            if isinstance(inner, exp.Identifier):
                return str(inner.this)
        return default


__all__ = ["rewrite_for_row_access"]
