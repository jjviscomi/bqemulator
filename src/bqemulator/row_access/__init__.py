"""Phase 8 — row access policy enforcement and caller-identity resolution.

The package contains three concerns:

* :mod:`bqemulator.row_access.identity` — extract the caller's IAM
  member identity from REST headers / gRPC metadata. See ADR 0018.
* :mod:`bqemulator.row_access.policy` — the
  :class:`RowAccessPolicyManager` that mediates CRUD and the matcher
  used by the SQL rewriter.
* :mod:`bqemulator.row_access.matcher` — pure functions evaluating
  whether a grantee list matches a caller (used by both the manager
  and the rewriter; tested via Hypothesis).
"""

from __future__ import annotations

from bqemulator.row_access.identity import (
    CALLER_HEADER,
    DEFAULT_CALLER,
    GROUPS_HEADER,
    USER_PROJECT_HEADER,
    CallerIdentity,
    parse_caller,
    resolve_caller_from_headers,
    resolve_caller_from_metadata,
)
from bqemulator.row_access.matcher import (
    GranteeMatcher,
    grantee_matches,
)
from bqemulator.row_access.policy import RowAccessPolicyManager

__all__ = [
    "CALLER_HEADER",
    "DEFAULT_CALLER",
    "GROUPS_HEADER",
    "USER_PROJECT_HEADER",
    "CallerIdentity",
    "GranteeMatcher",
    "RowAccessPolicyManager",
    "grantee_matches",
    "parse_caller",
    "resolve_caller_from_headers",
    "resolve_caller_from_metadata",
]
