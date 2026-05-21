"""Subprocess wrapper for Google's ``bq`` CLI bound to the bqemulator container.

The ``bq`` CLI ships as part of Google Cloud SDK and is the canonical
command-line surface for BigQuery — a distinct client shape from the
four official SDK clients (Python / Node / Go / Java) covered by the
sibling E2E suites. Real users (data engineers, DBAs, CI pipelines,
ad-hoc shell scripts) drive BigQuery through ``bq`` daily; a wire
regression that the SDK clients miss but ``bq`` exposes would ship
without this suite.

``BqRunner`` shells out to the operator's local ``bq`` binary against
a hermetic gcloud-config sandbox so the test never sees (or trips
over) the operator's real Google Cloud credentials. The sandbox
provides four things:

1. ``--api=<emulator_url>`` per invocation — overrides the BigQuery
   API endpoint. ``BIGQUERY_EMULATOR_HOST`` (honored by the SDK
   client libraries) is NOT honored by ``bq``; the only documented
   override paths are this flag, ``gcloud config set
   api_endpoint_overrides/bigquery``, or a ``~/.bigqueryrc`` ``api``
   line.
2. ``CLOUDSDK_CONFIG=<per-session tmp dir>`` — isolates the bq
   config (default ``~/.config/gcloud``) so concurrent test sessions
   don't trample each other and a crash mid-test cannot leave the
   operator's local bq pointed at a now-stopped emulator.
3. A pre-staged config inside that tmp dir: synthetic ``[core]``
   account + project (``configurations/config_default``), a sentinel
   ``active_config`` file selecting that profile, a synthetic OAuth
   refresh-token row in ``credentials.db`` (needed for the
   "active account has credentials" check), and a far-future-expiry
   access-token row in ``access_tokens.db`` (so ``gcloud config
   config-helper`` returns the cached token instead of attempting
   a network refresh that would 401 on the synthetic refresh-token).
4. ``--discovery_file=<cached-doc>`` — newer ``bq`` versions fetch
   the BigQuery v2 discovery document from the ``--api=`` host;
   the emulator does not serve ``$discovery/rest`` because it would
   require shipping a ~550 KB JSON mirror of the entire BQ API
   surface. The runner caches the real BigQuery discovery doc under
   the session tmp dir on first use (or reads a pre-staged copy
   from ``BQEMU_BQ_DISCOVERY_FILE``) and passes it to every ``bq``
   call.

Together these eliminate the local-env-specific failure mode where
the operator has a real ``~/.config/gcloud`` and ``bq`` refuses to
run with ``You do not currently have an active account selected``.
The pre-fix runner relied on ``CLOUDSDK_AUTH_DISABLE_CREDENTIALS=true``
alone; modern ``bq`` (gcloud SDK 2.x) ignores that env var for the
active-account check and still attempts the network refresh.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import urllib.request

import pytest

# Timeout for a single bq invocation. Streaming-insert + create-routine +
# load operations against the emulator are sub-second; 60s leaves headroom
# for the slowest legitimate path (CI bq install warming up its config
# directory on first invocation).
_DEFAULT_TIMEOUT_S = 60.0

#: Synthetic account email written to the config + credentials. Any
#: well-formed email works; this one is documented as the fixture's
#: anonymous identity.
_SANDBOX_ACCOUNT = "anonymous@bqemu.test"

#: Far-future expiry written to ``access_tokens.db``. Setting an
#: unexpired token causes ``gcloud config config-helper`` (which ``bq``
#: shells out to internally) to return the cached token instead of
#: trying to refresh against Google's OAuth endpoint.
_TOKEN_EXPIRY = "2099-12-31 00:00:00"  # noqa: S105 — literal date, not a credential

#: Real BigQuery discovery document URL. Fetched once per session
#: (or pre-staged) and cached under the runner's work dir.
_DISCOVERY_URL = "https://bigquery.googleapis.com/$discovery/rest?version=v2"


@dataclass(frozen=True)
class BqResult:
    """Captured output of a single ``bq`` subprocess invocation."""

    stdout: str
    stderr: str
    returncode: int

    def json(self) -> object:
        """Parse ``stdout`` as JSON.

        Raises:
            json.JSONDecodeError: if stdout is not valid JSON.
        """
        return json.loads(self.stdout)

    def succeeded(self) -> bool:
        """Return True if the subprocess exited 0."""
        return self.returncode == 0


def _stage_gcloud_sandbox(work_dir: Path, *, project_id: str) -> None:
    """Pre-populate ``work_dir`` so ``bq`` accepts it as ``CLOUDSDK_CONFIG``.

    Writes the four files gcloud needs to skip authentication entirely:

    * ``active_config`` — sentinel selecting the ``default`` profile.
      Must NOT have a trailing newline (gcloud reads the whole file
      as the profile name).
    * ``configurations/config_default`` — INI with the synthetic
      ``[core]`` account / project.
    * ``credentials.db`` — sqlite, one synthetic refresh-token row
      keyed by the account email.
    * ``access_tokens.db`` — sqlite, one synthetic access-token row
      keyed by ``sha256(account_email)`` (gcloud's storage key) AND
      one keyed by the plain account email (compatibility with older
      gcloud builds). Token expiry is far-future so no refresh fires.
    """
    (work_dir / "configurations").mkdir(parents=True, exist_ok=True)
    (work_dir / "configurations" / "config_default").write_text(
        f"[core]\n"
        f"account = {_SANDBOX_ACCOUNT}\n"
        f"project = {project_id}\n"
        f"disable_usage_reporting = True\n",
    )
    # No trailing newline; gcloud reads the whole file content as
    # the active-profile name.
    (work_dir / "active_config").write_text("default")

    credentials_db = work_dir / "credentials.db"
    # ``contextlib.closing`` is required because ``sqlite3.connect`` used
    # as a context manager only commits / rolls back — it does NOT close
    # the connection, and pytest 9's unraisable-exception collector
    # surfaces the GC-time finalize as a test failure.
    with contextlib.closing(sqlite3.connect(credentials_db)) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS credentials (account_id TEXT PRIMARY KEY, value BLOB)",
        )
        synthetic_cred = json.dumps(
            {
                "client_id": "anonymous",
                "client_secret": "anonymous",
                "refresh_token": "anonymous",
                "type": "authorized_user",
                "scopes": ["https://www.googleapis.com/auth/bigquery"],
            },
        )
        conn.execute(
            "INSERT OR REPLACE INTO credentials (account_id, value) VALUES (?, ?)",
            (_SANDBOX_ACCOUNT, synthetic_cred),
        )
        conn.commit()

    import hashlib

    access_tokens_db = work_dir / "access_tokens.db"
    with contextlib.closing(sqlite3.connect(access_tokens_db)) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS access_tokens "
            "(account_id TEXT PRIMARY KEY, access_token TEXT, "
            "token_expiry TIMESTAMP, rapt_token TEXT, id_token TEXT)",
        )
        account_hash = hashlib.sha256(_SANDBOX_ACCOUNT.encode("utf-8")).hexdigest()
        for key in (account_hash, _SANDBOX_ACCOUNT):
            conn.execute(
                "INSERT OR REPLACE INTO access_tokens "
                "(account_id, access_token, token_expiry, rapt_token, id_token) "
                "VALUES (?, ?, ?, ?, ?)",
                (key, "fake-anonymous-token", _TOKEN_EXPIRY, "", ""),
            )
        conn.commit()


def _resolve_discovery_file(work_dir: Path) -> Path:
    """Return a path to a BigQuery v2 discovery JSON file.

    Resolution order:

    1. ``BQEMU_BQ_DISCOVERY_FILE`` env var pointing at a pre-staged
       file (used by CI to avoid the network fetch in every run).
    2. ``<work_dir>/discovery.json`` if already cached from a prior
       call in this session.
    3. Download from real BigQuery and cache under work_dir.

    The fetched file is ~550 KB. Skipping the suite would be the
    alternative when offline; we choose the network fetch + cache
    because the fixture must be CI-reproducible and CI has network.
    """
    env_path = os.environ.get("BQEMU_BQ_DISCOVERY_FILE")
    if env_path:
        path = Path(env_path)
        if path.exists():
            return path

    cached = work_dir / "discovery.json"
    if cached.exists() and cached.stat().st_size > 1000:
        return cached

    try:
        with urllib.request.urlopen(_DISCOVERY_URL, timeout=30) as response:  # noqa: S310 — fixed https URL
            cached.write_bytes(response.read())
    except OSError as exc:  # pragma: no cover — network-failure escape
        msg = (
            f"Could not fetch BigQuery discovery doc from {_DISCOVERY_URL}: {exc}. "
            f"Set BQEMU_BQ_DISCOVERY_FILE to a pre-staged copy to skip this fetch."
        )
        pytest.skip(msg)

    return cached


class BqRunner:
    """Stateful wrapper that runs ``bq`` against the emulator endpoint.

    The runner is constructed once per pytest session (see
    :func:`bq_runner` fixture in ``conftest.py``) and re-used by every
    test. Each ``run(...)`` call spawns a fresh ``bq`` subprocess with
    the session-isolated env + per-call ``--api=`` override so a
    failing test cannot poison the next test's invocation.
    """

    def __init__(
        self,
        *,
        api_url: str,
        project_id: str,
        work_dir: Path,
    ) -> None:
        """Initialise the runner.

        Args:
            api_url: REST endpoint of the live emulator container
                (typically the ``bqemu_rest_url`` session fixture).
            project_id: BigQuery project id every invocation will
                carry via ``--project_id=``. Tests reuse a stable
                per-suite project id; per-test isolation is achieved
                via per-test dataset ids, mirroring the SDK suites.
            work_dir: Per-session temp directory used as
                ``CLOUDSDK_CONFIG``. The fixture creates this via
                ``tmp_path_factory.mktemp`` so the operator's real
                gcloud config is never touched.

        Skips the test session via ``pytest.skip`` if ``bq`` is not
        on ``PATH`` — the right ergonomic for developers without
        gcloud SDK installed locally. In CI, the workflow's
        google-cloud-cli install step fails fast before pytest runs,
        so the skip path is only ever taken on developer machines.
        """
        self.api_url = api_url
        self.project_id = project_id
        self.work_dir = work_dir
        bq_bin = shutil.which("bq")
        if bq_bin is None:
            pytest.skip("bq CLI not installed (install google-cloud-sdk)")
        self.bq_bin = bq_bin
        _stage_gcloud_sandbox(work_dir, project_id=project_id)
        self.discovery_file = _resolve_discovery_file(work_dir)

    def run(
        self,
        *args: str,
        input_bytes: bytes | None = None,
        timeout: float = _DEFAULT_TIMEOUT_S,
        extra_env: dict[str, str] | None = None,
    ) -> BqResult:
        """Run ``bq <args>`` with emulator-bound env and capture output.

        Args:
            *args: Arguments passed to ``bq`` after the global flags
                (``--api=``, ``--project_id=``, and ``--discovery_file=``).
            input_bytes: Optional stdin payload (used by ``bq insert``
                which reads NDJSON from stdin).
            timeout: Per-invocation timeout. Defaults to
                ``_DEFAULT_TIMEOUT_S``.
            extra_env: Optional environment overrides merged on top of
                the default subprocess env. Used by the
                ``.bigqueryrc`` test to set additional
                ``CLOUDSDK_*`` variables without polluting the parent
                process.

        Returns:
            ``BqResult`` capturing stdout / stderr / returncode.

        Raises:
            subprocess.TimeoutExpired: if the subprocess exceeds
                ``timeout``.
        """
        env = {
            **os.environ,
            # Per-session config dir so concurrent runs don't collide
            # on shared ~/.config/gcloud state.
            "CLOUDSDK_CONFIG": str(self.work_dir),
            # Anonymous calls — kept for backwards compat with older
            # gcloud SDK versions that honour this flag. Modern bq
            # (gcloud SDK 2.x) ignores it for the active-account
            # check; the credentials.db + access_tokens.db sandbox
            # is what actually unblocks the path.
            "CLOUDSDK_AUTH_DISABLE_CREDENTIALS": "true",
        }
        if extra_env:
            env.update(extra_env)
        cmd = [
            self.bq_bin,
            f"--api={self.api_url}",
            f"--project_id={self.project_id}",
            f"--discovery_file={self.discovery_file}",
            *args,
        ]
        proc = subprocess.run(  # noqa: S603 — list arg, no shell, validated env
            cmd,
            input=input_bytes,
            env=env,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return BqResult(
            stdout=proc.stdout.decode("utf-8", errors="replace"),
            stderr=proc.stderr.decode("utf-8", errors="replace"),
            returncode=proc.returncode,
        )

    def query_json(
        self,
        sql: str,
        *,
        use_legacy_sql: bool = False,
    ) -> list[dict[str, object]]:
        """Convenience: ``bq query --format=json`` returning parsed rows.

        ``bq query --format=json`` emits a JSON array of row objects;
        every cell value is rendered as a string (BigQuery's CLI JSON
        format encodes integers + floats + booleans + dates as
        strings — this matches real BigQuery's CLI output and is
        deliberate, so tests assert against stringified expected
        values).
        """
        legacy = "true" if use_legacy_sql else "false"
        result = self.run(
            "query",
            f"--use_legacy_sql={legacy}",
            "--format=json",
            sql,
        )
        if not result.succeeded():
            msg = (
                f"bq query failed (rc={result.returncode}):\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )
            raise RuntimeError(msg)
        rows = result.json()
        if not isinstance(rows, list):
            msg = f"bq query --format=json did not return a list: {rows!r}"
            raise TypeError(msg)
        return [row for row in rows if isinstance(row, dict)]
