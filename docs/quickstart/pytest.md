# pytest quickstart

`bqemulator` ships a pytest plugin. After `pip install bqemulator`, the
`bqemu_server`, `bqemu_endpoint`, and `bqemu_client` fixtures are available
automatically — no `conftest.py` wiring required.

## Minimal example

```python
# test_orders.py
from google.cloud import bigquery

def test_create_dataset(bqemu_client):
    dataset = bqemu_client.create_dataset("sales")
    assert dataset.dataset_id == "sales"
```

```bash
pytest test_orders.py
```

## Configuring the fixture

Override `bqemu_settings` to change defaults — persistent mode, logging,
etc.:

```python
# conftest.py
import pytest
from bqemulator.config import PersistenceMode, Settings

@pytest.fixture(scope="session")
def bqemu_settings(tmp_path_factory):
    return Settings(
        persistence_mode=PersistenceMode.PERSISTENT,
        data_dir=tmp_path_factory.mktemp("bqemu"),
        rest_port=0,
        grpc_port=0,
    )
```

## With environment variables

The fixture exports `BIGQUERY_EMULATOR_HOST` automatically, so client code
that reads the env var needs no special handling.

## Fixture reference

| Fixture | Scope | Returns |
|---|---|---|
| `bqemu_settings` | session | [`Settings`](../reference/configuration.md) |
| `bqemu_server` | session | running `EmulatorServer` |
| `bqemu_endpoint` | session | `EmulatorEndpoint(rest_url, grpc_endpoint, project_id)` |
| `bqemu_client` | function | `google.cloud.bigquery.Client` pointed at the emulator |

## Parallelism

`pytest-xdist` is supported. Each worker gets its own session-scoped
emulator on a distinct free port.
