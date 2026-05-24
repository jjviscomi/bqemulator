"""Unit tests for the ``SESSION_USER()`` pre-translator (ADR 0038).

Two surfaces pinned:

1. :func:`resolve_session_user` — the pure prefix-stripping helper
   that maps a :class:`CallerIdentity` to the literal string
   ``SESSION_USER()`` should return.
2. :func:`rewrite_session_user` — the SQLGlot AST walk that finds
   every ``SessionUser`` call site and replaces it with a string
   literal. Idempotent, parse-failure-tolerant, and a string-side
   fast-reject when no occurrences exist.
"""

from __future__ import annotations

import pytest

from bqemulator.row_access.identity import DEFAULT_CALLER, CallerIdentity
from bqemulator.sql.rewriter.session_user import (
    ANONYMOUS_CALLER,
    resolve_session_user,
    rewrite_session_user,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# resolve_session_user — every IAM-member shape
# ---------------------------------------------------------------------------


class TestResolveSessionUser:
    """Prefix-stripping per IAM-member kind matches BigQuery's contract."""

    def test_user_principal_strips_prefix(self) -> None:
        caller = CallerIdentity(principal="user:alice@example.com")
        assert resolve_session_user(caller) == "alice@example.com"

    def test_service_account_strips_prefix(self) -> None:
        caller = CallerIdentity(
            principal="serviceAccount:sa@svc.iam.gserviceaccount.com",
        )
        assert resolve_session_user(caller) == "sa@svc.iam.gserviceaccount.com"

    def test_group_strips_prefix(self) -> None:
        # ``group:`` callers never appear via the X-Bqemu-Caller header
        # in practice (groups are grantee-side identifiers), but the
        # rewriter is tolerant so a misconfigured emulator doesn't
        # raise. Strip the prefix to keep the function consistent.
        caller = CallerIdentity(principal="group:admins@example.com")
        assert resolve_session_user(caller) == "admins@example.com"

    def test_domain_strips_prefix(self) -> None:
        caller = CallerIdentity(principal="domain:example.com")
        assert resolve_session_user(caller) == "example.com"

    def test_all_users_passes_through(self) -> None:
        # Not a real caller — ``allUsers`` is a grantee-side
        # identifier. The function returns the raw string so a
        # misconfigured emulator doesn't raise; the value is documented
        # in the ADR as not BigQuery-faithful for this case.
        caller = CallerIdentity(principal="allUsers")
        assert resolve_session_user(caller) == "allUsers"

    def test_all_authenticated_users_passes_through(self) -> None:
        caller = CallerIdentity(principal="allAuthenticatedUsers")
        assert resolve_session_user(caller) == "allAuthenticatedUsers"

    def test_unauthenticated_fallback_returns_anonymous_sentinel(self) -> None:
        # The emulator's default caller (no ``X-Bqemu-Caller`` header)
        # has ``is_authenticated=False``. Real BigQuery never reaches
        # this branch — every API call is authenticated — but the
        # emulator routes queries without identity to the
        # :data:`ANONYMOUS_CALLER` literal so RAP policies that
        # compare ``SESSION_USER()`` against a tenant key deny every
        # row instead of leaking via a stale principal.
        caller = CallerIdentity(principal=DEFAULT_CALLER, is_authenticated=False)
        assert resolve_session_user(caller) == ANONYMOUS_CALLER

    def test_authenticated_anonymous_principal_strips_prefix(self) -> None:
        # If someone manually constructs an authenticated caller that
        # happens to use the ``DEFAULT_CALLER`` principal string, we
        # treat it as authenticated and strip the prefix. The
        # ``is_authenticated`` flag is the source of truth.
        caller = CallerIdentity(principal=DEFAULT_CALLER, is_authenticated=True)
        assert resolve_session_user(caller) == "anonymous@bqemulator.local"


# ---------------------------------------------------------------------------
# rewrite_session_user — SQL-level substitution
# ---------------------------------------------------------------------------


class TestRewriteSessionUser:
    """``rewrite_session_user`` substitutes every call site with a literal."""

    def _caller(self, email: str = "alice@example.com") -> CallerIdentity:
        return CallerIdentity(principal=f"user:{email}")

    def test_bare_select_session_user(self) -> None:
        sql = "SELECT SESSION_USER() AS who"
        out = rewrite_session_user(sql, self._caller("alice@example.com"))
        assert "SESSION_USER" not in out.upper()
        assert "'alice@example.com'" in out

    def test_lower_case_session_user(self) -> None:
        # BigQuery accepts ``session_user()`` (lower-case). The fast-
        # path string check is case-insensitive so this still bumps.
        sql = "SELECT session_user() AS who"
        out = rewrite_session_user(sql, self._caller("alice@example.com"))
        # Conjunctive assertion (CodeRabbit thread PRRT_kwDOSkfuJ86EVwPG):
        # both invariants must hold or the regression check is too weak.
        assert "session_user" not in out.lower()
        assert "'alice@example.com'" in out

    def test_no_session_user_call_is_fast_path_no_op(self) -> None:
        sql = "SELECT 1 + 2 AS x FROM t WHERE y > 3"
        out = rewrite_session_user(sql, self._caller())
        # Returns the same string object identity — the fast-path
        # check short-circuited before parsing.
        assert out is sql

    def test_session_user_inside_regexp_extract(self) -> None:
        # The canonical RAP-filter pattern.
        sql = "SELECT * FROM t WHERE REGEXP_EXTRACT(SESSION_USER(), r'@(.+)$') = 'example.com'"
        out = rewrite_session_user(sql, self._caller("bob@example.com"))
        assert "SESSION_USER" not in out.upper()
        assert "'bob@example.com'" in out

    def test_multiple_call_sites_all_substituted(self) -> None:
        sql = (
            "SELECT SESSION_USER() AS a, "
            "CONCAT('hi ', SESSION_USER()) AS b "
            "FROM t WHERE SESSION_USER() = 'me'"
        )
        out = rewrite_session_user(sql, self._caller("c@example.com"))
        # Three replacements; none of the ``SessionUser`` AST nodes
        # should survive.
        assert "SESSION_USER" not in out.upper()
        assert out.lower().count("c@example.com") == 3

    def test_idempotent_second_pass_is_noop(self) -> None:
        sql = "SELECT SESSION_USER() AS who"
        caller = self._caller("a@example.com")
        once = rewrite_session_user(sql, caller)
        twice = rewrite_session_user(once, caller)
        # Second pass sees no ``SESSION_USER`` in the SQL — the
        # fast-path returns the same object identity.
        assert twice is once

    def test_unparseable_sql_returns_input_unchanged(self) -> None:
        # The rewriter must not swallow parse errors — the downstream
        # SQLGlot transpile is the right layer to surface them.
        sql = "SELECT SESSION_USER() FROM (((unbalanced"
        caller = self._caller()
        out = rewrite_session_user(sql, caller)
        assert out == sql

    def test_unauthenticated_caller_substitutes_anonymous_literal(self) -> None:
        sql = "SELECT SESSION_USER() AS who"
        caller = CallerIdentity(principal=DEFAULT_CALLER, is_authenticated=False)
        out = rewrite_session_user(sql, caller)
        assert "'anonymous'" in out

    def test_service_account_caller_substitutes_full_email(self) -> None:
        sql = "SELECT SESSION_USER() AS who"
        caller = CallerIdentity(
            principal="serviceAccount:job@p.iam.gserviceaccount.com",
        )
        out = rewrite_session_user(sql, caller)
        assert "'job@p.iam.gserviceaccount.com'" in out

    def test_session_user_in_string_literal_not_rewritten(self) -> None:
        # ``"SESSION_USER"`` inside a string literal is not a function
        # call — SQLGlot's AST doesn't model it as a ``SessionUser``
        # node, so the rewriter leaves it alone.
        sql = "SELECT 'SESSION_USER()' AS literal"
        out = rewrite_session_user(sql, self._caller())
        assert "'SESSION_USER()'" in out

    def test_session_user_in_view_query_body_substituted(self) -> None:
        # The CREATE VIEW body itself isn't a call context, but its
        # SELECT is. The pre-translator rewrites the AST walk through
        # the SELECT.
        sql = "CREATE VIEW v AS SELECT SESSION_USER() AS who"
        out = rewrite_session_user(sql, self._caller("v@example.com"))
        assert "SESSION_USER" not in out.upper()
        assert "'v@example.com'" in out

    # ------------------------------------------------------------------
    # CURRENT_USER() — function alias (ADR 0040)
    # ------------------------------------------------------------------

    def test_bare_select_current_user(self) -> None:
        sql = "SELECT CURRENT_USER() AS who"
        out = rewrite_session_user(sql, self._caller("alice@example.com"))
        assert "CURRENT_USER" not in out.upper()
        assert "'alice@example.com'" in out

    def test_lower_case_current_user(self) -> None:
        sql = "SELECT current_user() AS who"
        out = rewrite_session_user(sql, self._caller("alice@example.com"))
        assert "current_user" not in out.lower()
        assert "'alice@example.com'" in out

    def test_current_user_unauthenticated_caller(self) -> None:
        sql = "SELECT CURRENT_USER() AS who"
        caller = CallerIdentity(principal=DEFAULT_CALLER, is_authenticated=False)
        out = rewrite_session_user(sql, caller)
        assert "'anonymous'" in out

    def test_current_user_inside_regexp_extract(self) -> None:
        # Same RAP-filter pattern that exercises SESSION_USER, on the
        # CURRENT_USER alias. Both spellings produce the same plan.
        sql = "SELECT * FROM t WHERE REGEXP_EXTRACT(CURRENT_USER(), r'@(.+)$') = 'example.com'"
        out = rewrite_session_user(sql, self._caller("bob@example.com"))
        assert "CURRENT_USER" not in out.upper()
        assert "'bob@example.com'" in out

    # ------------------------------------------------------------------
    # @@session.user — system-variable spelling (ADR 0040)
    # ------------------------------------------------------------------

    def test_bare_select_session_user_system_var(self) -> None:
        sql = "SELECT @@session.user AS who"
        out = rewrite_session_user(sql, self._caller("alice@example.com"))
        # The rewritten output drops the ``@@session.user`` token
        # entirely — it's been replaced with the literal.
        assert "@@session" not in out.lower()
        assert "'alice@example.com'" in out

    def test_session_user_system_var_unauthenticated(self) -> None:
        sql = "SELECT @@session.user AS who"
        caller = CallerIdentity(principal=DEFAULT_CALLER, is_authenticated=False)
        out = rewrite_session_user(sql, caller)
        assert "'anonymous'" in out

    def test_user_column_not_falsely_matched(self) -> None:
        # ``SELECT user FROM users`` references a column named
        # ``user`` — it is NOT the ``@@session.user`` system variable
        # and the rewriter must leave it alone. Pins the precision
        # of ``_is_session_user_system_var`` against false positives.
        sql = "SELECT user FROM users"
        out = rewrite_session_user(sql, self._caller())
        assert out is sql  # fast-path short-circuit

    # ------------------------------------------------------------------
    # All three spellings in one query — all rewritten
    # ------------------------------------------------------------------

    def test_all_three_spellings_in_one_query(self) -> None:
        sql = "SELECT SESSION_USER() AS a, CURRENT_USER() AS b, @@session.user AS c"
        out = rewrite_session_user(sql, self._caller("multi@example.com"))
        # Every spelling resolved to the same literal — three
        # occurrences in the output.
        assert "SESSION_USER" not in out.upper()
        assert "CURRENT_USER" not in out.upper()
        assert "@@session" not in out.lower()
        assert out.lower().count("multi@example.com") == 3


# ---------------------------------------------------------------------------
# Translator integration — through the full pipeline
# ---------------------------------------------------------------------------


class TestTranslatorIntegration:
    """``SQLTranslator.translate`` runs the rewriter as a pre-translator pass.

    These tests verify the wiring; the rewriter itself is fully unit-
    tested in :class:`TestRewriteSessionUser` above. The integration
    tests stay focused on the contract: when ``caller`` is supplied,
    every ``SESSION_USER()`` call site lands as the resolved email
    literal in the DuckDB output.
    """

    def test_translator_substitutes_session_user(self) -> None:
        from bqemulator.domain.result import Ok
        from bqemulator.sql.translator import SQLTranslator

        translator = SQLTranslator()
        caller = CallerIdentity(principal="user:alice@example.com")
        result = translator.translate(
            "SELECT SESSION_USER() AS who",
            caller=caller,
        )
        assert isinstance(result, Ok)
        assert "'alice@example.com'" in result.value
        # DuckDB's native ``SESSION_USER`` resolves to ``'duckdb'`` —
        # if the rewriter ever stops running, the output below would
        # carry the bare ``SESSION_USER`` identifier and DuckDB would
        # return ``'duckdb'``. Pin against that regression.
        assert "SESSION_USER" not in result.value.upper()

    def test_translator_without_caller_uses_anonymous_fallback(self) -> None:
        from bqemulator.domain.result import Ok
        from bqemulator.sql.translator import SQLTranslator

        translator = SQLTranslator()
        result = translator.translate("SELECT SESSION_USER() AS who")
        assert isinstance(result, Ok)
        assert "'anonymous'" in result.value
