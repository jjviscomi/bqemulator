"""Caller-identity resolution for the row-access rewriter.

See [ADR 0018](../../docs/adr/0018-caller-identity-and-row-access-enforcement.md)
for the full decision record.

The resolver returns a :class:`CallerIdentity` value composed of:

* ``principal`` — the IAM-member-shaped string used by the matcher
  (``"user:alice@example.com"``, ``"serviceAccount:sa@…"``,
  ``"allUsers"`` …).
* ``groups`` — emulator-only escape hatch sourced from
  ``X-Bqemu-Groups``. Used so test code can model group membership
  without standing up a real Google Workspace.
* ``is_authenticated`` — ``False`` only for the default fallback
  identity. Lets the matcher accept ``allAuthenticatedUsers`` without
  also accepting the anonymous fallback.

The resolver is intentionally tolerant of malformed input: every
unknown shape collapses to the default fallback. It never raises.
"""

from __future__ import annotations

from dataclasses import dataclass

CALLER_HEADER = "x-bqemu-caller"
"""Primary header for the caller's IAM member identity (lower-case)."""

USER_PROJECT_HEADER = "x-goog-user-project"
"""Standard BigQuery billing-project header used as a fallback only."""

GROUPS_HEADER = "x-bqemu-groups"
"""Comma-separated group emails the caller is a member of.

Each entry may be either a bare email (``admins@example.com``) or
the full IAM-member form (``group:admins@example.com``); the parser
strips the ``group:`` prefix so the matcher always sees bare emails.
This dual-form tolerance lets conformance fixtures that substitute
``${GROUP}`` (which carries ``group:<addr>``) into ``headers.json``
produce the same caller identity as integration tests that pass bare
emails.
"""

DEFAULT_CALLER = "user:anonymous@bqemulator.local"
"""Default identity when no header supplies one."""


@dataclass(slots=True, frozen=True)
class CallerIdentity:
    """An IAM-member identity, plus the emulator-only group escape hatch."""

    principal: str
    groups: tuple[str, ...] = ()
    is_authenticated: bool = True

    @property
    def kind(self) -> str:
        """Return the IAM-member kind: ``user``, ``serviceAccount``, ``group``, etc."""
        if ":" not in self.principal:
            return self.principal
        return self.principal.split(":", 1)[0]

    @property
    def email(self) -> str | None:
        """Return the email part for ``user:`` / ``serviceAccount:`` callers."""
        if ":" not in self.principal:
            return None
        kind, rest = self.principal.split(":", 1)
        if kind in ("user", "serviceAccount"):
            return rest
        return None

    @property
    def domain(self) -> str | None:
        """Return the email's host part, lower-cased."""
        email = self.email
        if email is None or "@" not in email:
            return None
        return email.split("@", 1)[1].lower()


def parse_caller(value: str | None) -> str | None:
    """Trim + validate a caller-header value.

    Returns ``None`` if the value is empty or obviously malformed
    (contains whitespace, control bytes, or commas — none of which are
    valid IAM-member characters).
    """
    if value is None:
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    # Reject whitespace and bytes that the matcher would mis-handle.
    for bad in (" ", "\t", "\n", "\r", ","):
        if bad in trimmed:
            return None
    return trimmed


_GROUP_PREFIX = "group:"


def _parse_groups(raw: str | None) -> tuple[str, ...]:
    """Split a comma-separated group-header value into bare emails.

    Strips the ``group:`` IAM-member prefix when present so the
    matcher (which expects bare emails per its
    ``test_group_via_groups_header`` contract) sees a uniform form
    regardless of whether the caller passed
    ``admins@example.com`` or ``group:admins@example.com`` in the
    ``X-Bqemu-Groups`` header.
    """
    if raw is None:
        return ()
    parts: list[str] = []
    for entry in raw.split(","):
        trimmed = entry.strip()
        if not trimmed:
            continue
        if trimmed.startswith(_GROUP_PREFIX):
            trimmed = trimmed[len(_GROUP_PREFIX) :].strip()
            if not trimmed:
                continue
        parts.append(trimmed)
    return tuple(parts)


def _identity_for_user_project(project_id: str) -> str:
    """Synthetic identity for a billing-project-only fallback."""
    safe = project_id.strip() or "unknown"
    return f"user:caller@{safe}.iam.gserviceaccount.com"


def _resolve_from_pairs(pairs: dict[str, str]) -> CallerIdentity:
    """Apply the resolution order to a header/metadata mapping.

    ``pairs`` keys must already be lower-case.
    """
    primary = parse_caller(pairs.get(CALLER_HEADER))
    if primary is not None:
        return CallerIdentity(
            principal=primary,
            groups=_parse_groups(pairs.get(GROUPS_HEADER)),
            is_authenticated=True,
        )

    user_project = parse_caller(pairs.get(USER_PROJECT_HEADER))
    if user_project is not None:
        return CallerIdentity(
            principal=_identity_for_user_project(user_project),
            groups=_parse_groups(pairs.get(GROUPS_HEADER)),
            is_authenticated=True,
        )

    return CallerIdentity(
        principal=DEFAULT_CALLER,
        groups=_parse_groups(pairs.get(GROUPS_HEADER)),
        is_authenticated=False,
    )


def resolve_caller_from_headers(headers: object) -> CallerIdentity:
    """Resolve a caller from a Starlette / Werkzeug-style headers mapping.

    Accepts anything that exposes ``__iter__`` over ``(name, value)``
    pairs OR a ``.get(name)`` method. HTTP header names are case-
    insensitive, so we lower-case keys before consulting the resolver.
    """
    pairs: dict[str, str] = {}
    # Starlette `Headers` and werkzeug `Headers` both implement items().
    items: list[tuple[object, object]] | None = None
    if hasattr(headers, "items"):
        try:
            items = list(headers.items())
        except Exception:  # noqa: BLE001 — fall through to .raw probe
            items = None
    if items is None and hasattr(headers, "raw"):
        try:
            items = [(k.decode("latin-1"), v.decode("latin-1")) for k, v in headers.raw]
        except Exception:  # noqa: BLE001
            items = None
    if items is None:
        return CallerIdentity(
            principal=DEFAULT_CALLER,
            groups=(),
            is_authenticated=False,
        )
    for name, value in items:
        pairs[str(name).lower()] = str(value)
    return _resolve_from_pairs(pairs)


def resolve_caller_from_metadata(
    metadata: list[tuple[str, str]] | tuple[tuple[str, str], ...] | None,
) -> CallerIdentity:
    """Resolve a caller from gRPC metadata.

    gRPC normalises metadata keys to lower-case at the wire level, so
    the resolver can look them up directly.
    """
    if not metadata:
        return CallerIdentity(
            principal=DEFAULT_CALLER,
            groups=(),
            is_authenticated=False,
        )
    pairs = {str(k).lower(): str(v) for k, v in metadata}
    return _resolve_from_pairs(pairs)


__all__ = [
    "CALLER_HEADER",
    "DEFAULT_CALLER",
    "GROUPS_HEADER",
    "USER_PROJECT_HEADER",
    "CallerIdentity",
    "parse_caller",
    "resolve_caller_from_headers",
    "resolve_caller_from_metadata",
]
