"""Unit tests for the wildcard table expander."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bqemulator.catalog.memory_repository import MemoryCatalogRepository
from bqemulator.catalog.models import DatasetMeta, TableMeta
from bqemulator.sql.rewriter.wildcard_expander import expand_wildcard_tables

pytestmark = pytest.mark.unit

NOW = datetime(2026, 4, 15, tzinfo=UTC)


def _setup_catalog(
    tables: list[str],
    project: str = "p",
    dataset: str = "ds",
) -> MemoryCatalogRepository:
    catalog = MemoryCatalogRepository()
    catalog.create_dataset(
        DatasetMeta(
            project_id=project,
            dataset_id=dataset,
            creation_time=NOW,
            last_modified_time=NOW,
            etag="e",
        ),
    )
    for table_name in tables:
        catalog.create_table(
            TableMeta(
                project_id=project,
                dataset_id=dataset,
                table_id=table_name,
                creation_time=NOW,
                last_modified_time=NOW,
                etag="e",
            ),
        )
    return catalog


class TestWildcardExpansion:
    def test_expands_matching_tables(self) -> None:
        catalog = _setup_catalog(["events_20260101", "events_20260201", "other"])
        result = expand_wildcard_tables(
            "SELECT * FROM ds.events_*",
            "p",
            catalog,
        )
        assert "UNION ALL" in result
        assert "events_20260101" in result
        assert "events_20260201" in result
        assert "other" not in result
        assert "_TABLE_SUFFIX" in result

    def test_suffix_values_correct(self) -> None:
        catalog = _setup_catalog(["tbl_a", "tbl_b"])
        result = expand_wildcard_tables(
            "SELECT * FROM ds.tbl_*",
            "p",
            catalog,
        )
        assert "'a' AS _TABLE_SUFFIX" in result
        assert "'b' AS _TABLE_SUFFIX" in result

    def test_no_wildcard_returns_unchanged(self) -> None:
        catalog = _setup_catalog([])
        sql = "SELECT * FROM ds.regular_table"
        assert expand_wildcard_tables(sql, "p", catalog) == sql

    def test_no_matching_tables_returns_unchanged(self) -> None:
        catalog = _setup_catalog(["other_table"])
        sql = "SELECT * FROM ds.events_*"
        result = expand_wildcard_tables(sql, "p", catalog)
        # No tables match events_* prefix, so SQL unchanged.
        assert result == sql

    def test_no_dataset_qualifier_returns_unchanged(self) -> None:
        catalog = _setup_catalog([])
        sql = "SELECT * FROM events_*"
        result = expand_wildcard_tables(sql, "p", catalog)
        assert result == sql

    def test_preserves_where_clause(self) -> None:
        catalog = _setup_catalog(["log_a", "log_b"])
        # Phase 6: _TABLE_SUFFIX predicate pushdown narrows the match set
        # at plan time. With a single '=' predicate we end up with just
        # the one matching table (no UNION ALL needed) but the predicate
        # is preserved on the outer query.
        sql = "SELECT x FROM ds.log_* WHERE _TABLE_SUFFIX = 'a'"
        result = expand_wildcard_tables(sql, "p", catalog)
        assert "log_a" in result
        assert "log_b" not in result
        assert "WHERE _TABLE_SUFFIX = 'a'" in result

    def test_preserves_where_clause_multi_match(self) -> None:
        # Multiple tables match → UNION ALL emitted.
        catalog = _setup_catalog(["log_a", "log_b", "log_c"])
        sql = "SELECT x FROM ds.log_* WHERE _TABLE_SUFFIX IN ('a', 'b')"
        result = expand_wildcard_tables(sql, "p", catalog)
        assert "UNION ALL" in result
        assert "log_a" in result
        assert "log_b" in result
        assert "log_c" not in result

    def test_join_with_wildcard(self) -> None:
        catalog = _setup_catalog(["ev_1", "ev_2"])
        sql = "SELECT * FROM ds.other JOIN ds.ev_* ON true"
        result = expand_wildcard_tables(sql, "p", catalog)
        assert "UNION ALL" in result
        assert "__wildcard" in result

    def test_fully_qualified_three_part_backticked(self) -> None:
        """Bucket C closure: ``project.dataset.events_*`` must expand.

        The conformance corpus uses ``${DATASET}.events_*`` where
        ``${DATASET}`` expands to ``<project>.<dataset>``, so the
        rewriter must engage on a fully-qualified, backtick-wrapped
        3-part reference. Pre-closure the regex predicate only handled
        the trailing 2-part shape and left the table reference
        verbatim, which DuckDB then rejected with
        ``Catalog Error: Table with name events_* does not exist``.
        """
        catalog = _setup_catalog(
            ["events_20240101", "events_20240102", "other"],
            project="p",
            dataset="ds",
        )
        sql = "SELECT _TABLE_SUFFIX AS suffix, id FROM `p.ds.events_*` ORDER BY suffix"
        result = expand_wildcard_tables(sql, "p", catalog)

        # Every matching table is expanded, none is left as the
        # original wildcard literal.
        assert "events_*" not in result
        assert "UNION ALL" in result
        assert "p.ds.events_20240101" in result
        assert "p.ds.events_20240102" in result
        assert "other" not in result

        # _TABLE_SUFFIX literals are correctly populated from the
        # leaf identifier — without the date prefix included.
        assert "'20240101' AS _TABLE_SUFFIX" in result
        assert "'20240102' AS _TABLE_SUFFIX" in result

    def test_fully_qualified_three_part_suffix_pushdown_runs_first(
        self,
    ) -> None:
        """Suffix-equality pushdown applies even on 3-part qualified refs.

        ``_TABLE_SUFFIX = '20240102'`` should prune the UNION before
        it is materialised. The closure must keep this behaviour
        intact — pruning is the whole point of Phase 6's pushdown.
        """
        catalog = _setup_catalog(
            ["events_20240101", "events_20240102", "events_20240103"],
            project="p",
            dataset="ds",
        )
        sql = "SELECT id FROM `p.ds.events_*` WHERE _TABLE_SUFFIX = '20240102' ORDER BY id"
        result = expand_wildcard_tables(sql, "p", catalog)

        # Only the one matching table appears in the expanded subquery
        # — pushdown removed the other two from the UNION before
        # materialisation.
        assert "p.ds.events_20240102" in result
        assert "events_20240101" not in result
        assert "events_20240103" not in result

    def test_three_part_qualified_uses_sql_project_not_arg(self) -> None:
        """The project segment in the SQL wins over the caller's project_id.

        BigQuery semantics: a 3-part reference identifies an absolute
        dataset, independent of the session's default project. The
        rewriter must look up tables under the SQL-supplied project
        so cross-project queries work the same way they do against
        real BigQuery.
        """
        catalog = _setup_catalog(
            ["events_20240101"],
            project="other_project",
            dataset="ds",
        )
        # Caller's default project is "p"; the SQL names "other_project".
        sql = "SELECT * FROM `other_project.ds.events_*`"
        result = expand_wildcard_tables(sql, "p", catalog)
        assert "other_project.ds.events_20240101" in result
        assert "UNION ALL" not in result  # only one match

    def test_self_join_expands_both_wildcards(self) -> None:
        """Bucket C closure: self-joins on a wildcard expand both refs.

        Pre-closure ``re.search`` only expanded the first wildcard in
        a query; the second stayed as ``events_*`` and DuckDB raised
        a catalog error. The closure switches to ``re.sub`` so every
        reference gets its own UNION ALL — and preserves any
        explicit ``AS <alias>`` so we don't double-alias the
        synthetic subquery.
        """
        catalog = _setup_catalog(
            ["events_20240101", "events_20240102"],
            project="p",
            dataset="ds",
        )
        sql = (
            "SELECT a._TABLE_SUFFIX, b._TABLE_SUFFIX "
            "FROM `p.ds.events_*` AS a "
            "JOIN `p.ds.events_*` AS b "
            "ON a._TABLE_SUFFIX < b._TABLE_SUFFIX"
        )
        result = expand_wildcard_tables(sql, "p", catalog)

        # Two independent expansions — count `UNION ALL` occurrences.
        assert result.count("UNION ALL") == 2

        # Original ``AS a`` and ``AS b`` aliases preserved verbatim,
        # the synthetic ``__wildcard`` is NOT emitted when the author
        # already supplied an alias.
        assert " AS a " in result or result.endswith(" AS a")
        assert " AS b " in result or result.endswith(" AS b")
        assert "__wildcard" not in result


class TestPartitionMetadataInRest:
    """Verify partitioning/clustering serializes to REST correctly."""

    def test_partitioning_serialization(self) -> None:
        from bqemulator.api.routes.tables import _table_to_rest
        from bqemulator.catalog.models import TimePartitioning

        table = TableMeta(
            project_id="p",
            dataset_id="ds",
            table_id="t",
            time_partitioning=TimePartitioning(
                type="DAY",
                field="event_date",
                expiration_ms=86400000,
                require_partition_filter=True,
            ),
            creation_time=NOW,
            last_modified_time=NOW,
            etag="e",
        )
        rest = _table_to_rest(table)
        tp = rest["timePartitioning"]
        assert tp["type"] == "DAY"
        assert tp["field"] == "event_date"
        assert tp["expirationMs"] == "86400000"
        assert tp["requirePartitionFilter"] is True

    def test_clustering_serialization(self) -> None:
        from bqemulator.api.routes.tables import _table_to_rest
        from bqemulator.catalog.models import Clustering

        table = TableMeta(
            project_id="p",
            dataset_id="ds",
            table_id="t",
            clustering=Clustering(fields=("level", "region")),
            creation_time=NOW,
            last_modified_time=NOW,
            etag="e",
        )
        rest = _table_to_rest(table)
        assert rest["clustering"]["fields"] == ["level", "region"]

    def test_no_partitioning_omits_field(self) -> None:
        from bqemulator.api.routes.tables import _table_to_rest

        table = TableMeta(
            project_id="p",
            dataset_id="ds",
            table_id="t",
            creation_time=NOW,
            last_modified_time=NOW,
            etag="e",
        )
        rest = _table_to_rest(table)
        assert "timePartitioning" not in rest
        assert "clustering" not in rest

    def test_partitioning_without_optional_fields(self) -> None:
        from bqemulator.api.routes.tables import _table_to_rest
        from bqemulator.catalog.models import TimePartitioning

        table = TableMeta(
            project_id="p",
            dataset_id="ds",
            table_id="t",
            time_partitioning=TimePartitioning(type="DAY"),
            creation_time=NOW,
            last_modified_time=NOW,
            etag="e",
        )
        rest = _table_to_rest(table)
        tp = rest["timePartitioning"]
        assert tp["type"] == "DAY"
        assert "field" not in tp
        assert "expirationMs" not in tp
        assert "requirePartitionFilter" not in tp
