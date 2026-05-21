"""Integration test: full dataset CRUD via the google-cloud-bigquery client.

This exercises the emulator in-process via the pytest fixture and
drives it with the real ``google.cloud.bigquery.Client``.
"""

from __future__ import annotations

import pytest

from bqemulator.server import EmulatorServer

pytestmark = pytest.mark.integration


def test_create_get_patch_delete_dataset(bqemu_server: EmulatorServer) -> None:
    """Ship-criterion workflow for datasets."""
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery

    client = bigquery.Client(
        project="test-project",
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=bqemu_server.rest_url),
    )

    # Create
    ds = client.create_dataset("integration_ds")
    assert ds.dataset_id == "integration_ds"

    # Get
    fetched = client.get_dataset("integration_ds")
    assert fetched.dataset_id == "integration_ds"

    # Patch (update description)
    fetched.description = "updated description"
    patched = client.update_dataset(fetched, ["description"])
    assert patched.description == "updated description"

    # List
    datasets = list(client.list_datasets())
    assert any(d.dataset_id == "integration_ds" for d in datasets)

    # Delete
    client.delete_dataset("integration_ds")
    datasets_after = list(client.list_datasets())
    assert all(d.dataset_id != "integration_ds" for d in datasets_after)
