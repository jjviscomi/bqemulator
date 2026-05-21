"""Grantee-list matching against a caller identity.

The matcher answers: "given a row access policy's grantees and the
current caller, which policies apply?" See ADR 0018 for the full
matching contract.

Match rules (in evaluation order — first match wins):

* ``allUsers`` — always matches.
* ``allAuthenticatedUsers`` — matches when the caller is not the
  default anonymous fallback.
* ``user:<email>`` / ``serviceAccount:<email>`` — case-insensitive on
  the domain part, case-sensitive on the local part (per RFC 5321).
* ``domain:<host>`` — matches a ``user:`` or ``serviceAccount:``
  caller whose email host equals ``<host>`` (case-insensitive).
* ``group:<email>`` — matches when the caller's ``groups`` list
  contains ``<email>``. Groups come from the emulator-only
  ``X-Bqemu-Groups`` header.

Anything else (typo'd kind prefix, missing ``:``) is a non-match.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from bqemulator.catalog.models import RowAccessPolicyMeta
    from bqemulator.row_access.identity import CallerIdentity


def grantee_matches(grantee: str, caller: CallerIdentity) -> bool:
    """Return True iff ``grantee`` resolves to ``caller``."""
    g = grantee.strip()
    if not g:
        return False

    if g == "allUsers":
        return True

    if g == "allAuthenticatedUsers":
        return caller.is_authenticated

    if ":" not in g:
        return False

    kind, value = g.split(":", 1)
    value = value.strip()
    if not value:
        return False

    if kind in ("user", "serviceAccount"):
        return _email_matches(value, caller, kind)

    if kind == "domain":
        host = value.lower()
        caller_domain = caller.domain
        return caller_domain is not None and caller_domain == host

    if kind == "group":
        # caller.groups is treated case-sensitively on the local part
        # to match the user/serviceAccount rule. The host is normalised
        # to lower-case in the matcher.
        norm_value = _normalise_email(value)
        return any(_normalise_email(g) == norm_value for g in caller.groups)

    return False


def _email_matches(email: str, caller: CallerIdentity, expect_kind: str) -> bool:
    """Compare a grantee's email against the caller's principal."""
    if caller.kind != expect_kind:
        return False
    caller_email = caller.email
    if caller_email is None:
        return False
    return _normalise_email(email) == _normalise_email(caller_email)


def _normalise_email(email: str) -> str:
    """Lower-case the host part of an email; preserve the local part."""
    if "@" not in email:
        return email
    local, host = email.split("@", 1)
    return f"{local}@{host.lower()}"


class GranteeMatcher:
    """Filter a list of policies to those whose grantees match the caller.

    Constructed once per query (the rewriter reuses it across every
    table reference). Pure, no I/O — safe to call from the SQL
    rewriter without a write lock.
    """

    def __init__(self, caller: CallerIdentity) -> None:
        self._caller = caller

    @property
    def caller(self) -> CallerIdentity:
        """Expose the caller identity (used by the rewriter for logging)."""
        return self._caller

    def matches_any(self, grantees: Iterable[str]) -> bool:
        """Return True iff any grantee in ``grantees`` matches the caller."""
        return any(grantee_matches(g, self._caller) for g in grantees)

    def applicable_policies(
        self,
        policies: Iterable[RowAccessPolicyMeta],
    ) -> tuple[RowAccessPolicyMeta, ...]:
        """Return the subset of ``policies`` that grant access to the caller."""
        return tuple(p for p in policies if self.matches_any(p.grantees))


__all__ = ["GranteeMatcher", "grantee_matches"]
