"""Caller-identity resolver tests — see ADR 0018."""

from __future__ import annotations

import pytest

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

pytestmark = pytest.mark.unit


class _HeadersDict:
    """A minimal Starlette-Headers-shaped object for tests."""

    def __init__(self, mapping: dict[str, str]) -> None:
        self._d = mapping

    def items(self) -> list[tuple[str, str]]:
        return list(self._d.items())


class TestParseCaller:
    def test_returns_none_for_none(self) -> None:
        assert parse_caller(None) is None

    def test_returns_none_for_blank(self) -> None:
        assert parse_caller("") is None
        assert parse_caller("   ") is None

    def test_strips_whitespace(self) -> None:
        assert parse_caller(" user:a@x ") == "user:a@x"

    @pytest.mark.parametrize(
        "bad",
        [
            "user a@x",  # internal space
            "user:a@x,group:b",  # comma — too easy to typo into list-shape
            "user:a@x\nextra",  # internal newline
            "user:a@x\textra",  # internal tab
        ],
    )
    def test_rejects_malformed(self, bad: str) -> None:
        assert parse_caller(bad) is None

    @pytest.mark.parametrize(
        ("padded", "expected"),
        [
            ("  user:a@x  ", "user:a@x"),
            ("user:a@x\n", "user:a@x"),  # trailing whitespace stripped
            ("user:a@x\t", "user:a@x"),
        ],
    )
    def test_strips_outer_whitespace(self, padded: str, expected: str) -> None:
        assert parse_caller(padded) == expected


class TestResolveFromHeaders:
    def test_default_when_empty(self) -> None:
        ident = resolve_caller_from_headers(_HeadersDict({}))
        assert ident.principal == DEFAULT_CALLER
        assert ident.is_authenticated is False

    def test_x_bqemu_caller_wins(self) -> None:
        headers = _HeadersDict(
            {
                CALLER_HEADER: "user:alice@example.com",
                USER_PROJECT_HEADER: "should-be-ignored",
            },
        )
        ident = resolve_caller_from_headers(headers)
        assert ident.principal == "user:alice@example.com"
        assert ident.is_authenticated is True

    def test_user_project_fallback(self) -> None:
        ident = resolve_caller_from_headers(
            _HeadersDict({USER_PROJECT_HEADER: "my-proj"}),
        )
        assert ident.principal == "user:caller@my-proj.iam.gserviceaccount.com"
        assert ident.is_authenticated is True

    def test_groups_parsed(self) -> None:
        ident = resolve_caller_from_headers(
            _HeadersDict(
                {
                    CALLER_HEADER: "user:b@x",
                    GROUPS_HEADER: "admins@x , readers@y, ",
                },
            ),
        )
        assert ident.groups == ("admins@x", "readers@y")

    def test_groups_parsed_strips_group_prefix(self) -> None:
        # P2.a closure-bug follow-up (2026-05-18): the
        # ``X-Bqemu-Groups`` header may carry IAM-member-shaped
        # entries (``group:<addr>``) — that is the form the P2.d
        # conformance fixtures substitute ``${GROUP}`` into via
        # ``headers.json``. The matcher expects bare emails per the
        # ``test_group_via_groups_header`` contract, so the parser
        # strips the ``group:`` prefix and produces the same
        # ``CallerIdentity.groups`` tuple for both the bare-email
        # form and the IAM-member form.
        ident = resolve_caller_from_headers(
            _HeadersDict(
                {
                    CALLER_HEADER: "user:b@x",
                    GROUPS_HEADER: "group:admins@x , group:readers@y, ",
                },
            ),
        )
        assert ident.groups == ("admins@x", "readers@y")

    def test_groups_parsed_mixes_bare_and_prefixed(self) -> None:
        # Mixed bare + prefixed entries produce the same uniform
        # bare-email tuple — the matcher cannot tell them apart.
        ident = resolve_caller_from_headers(
            _HeadersDict(
                {
                    CALLER_HEADER: "user:b@x",
                    GROUPS_HEADER: "admins@x,group:readers@y",
                },
            ),
        )
        assert ident.groups == ("admins@x", "readers@y")

    def test_groups_parsed_drops_naked_prefix(self) -> None:
        # A bare ``group:`` token (no value) is dropped — it carries
        # no useful identity and would confuse the matcher's bare-email
        # comparison.
        ident = resolve_caller_from_headers(
            _HeadersDict(
                {
                    CALLER_HEADER: "user:b@x",
                    GROUPS_HEADER: "group:,admins@x",
                },
            ),
        )
        assert ident.groups == ("admins@x",)

    def test_case_insensitive_header_keys(self) -> None:
        ident = resolve_caller_from_headers(
            _HeadersDict({"X-Bqemu-Caller": "user:c@y"}),
        )
        assert ident.principal == "user:c@y"

    def test_returns_default_when_headers_object_unsupported(self) -> None:
        ident = resolve_caller_from_headers(object())
        assert ident.principal == DEFAULT_CALLER
        assert ident.is_authenticated is False


class TestResolveFromMetadata:
    def test_default_when_metadata_none(self) -> None:
        ident = resolve_caller_from_metadata(None)
        assert ident.principal == DEFAULT_CALLER
        assert not ident.is_authenticated

    def test_default_when_metadata_empty(self) -> None:
        ident = resolve_caller_from_metadata([])
        assert ident.principal == DEFAULT_CALLER

    def test_resolves_from_metadata_pairs(self) -> None:
        ident = resolve_caller_from_metadata(
            [(CALLER_HEADER, "user:a@b"), (GROUPS_HEADER, "g1@x,g2@x")],
        )
        assert ident.principal == "user:a@b"
        assert ident.groups == ("g1@x", "g2@x")

    def test_metadata_lower_cased_keys(self) -> None:
        # gRPC normalises keys to lower-case, but mixed input still works.
        ident = resolve_caller_from_metadata([("X-Bqemu-Caller", "user:c@d")])
        assert ident.principal == "user:c@d"


class TestCallerIdentityProperties:
    def test_kind_for_user(self) -> None:
        c = CallerIdentity(principal="user:a@x")
        assert c.kind == "user"
        assert c.email == "a@x"
        assert c.domain == "x"

    def test_kind_for_service_account(self) -> None:
        c = CallerIdentity(principal="serviceAccount:sa@y.iam.gserviceaccount.com")
        assert c.kind == "serviceAccount"
        assert c.email == "sa@y.iam.gserviceaccount.com"
        assert c.domain == "y.iam.gserviceaccount.com"

    def test_kind_for_special_groups(self) -> None:
        c = CallerIdentity(principal="allUsers")
        assert c.kind == "allUsers"
        assert c.email is None
        assert c.domain is None

    def test_email_none_for_group(self) -> None:
        c = CallerIdentity(principal="group:admins@x")
        assert c.email is None
        assert c.domain is None

    def test_domain_lowercased(self) -> None:
        c = CallerIdentity(principal="user:a@EXAMPLE.com")
        assert c.domain == "example.com"
