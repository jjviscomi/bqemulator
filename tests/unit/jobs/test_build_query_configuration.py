"""Pin the executor's ``_build_query_configuration`` helper.

Covers both branches:

* ``CREATE [OR REPLACE] TABLE`` outputs → ``destinationTable``
  is the real target.
* Everything else (``SELECT``, ``INSERT``, ``UPDATE``, …) →
  ``destinationTable`` is a synthetic ``_bqemu_anonymous`` ref so
  dbt-bigquery's ``client.get_table(query_job.destination)`` has a
  non-``None`` ref to follow.
"""

from __future__ import annotations

import pytest

from bqemulator.jobs.executor import (
    _ANONYMOUS_RESULTS_DATASET,
    _build_query_configuration,
)

pytestmark = pytest.mark.unit


class TestBuildQueryConfiguration:
    def test_ctas_sets_real_destination_table(self) -> None:
        config = _build_query_configuration(
            "CREATE OR REPLACE TABLE `proj`.`ds`.`t` AS SELECT 1 AS n",
            project_id="proj",
            job_id="abc123",
        )
        assert config["query"]["destinationTable"] == {
            "projectId": "proj",
            "datasetId": "ds",
            "tableId": "t",
        }

    def test_plain_create_table_sets_real_destination_table(self) -> None:
        config = _build_query_configuration(
            "CREATE TABLE `proj`.`ds`.`t2` (id INT64)",
            project_id="proj",
            job_id="abc123",
        )
        assert config["query"]["destinationTable"] == {
            "projectId": "proj",
            "datasetId": "ds",
            "tableId": "t2",
        }

    def test_select_falls_through_to_anonymous_destination(self) -> None:
        config = _build_query_configuration(
            "SELECT 1 AS n",
            project_id="proj",
            job_id="11111111-2222-3333-4444-555555555555",
        )
        # Anonymous dataset; hyphens stripped from the job-id.
        assert config["query"]["destinationTable"] == {
            "projectId": "proj",
            "datasetId": _ANONYMOUS_RESULTS_DATASET,
            "tableId": "anon11111111222233334444555555555555",
        }

    def test_insert_falls_through_to_anonymous_destination(self) -> None:
        config = _build_query_configuration(
            "INSERT INTO `proj`.`ds`.`t` VALUES (1)",
            project_id="proj",
            job_id="job-1",
        )
        assert config["query"]["destinationTable"]["datasetId"] == (_ANONYMOUS_RESULTS_DATASET)
        assert config["query"]["destinationTable"]["tableId"] == "anonjob1"

    def test_query_text_is_preserved_verbatim(self) -> None:
        sql = "SELECT 1 AS n FROM `proj`.`ds`.`t`"
        config = _build_query_configuration(sql, project_id="proj", job_id="j")
        assert config["query"]["query"] == sql

    def test_create_table_without_target_falls_through(self) -> None:
        # Unparseable / non-canonical input: detector returns ``None``,
        # the function still emits a synthetic anonymous destination.
        config = _build_query_configuration(
            "-- not a real CTAS\nSELECT 1",
            project_id="proj",
            job_id="abc",
        )
        assert config["query"]["destinationTable"]["datasetId"] == (_ANONYMOUS_RESULTS_DATASET)
