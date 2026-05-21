"""Row access policy lifecycle and validation.

The :class:`RowAccessPolicyManager` is the only place that mutates
:class:`RowAccessPolicyMeta` rows in the catalog. It enforces:

* The target table exists (raises :class:`NotFoundError` otherwise).
* The target table is not a SNAPSHOT or MATERIALIZED_VIEW (Phase 7
  rejects DML on those types; row access policies on read-only
  artefacts would have no point of attachment).
* ``policy_id`` matches BigQuery's grammar (``[A-Za-z0-9_]+``, max
  256 chars).
* ``filter_predicate`` is a parseable BigQuery boolean expression and
  contains no subqueries — BigQuery rejects subqueries inside RAP
  filters because they would reference rows outside the policy's
  scope (see [BigQuery docs][1]).
* ``grantees`` are valid IAM-member strings. Empty grantee lists are
  allowed but produce a policy that matches *no* caller.

[1]: https://cloud.google.com/bigquery/docs/reference/rest/v2/rowAccessPolicies
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from bqemulator.catalog.etag import generate_etag
from bqemulator.catalog.models import RowAccessPolicyMeta
from bqemulator.domain.errors import (
    InvalidQueryError,
    ResourceRef,
    ValidationError,
    resource_not_found,
)
from bqemulator.observability.logging_ import get_logger

if TYPE_CHECKING:  # pragma: no cover
    from bqemulator.catalog.repository import CatalogRepository
    from bqemulator.domain.clock import Clock

_log = get_logger(__name__)

_POLICY_ID_RE = re.compile(r"^[A-Za-z0-9_]{1,256}$")

# Grantees follow IAM member grammar.
_GRANTEE_PREFIXES = (
    "user:",
    "serviceAccount:",
    "group:",
    "domain:",
)
_GRANTEE_LITERALS = ("allUsers", "allAuthenticatedUsers")


def _validate_policy_id(policy_id: str) -> None:
    if not _POLICY_ID_RE.match(policy_id):
        raise ValidationError(
            f"policy_id must be 1-256 chars from [A-Za-z0-9_], got {policy_id!r}",
        )


def _validate_grantees(grantees: tuple[str, ...]) -> None:
    seen: set[str] = set()
    for raw in grantees:
        g = raw.strip()
        if not g:
            raise ValidationError("Empty grantee in row access policy")
        if g in seen:
            raise ValidationError(f"Duplicate grantee {g!r} in row access policy")
        seen.add(g)
        if g in _GRANTEE_LITERALS:
            continue
        if not any(g.startswith(p) for p in _GRANTEE_PREFIXES):
            raise ValidationError(
                f"Grantee {g!r} must be one of allUsers, allAuthenticatedUsers, "
                "or have a user:/serviceAccount:/group:/domain: prefix",
            )
        if ":" in g:
            _, value = g.split(":", 1)
            if not value.strip():
                raise ValidationError(f"Grantee {g!r} has an empty value")


def _validate_filter_predicate(predicate: str) -> None:
    """Reject empty filters and obvious subquery / multi-statement abuse."""
    text = predicate.strip()
    if not text:
        raise ValidationError("filter_predicate cannot be empty")

    # SQLGlot is the canonical parser — but we also pre-screen the
    # string for ``;`` and the ``SELECT`` keyword so a policy can never
    # smuggle in a multi-statement payload that targets a table we
    # haven't checked. SQLGlot would happily parse "1=1; DROP TABLE ..."
    # because it scopes the WHERE inside the rewriter — the pre-screen
    # is the belt-and-braces layer.
    if ";" in text:
        raise ValidationError("filter_predicate must not contain ';'")
    upper = text.upper()
    forbidden = ("SELECT ", "FROM ", "INSERT ", "UPDATE ", "DELETE ", "MERGE ")
    for marker in forbidden:
        if marker in upper:
            raise ValidationError(
                "filter_predicate must be a simple boolean expression; "
                f"keyword {marker.strip()!r} is not allowed",
            )

    # Validate parseability via SQLGlot. We wrap the predicate in a
    # sentinel SELECT so the parser sees it in WHERE position.
    import sqlglot

    try:
        sqlglot.parse_one(f"SELECT * FROM t WHERE ({text})", read="bigquery")
    except Exception as exc:
        raise ValidationError(
            f"filter_predicate is not a valid BigQuery boolean expression: {exc}",
        ) from exc


class RowAccessPolicyManager:
    """Validate, persist, and load row access policies.

    The manager owns no SQL execution — the rewriter reads from the
    catalog directly. Centralising validation here keeps the REST
    adapter thin and makes the manager unit-testable without spinning
    up FastAPI.
    """

    def __init__(self, *, catalog: CatalogRepository, clock: Clock) -> None:
        self._catalog = catalog
        self._clock = clock

    # -- Read paths -----------------------------------------------------

    def list_for_table(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
    ) -> tuple[RowAccessPolicyMeta, ...]:
        """Return every policy on a table, in stable id order."""
        policies = self._catalog.list_row_access_policies(
            project_id,
            dataset_id,
            table_id,
        )
        return tuple(sorted(policies, key=lambda p: p.policy_id))

    def get(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
        policy_id: str,
    ) -> RowAccessPolicyMeta | None:
        """Return one policy or ``None``."""
        return self._catalog.get_row_access_policy(
            project_id,
            dataset_id,
            table_id,
            policy_id,
        )

    # -- Write paths ----------------------------------------------------

    def create(
        self,
        *,
        project_id: str,
        dataset_id: str,
        table_id: str,
        policy_id: str,
        filter_predicate: str,
        grantees: tuple[str, ...],
    ) -> RowAccessPolicyMeta:
        """Insert a new policy after validating it against the catalog."""
        self._validate_target(project_id, dataset_id, table_id)
        _validate_policy_id(policy_id)
        _validate_grantees(grantees)
        _validate_filter_predicate(filter_predicate)

        now = self._clock.now()
        policy = RowAccessPolicyMeta(
            project_id=project_id,
            dataset_id=dataset_id,
            table_id=table_id,
            policy_id=policy_id,
            filter_predicate=filter_predicate,
            grantees=grantees,
            creation_time=now,
            last_modified_time=now,
            etag=generate_etag(
                project_id,
                dataset_id,
                table_id,
                policy_id,
                str(now),
            ),
        )
        result = self._catalog.create_row_access_policy(policy)
        _log.info(
            "row_access.create",
            project=project_id,
            dataset=dataset_id,
            table=table_id,
            policy=policy_id,
            grantees=list(grantees),
        )
        return result

    def update(
        self,
        *,
        project_id: str,
        dataset_id: str,
        table_id: str,
        policy_id: str,
        filter_predicate: str,
        grantees: tuple[str, ...],
    ) -> RowAccessPolicyMeta:
        """Replace a policy's filter and grantees, preserving creation time."""
        existing = self._catalog.get_row_access_policy(
            project_id,
            dataset_id,
            table_id,
            policy_id,
        )
        if existing is None:
            raise resource_not_found(
                ResourceRef(
                    "row_access_policy",
                    project_id,
                    dataset_id,
                    table_id,
                ),
            )
        _validate_grantees(grantees)
        _validate_filter_predicate(filter_predicate)
        now = self._clock.now()
        updated = existing.model_copy(
            update={
                "filter_predicate": filter_predicate,
                "grantees": grantees,
                "last_modified_time": now,
                "etag": generate_etag(
                    project_id,
                    dataset_id,
                    table_id,
                    policy_id,
                    str(now),
                ),
            },
        )
        result = self._catalog.update_row_access_policy(updated)
        _log.info(
            "row_access.update",
            project=project_id,
            dataset=dataset_id,
            table=table_id,
            policy=policy_id,
        )
        return result

    def delete(
        self,
        *,
        project_id: str,
        dataset_id: str,
        table_id: str,
        policy_id: str,
        not_found_ok: bool = False,
    ) -> None:
        """Delete one policy."""
        self._catalog.delete_row_access_policy(
            project_id,
            dataset_id,
            table_id,
            policy_id,
            not_found_ok=not_found_ok,
        )
        _log.info(
            "row_access.delete",
            project=project_id,
            dataset=dataset_id,
            table=table_id,
            policy=policy_id,
            not_found_ok=not_found_ok,
        )

    def batch_delete(
        self,
        *,
        project_id: str,
        dataset_id: str,
        table_id: str,
        policy_ids: tuple[str, ...],
        force: bool = False,  # noqa: ARG002 — emulator has no IAM widening check
    ) -> None:
        """Delete several policies atomically.

        ``force`` is accepted for REST-shape parity with BigQuery's
        documented ``BatchDeleteRowAccessPoliciesRequest`` but the
        emulator never enforces the "would widen access" check (we
        don't model IAM).
        """
        if not policy_ids:
            raise ValidationError("policy_ids cannot be empty for batch_delete")
        # Validate every id up front so the operation is atomic.
        missing = [
            pid
            for pid in policy_ids
            if self._catalog.get_row_access_policy(
                project_id,
                dataset_id,
                table_id,
                pid,
            )
            is None
        ]
        if missing:
            raise resource_not_found(
                ResourceRef(
                    "row_access_policy",
                    project_id,
                    dataset_id,
                    resource_id=f"{table_id}.{','.join(missing)}",
                ),
            )
        for pid in policy_ids:
            self._catalog.delete_row_access_policy(
                project_id,
                dataset_id,
                table_id,
                pid,
            )
        _log.info(
            "row_access.batch_delete",
            project=project_id,
            dataset=dataset_id,
            table=table_id,
            count=len(policy_ids),
        )

    # -- Helpers --------------------------------------------------------

    def _validate_target(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
    ) -> None:
        table = self._catalog.get_table(project_id, dataset_id, table_id)
        if table is None:
            raise resource_not_found(
                ResourceRef("table", project_id, dataset_id, table_id),
            )
        if table.table_type in ("SNAPSHOT", "MATERIALIZED_VIEW"):
            raise InvalidQueryError(
                "Row access policies cannot be attached to "
                f"{table.table_type} tables; "
                f"{project_id}.{dataset_id}.{table_id} is read-only.",
            )


__all__ = ["RowAccessPolicyManager"]
