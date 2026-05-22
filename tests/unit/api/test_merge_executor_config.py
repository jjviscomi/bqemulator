"""Pin ``_merge_executor_config`` in the POST /jobs response builder.

The executor enriches ``job_meta.configuration`` with a
``destinationTable`` (see ``_build_query_configuration`` in
``jobs/executor.py``). ``insert_job``'s response is rendered from
the *request*'s config, so without this overlay the executor's
addition is dropped on the synchronous response — dbt-bigquery's
post-execution ``client.get_table(query_job.destination)`` then
sees ``None`` and trips
``'NoneType' object has no attribute 'path'``.

The helper merges the executor's ``destinationTable`` into the
request config *without* overwriting caller-supplied keys
(``useLegacySql``, ``defaultDataset``, …).
"""

from __future__ import annotations

import pytest

from bqemulator.api.routes.jobs import _merge_executor_config

pytestmark = pytest.mark.unit


class TestMergeExecutorConfig:
    def test_executor_destination_table_propagates_into_request_config(self) -> None:
        request = {"query": {"query": "SELECT 1", "useLegacySql": False}}
        executor = {
            "query": {
                "query": "SELECT 1",
                "destinationTable": {
                    "projectId": "p",
                    "datasetId": "_bqemu_anonymous",
                    "tableId": "anon123",
                },
            }
        }
        _merge_executor_config(request, executor)
        assert request["query"]["destinationTable"] == {
            "projectId": "p",
            "datasetId": "_bqemu_anonymous",
            "tableId": "anon123",
        }
        # Caller-supplied key preserved.
        assert request["query"]["useLegacySql"] is False

    def test_does_not_overwrite_caller_destination_table(self) -> None:
        existing = {
            "projectId": "p",
            "datasetId": "explicit",
            "tableId": "user_choice",
        }
        request = {"query": {"query": "SELECT 1", "destinationTable": existing}}
        executor = {
            "query": {
                "destinationTable": {
                    "projectId": "p",
                    "datasetId": "_bqemu_anonymous",
                    "tableId": "anon123",
                }
            }
        }
        _merge_executor_config(request, executor)
        # Caller's value wins.
        assert request["query"]["destinationTable"] == existing

    def test_no_op_when_executor_config_missing(self) -> None:
        request = {"query": {"query": "SELECT 1"}}
        _merge_executor_config(request, None)
        assert "destinationTable" not in request["query"]

    def test_no_op_when_executor_config_lacks_destination(self) -> None:
        request = {"query": {"query": "SELECT 1"}}
        _merge_executor_config(request, {"query": {"query": "SELECT 1"}})
        assert "destinationTable" not in request["query"]

    def test_creates_query_subdict_when_request_lacks_one(self) -> None:
        request: dict = {}
        executor = {
            "query": {
                "destinationTable": {
                    "projectId": "p",
                    "datasetId": "_bqemu_anonymous",
                    "tableId": "anon456",
                }
            }
        }
        _merge_executor_config(request, executor)
        assert request["query"]["destinationTable"]["tableId"] == "anon456"
