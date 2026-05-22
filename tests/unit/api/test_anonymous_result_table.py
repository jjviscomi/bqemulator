"""Pin the ``GET /tables/_bqemu_anonymous/anon<job>`` synthesis path.

Anonymous query-result tables are not registered in the catalog (it
would pollute every listing endpoint); the tables route's GET
handler intercepts the reserved dataset and synthesises a response
from ``JOB_RESULTS`` / ``JOB_SCHEMAS`` so
``client.get_table(query_job.destination)`` returns a real
``num_rows`` + ``schema`` for ``SELECT``-style queries.
"""

from __future__ import annotations

import pyarrow as pa
import pytest

from bqemulator.api.routes.tables import _anonymous_result_table_to_rest
from bqemulator.jobs.executor import JOB_RESULTS, JOB_SCHEMAS

pytestmark = pytest.mark.unit


class TestAnonymousResultTableToRest:
    def test_with_known_job_returns_arrow_row_count(self) -> None:
        job_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        flat = job_id.replace("-", "")
        JOB_RESULTS[job_id] = pa.table({"n": pa.array([1, 2, 3])})
        JOB_SCHEMAS[job_id] = [{"name": "n", "type": "INTEGER", "mode": "NULLABLE"}]
        try:
            body = _anonymous_result_table_to_rest("proj", "_bqemu_anonymous", f"anon{flat}")
        finally:
            del JOB_RESULTS[job_id]
            del JOB_SCHEMAS[job_id]
        assert body["kind"] == "bigquery#table"
        assert body["tableReference"] == {
            "projectId": "proj",
            "datasetId": "_bqemu_anonymous",
            "tableId": f"anon{flat}",
        }
        assert body["numRows"] == "3"
        assert body["schema"]["fields"] == [{"name": "n", "type": "INTEGER", "mode": "NULLABLE"}]

    def test_unknown_job_returns_zero_rows_and_empty_schema(self) -> None:
        body = _anonymous_result_table_to_rest("proj", "_bqemu_anonymous", "anon" + "0" * 32)
        assert body["numRows"] == "0"
        assert body["schema"]["fields"] == []

    def test_non_uuid_table_id_uses_flat_form_as_job_id(self) -> None:
        # ``anon<arbitrary>`` (not a 32-hex UUID): hyphen-restitching is
        # skipped; the flat suffix becomes the literal lookup key.
        JOB_RESULTS["scripted-job-id"] = pa.table({"v": pa.array([42])})
        try:
            body = _anonymous_result_table_to_rest(
                "proj", "_bqemu_anonymous", "anonscripted-job-id"
            )
        finally:
            del JOB_RESULTS["scripted-job-id"]
        assert body["numRows"] == "1"
