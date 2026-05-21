"""Grantee matcher tests — see ADR 0018 for rules."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bqemulator.catalog.models import RowAccessPolicyMeta
from bqemulator.row_access.identity import DEFAULT_CALLER, CallerIdentity
from bqemulator.row_access.matcher import GranteeMatcher, grantee_matches

pytestmark = pytest.mark.unit


def _make_caller(
    principal: str = "user:alice@example.com",
    *,
    groups: tuple[str, ...] = (),
    is_authenticated: bool = True,
) -> CallerIdentity:
    return CallerIdentity(
        principal=principal,
        groups=groups,
        is_authenticated=is_authenticated,
    )


class TestGranteeMatches:
    def test_user_match(self) -> None:
        c = _make_caller("user:alice@example.com")
        assert grantee_matches("user:alice@example.com", c)

    def test_user_host_case_insensitive(self) -> None:
        c = _make_caller("user:alice@example.com")
        assert grantee_matches("user:alice@EXAMPLE.com", c)

    def test_user_local_part_case_sensitive(self) -> None:
        c = _make_caller("user:alice@example.com")
        assert not grantee_matches("user:Alice@example.com", c)

    def test_user_kind_mismatch(self) -> None:
        c = _make_caller("serviceAccount:sa@x")
        assert not grantee_matches("user:sa@x", c)

    def test_service_account_match(self) -> None:
        c = _make_caller("serviceAccount:sa@x.iam.gserviceaccount.com")
        assert grantee_matches("serviceAccount:sa@x.iam.gserviceaccount.com", c)

    def test_domain_match_for_user(self) -> None:
        c = _make_caller("user:alice@example.com")
        assert grantee_matches("domain:example.com", c)
        assert grantee_matches("domain:EXAMPLE.COM", c)

    def test_domain_no_match_other_domain(self) -> None:
        c = _make_caller("user:alice@example.com")
        assert not grantee_matches("domain:other.com", c)

    def test_domain_does_not_match_groups(self) -> None:
        c = _make_caller("group:admins@example.com")
        assert not grantee_matches("domain:example.com", c)

    def test_group_via_groups_header(self) -> None:
        c = _make_caller("user:b@x", groups=("admins@x", "readers@y"))
        assert grantee_matches("group:admins@x", c)
        assert grantee_matches("group:admins@X", c)  # host case-insensitive
        assert not grantee_matches("group:other@x", c)

    def test_group_no_match_when_not_in_groups(self) -> None:
        c = _make_caller("user:b@x")
        assert not grantee_matches("group:admins@x", c)

    def test_all_users_always_matches(self) -> None:
        assert grantee_matches("allUsers", _make_caller(is_authenticated=True))
        assert grantee_matches(
            "allUsers",
            _make_caller(principal=DEFAULT_CALLER, is_authenticated=False),
        )

    def test_all_authenticated_users_only_when_authenticated(self) -> None:
        assert grantee_matches(
            "allAuthenticatedUsers",
            _make_caller(is_authenticated=True),
        )
        assert not grantee_matches(
            "allAuthenticatedUsers",
            _make_caller(principal=DEFAULT_CALLER, is_authenticated=False),
        )

    @pytest.mark.parametrize(
        "bad_grantee",
        [
            "",  # empty
            "   ",
            "weird:value",  # unrecognised kind
            "user:",  # empty value
            "user",  # missing colon
            "domain:",
            "group:",
        ],
    )
    def test_malformed_grantee_does_not_match(self, bad_grantee: str) -> None:
        assert not grantee_matches(bad_grantee, _make_caller())


class TestGranteeMatcher:
    def test_matches_any(self) -> None:
        c = _make_caller("user:alice@example.com")
        m = GranteeMatcher(c)
        assert m.matches_any(["user:alice@example.com"])
        assert m.matches_any(["user:other@x", "allUsers"])
        assert not m.matches_any(["user:other@x"])

    def test_matches_any_empty(self) -> None:
        c = _make_caller("user:alice@example.com")
        m = GranteeMatcher(c)
        assert not m.matches_any([])

    def test_applicable_policies_filters(self) -> None:
        c = _make_caller("user:alice@example.com")
        m = GranteeMatcher(c)
        now = datetime(2026, 1, 1, tzinfo=UTC)
        a = RowAccessPolicyMeta(
            project_id="p",
            dataset_id="d",
            table_id="t",
            policy_id="a",
            filter_predicate="1=1",
            grantees=("user:alice@example.com",),
            creation_time=now,
            last_modified_time=now,
            etag='"a"',
        )
        b = RowAccessPolicyMeta(
            project_id="p",
            dataset_id="d",
            table_id="t",
            policy_id="b",
            filter_predicate="2=2",
            grantees=("user:other@x",),
            creation_time=now,
            last_modified_time=now,
            etag='"b"',
        )
        c_pol = RowAccessPolicyMeta(
            project_id="p",
            dataset_id="d",
            table_id="t",
            policy_id="c",
            filter_predicate="3=3",
            grantees=("allUsers",),
            creation_time=now,
            last_modified_time=now,
            etag='"c"',
        )
        applicable = m.applicable_policies([a, b, c_pol])
        assert {p.policy_id for p in applicable} == {"a", "c"}

    def test_caller_property_exposes_identity(self) -> None:
        c = _make_caller("user:alice@example.com")
        m = GranteeMatcher(c)
        assert m.caller is c
