"""Conformance-tier shared fixtures.

The conformance runner is a parametrised pytest test (see
``test_corpus.py``) that discovers every fixture under
``sql_corpus/``, replays it against an in-process emulator, and diffs
the result against the recorded ``expected.json`` using the type-aware
comparison helper from ``_comparison.py``.

Inherited from this conftest:

* ``CONFORMANCE_SEED`` — fixed RNG seed (default ``0``). Affects only the
  order in which parametrised fixtures are iterated; rows themselves are
  fully determined by the recorded baselines. Override via
  ``BQEMU_CONFORMANCE_SEED`` when reproducing a flake.
* ``pytestmark = pytest.mark.conformance`` — applied to every test in
  this package via ``pytest_collection_modifyitems`` so the suite can
  be opted-in with ``pytest -m conformance``.
* ``conformance_dataset`` — a per-test fixture that creates a unique
  dataset on the emulator, yields its fully-qualified name, and drops
  it on teardown. Only fixtures with ``setup.sql`` consume this; the
  literal-only fixtures bypass dataset creation entirely.
"""

from __future__ import annotations

from collections.abc import Iterator
import os
from pathlib import Path
import random
import stat
from typing import TYPE_CHECKING
import uuid

import pytest

if TYPE_CHECKING:  # pragma: no cover
    from bqemulator.config import Settings
    from bqemulator.testing.fixtures import EmulatorEndpoint


_DEFAULT_CONFORMANCE_SEED = 0
CONFORMANCE_SEED = int(os.environ.get("BQEMU_CONFORMANCE_SEED", str(_DEFAULT_CONFORMANCE_SEED)))


def pytest_report_header(config: pytest.Config) -> str:
    """Emit the active conformance seed in the pytest session header.

    Mirrors the chaos tier convention so a CI flake is reproducible by
    copying the seed line into ``BQEMU_CONFORMANCE_SEED=<seed>``.
    """
    del config
    return f"conformance.seed={CONFORMANCE_SEED}"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Auto-apply the per-tier marker to every test in this package.

    Most tests get the ``conformance`` marker. The differential
    tier (``test_corpus_row_order_perturbed.py``, P8.f / ADR 0028)
    is deliberately marked ``differential`` instead so it ships as
    a separately-gated tier — invoked via ``make test-differential``
    or the ``differential.yml`` manual workflow, NOT via the per-PR
    conformance gate.
    """
    del config
    for item in items:
        path = str(item.fspath).replace("\\", "/")
        if "tests/conformance/" not in path:
            continue
        if "test_corpus_row_order_perturbed.py" in path:
            item.add_marker(pytest.mark.differential)
            continue
        item.add_marker(pytest.mark.conformance)


@pytest.fixture(autouse=True)
def _conformance_deterministic_seed() -> None:
    """Reset every conformance test to the same RNG state.

    The conformance tier does not generate random inputs, but a stable
    RNG state guards against any future helper that might iterate the
    corpus in random order or sample a sub-set.
    """
    random.seed(CONFORMANCE_SEED)


@pytest.fixture
def conformance_dataset(bqemu_endpoint: EmulatorEndpoint) -> Iterator[str]:
    """Create a unique dataset on the emulator and drop it on teardown.

    Yields the fully-qualified ``project.dataset`` name suitable for
    ``${DATASET}`` placeholder substitution in fixture SQL files. The
    dataset is auto-dropped with ``deleteContents=True`` after each
    test so successive parametrised runs do not collide.
    """
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import bigquery

    project = bqemu_endpoint.project_id
    dataset_name = f"bqemu_conformance_{uuid.uuid4().hex[:12]}"
    fqdn = f"{project}.{dataset_name}"

    client = bigquery.Client(
        project=project,
        credentials=AnonymousCredentials(),  # type: ignore[no-untyped-call]
        client_options=ClientOptions(api_endpoint=bqemu_endpoint.rest_url),
    )
    client.create_dataset(bigquery.Dataset(fqdn))
    try:
        yield fqdn
    finally:
        client.delete_dataset(fqdn, delete_contents=True, not_found_ok=True)


# ---------------------------------------------------------------------------
# G1 (load/extract Avro/ORC) — local GCS-root staging
# ---------------------------------------------------------------------------

#: Bucket name used inside the recorded fixture URLs. Must match the value
#: ``BQEMU_CONFORMANCE_GCS_BUCKET`` was set to at recording time so the
#: ``gs://`` URLs the emulator receives are byte-identical to the ones in
#: the recorded baseline (the comparator does an exact-string match on
#: ``sourceUris`` / ``destinationUris``).
_G1_RECORDED_BUCKET = "test-webhook-bucket/bqemu-conformance"


@pytest.fixture(scope="session")
def bqemu_settings(tmp_path_factory: pytest.TempPathFactory) -> Settings:
    """Override the session-scoped ``bqemu_settings`` from the testing plugin.

    Sets ``gcs_local_root`` so the G1 conformance fixtures (load/extract
    Avro/ORC) can resolve their ``gs://`` URLs to local files staged by
    :mod:`scripts.stage_g1_e2e_fixtures`. Without this override the
    emulator raises ``Cannot resolve gs:// URIs without
    BQEMU_GCS_LOCAL_ROOT configured`` on every G1 fixture replay.
    """
    from bqemulator.config import PersistenceMode, Settings

    gcs_root = tmp_path_factory.mktemp("bqemu_conformance_gcs", numbered=False)
    gcs_root.chmod(0o777)
    _stage_g1_fixture_bytes(gcs_root)

    return Settings(
        persistence_mode=PersistenceMode.EPHEMERAL,
        rest_host="127.0.0.1",
        rest_port=0,
        grpc_host="127.0.0.1",
        grpc_port=0,
        gcs_local_root=gcs_root,
    )


def _stage_g1_fixture_bytes(gcs_root: Path) -> None:
    """Stage the same Avro/ORC bytes the recorder uploaded to GCS.

    Files are written under ``<gcs_root>/<bucket>/<path-from-URL>`` so
    the emulator's ``_resolve_uri`` resolves the recorded ``gs://``
    URLs to the right local file. The bucket path matches the recorder's
    ``BQEMU_CONFORMANCE_GCS_BUCKET=test-webhook-bucket/bqemu-conformance``
    value — see [`_G1_RECORDED_BUCKET`](tests/conformance/conftest.py).
    """
    import importlib.util

    here = Path(__file__).resolve().parent.parent.parent  # repo root
    script_path = here / "scripts" / "stage_g1_e2e_fixtures.py"
    spec = importlib.util.spec_from_file_location("stage_g1_e2e_fixtures", script_path)
    if spec is None or spec.loader is None:  # pragma: no cover — wiring
        msg = f"failed to load {script_path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # The staging script writes into ``<root>/g1-e2e/``; the recorded
    # fixtures reference ``gs://test-webhook-bucket/bqemu-conformance/g1/``.
    # Bridge by writing into the matching subpath.
    target = gcs_root / _G1_RECORDED_BUCKET / "g1"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.parent.chmod(0o777)

    from decimal import Decimal
    import io

    import fastavro
    import pyorc

    target.mkdir(parents=True, exist_ok=True)
    target.chmod(0o777)

    # Mirror scripts/stage_g1_e2e_fixtures.py BUT with the additional
    # fixtures the recorder needed (nested_record, logical_decimal,
    # nested ORC, invalid). Centralising the byte definitions in
    # scripts/stage_g1_e2e_fixtures.py would be cleaner; that's a v1.0.x
    # follow-up tracked in the G1 ADR.
    def write(name: str, payload: bytes) -> None:
        path = target / name
        path.write_bytes(payload)
        path.chmod(0o644 | stat.S_IROTH)

    # load_avro_basic
    schema = fastavro.parse_schema(
        {
            "type": "record",
            "name": "Item",
            "fields": [
                {"name": "id", "type": "long"},
                {"name": "name", "type": ["null", "string"], "default": None},
            ],
        },
    )
    buf = io.BytesIO()
    fastavro.writer(
        buf,
        schema,
        [
            {"id": 1, "name": "alpha"},
            {"id": 2, "name": "beta"},
            {"id": 3, "name": "gamma"},
        ],
    )
    write("load_avro_basic.avro", buf.getvalue())

    # load_avro_nested_record
    schema = fastavro.parse_schema(
        {
            "type": "record",
            "name": "Person",
            "fields": [
                {"name": "id", "type": "long"},
                {"name": "name", "type": "string"},
                {
                    "name": "addr",
                    "type": {
                        "type": "record",
                        "name": "Address",
                        "fields": [
                            {"name": "city", "type": "string"},
                            {"name": "zip", "type": "string"},
                        ],
                    },
                },
            ],
        },
    )
    buf = io.BytesIO()
    fastavro.writer(
        buf,
        schema,
        [
            {"id": 1, "name": "Ada", "addr": {"city": "London", "zip": "NW1"}},
            {"id": 2, "name": "Linus", "addr": {"city": "Helsinki", "zip": "00100"}},
        ],
    )
    write("load_avro_nested_record.avro", buf.getvalue())

    # load_avro_logical_decimal
    schema = fastavro.parse_schema(
        {
            "type": "record",
            "name": "Amount",
            "fields": [
                {"name": "id", "type": "long"},
                {
                    "name": "value",
                    "type": {
                        "type": "bytes",
                        "logicalType": "decimal",
                        "precision": 38,
                        "scale": 9,
                    },
                },
            ],
        },
    )
    buf = io.BytesIO()
    fastavro.writer(
        buf,
        schema,
        [
            {"id": 1, "value": Decimal("123.456789000")},
            {"id": 2, "value": Decimal("-0.000000001")},
        ],
    )
    write("load_avro_logical_decimal.avro", buf.getvalue())

    # load_orc_basic
    buf = io.BytesIO()
    w = pyorc.Writer(buf, "struct<id:bigint,name:string>")
    for r in [(1, "alpha"), (2, "beta"), (3, "gamma")]:
        w.write(r)
    w.close()
    write("load_orc_basic.orc", buf.getvalue())

    # load_orc_nested
    buf = io.BytesIO()
    w = pyorc.Writer(buf, "struct<id:bigint,name:string,addr:struct<city:string,zip:string>>")
    w.write((1, "Ada", ("London", "NW1")))
    w.write((2, "Linus", ("Helsinki", "00100")))
    w.close()
    write("load_orc_nested.orc", buf.getvalue())

    # load_avro_invalid_file (intentionally NOT avro)
    write("load_avro_invalid_file.txt", b"this is plain text, not avro\n")
