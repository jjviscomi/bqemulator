"""Hypothesis property tests for the grantee matcher."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st
import pytest

from bqemulator.row_access.identity import DEFAULT_CALLER, CallerIdentity
from bqemulator.row_access.matcher import GranteeMatcher, grantee_matches

pytestmark = pytest.mark.property

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Identity local-part: simple ASCII to keep matching tractable.
_LOCAL = st.from_regex(r"^[a-z]{1,12}$", fullmatch=True)
_HOST = st.from_regex(r"^[a-z]{1,8}\.com$", fullmatch=True)


def _email_strategy() -> st.SearchStrategy[str]:
    return st.tuples(_LOCAL, _HOST).map(lambda lh: f"{lh[0]}@{lh[1]}")


def _user_principal() -> st.SearchStrategy[str]:
    return _email_strategy().map(lambda e: f"user:{e}")


def _domain_grantee() -> st.SearchStrategy[str]:
    return _HOST.map(lambda h: f"domain:{h}")


def _grantee_strategy() -> st.SearchStrategy[str]:
    return st.one_of(
        _user_principal(),
        _domain_grantee(),
        st.just("allUsers"),
        st.just("allAuthenticatedUsers"),
    )


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


@given(principal=_user_principal())
def test_user_grantee_matches_iff_equal(principal: str) -> None:
    """A ``user:`` grantee matches the caller iff the principals agree."""
    caller = CallerIdentity(principal=principal, is_authenticated=True)
    assert grantee_matches(principal, caller)


@given(principal=_user_principal(), other=_user_principal())
def test_user_grantee_does_not_match_other(principal: str, other: str) -> None:
    if principal == other:
        return  # generator collision; nothing to assert
    caller = CallerIdentity(principal=principal, is_authenticated=True)
    assert not grantee_matches(other, caller)


@given(principal=_user_principal())
def test_domain_grantee_matches_principal_host(principal: str) -> None:
    caller = CallerIdentity(principal=principal, is_authenticated=True)
    host = principal.split("@")[1]
    assert grantee_matches(f"domain:{host}", caller)


@given(principal=_user_principal())
def test_all_users_always_matches(principal: str) -> None:
    caller = CallerIdentity(principal=principal, is_authenticated=True)
    assert grantee_matches("allUsers", caller)
    anon = CallerIdentity(principal=DEFAULT_CALLER, is_authenticated=False)
    assert grantee_matches("allUsers", anon)


@given(principal=_user_principal())
def test_all_authenticated_only_when_authenticated(principal: str) -> None:
    auth = CallerIdentity(principal=principal, is_authenticated=True)
    assert grantee_matches("allAuthenticatedUsers", auth)
    anon = CallerIdentity(principal=DEFAULT_CALLER, is_authenticated=False)
    assert not grantee_matches("allAuthenticatedUsers", anon)


@given(
    grantees=st.lists(_grantee_strategy(), min_size=0, max_size=8, unique=True),
    principal=_user_principal(),
)
def test_matcher_idempotent(grantees: list[str], principal: str) -> None:
    """Running matches_any twice returns the same boolean."""
    caller = CallerIdentity(principal=principal, is_authenticated=True)
    m = GranteeMatcher(caller)
    first = m.matches_any(grantees)
    second = m.matches_any(grantees)
    assert first is second


@given(
    grantees=st.lists(_grantee_strategy(), min_size=1, max_size=8, unique=True),
    principal=_user_principal(),
)
def test_matches_any_implies_at_least_one_match(
    grantees: list[str],
    principal: str,
) -> None:
    """If matches_any is True, at least one grantee individually matches."""
    caller = CallerIdentity(principal=principal, is_authenticated=True)
    m = GranteeMatcher(caller)
    if m.matches_any(grantees):
        assert any(grantee_matches(g, caller) for g in grantees)


@given(
    grantees=st.lists(_grantee_strategy(), min_size=1, max_size=8, unique=True),
    principal=_user_principal(),
)
def test_match_invariant_under_grantee_reorder(
    grantees: list[str],
    principal: str,
) -> None:
    """Reordering grantees doesn't change the match outcome."""
    caller = CallerIdentity(principal=principal, is_authenticated=True)
    m = GranteeMatcher(caller)
    assert m.matches_any(grantees) == m.matches_any(list(reversed(grantees)))
