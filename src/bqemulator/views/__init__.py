"""Authorized-view helpers used by the row-access rewriter.

A *view* in BigQuery is a stored SELECT statement; an *authorized
view* is a view whose containing dataset has been listed in the base
table's dataset's ``access`` array. When an authorized view reads a
protected base table, the read happens *as the view*, not as the
caller, so the caller-bound row access policy is bypassed for that
read. Per-view policies (those whose target IS the view's data) still
apply normally.

This package owns the bypass detection used by
:mod:`bqemulator.sql.rewriter.row_access_filter`. It does not own SQL
rewriting itself — that lives in the rewriter so the bypass and the
per-table policy lookup share a single catalog read.
"""

from __future__ import annotations

from bqemulator.views.authorized_views import (
    AuthorizedViewManager,
    is_view_authorized_on,
)

__all__ = [
    "AuthorizedViewManager",
    "is_view_authorized_on",
]
