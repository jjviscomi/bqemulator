"""Resumable-upload session manager — G2.

Tracks active resumable-upload sessions in memory. Each session
materialises its inbound bytes into a per-session temp file under
``Settings.upload_staging_dir`` (or the system tempdir when unset).
The manager enforces the configured size cap on every append and
evicts sessions older than ``Settings.upload_session_ttl_seconds``
lazily on the next call that touches it.

Session state is **process-local**. A restart drops every in-progress
upload — see ``docs/reference/out-of-scope.md`` under
"Durable upload session state".
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
import re
import tempfile
import threading
from typing import Any
from uuid import uuid4

from bqemulator.domain.clock import Clock
from bqemulator.domain.errors import InvalidQueryError, NotFoundError


class UploadSizeExceededError(InvalidQueryError):
    """The combined chunk bytes for a session exceed ``upload_max_bytes``."""

    def __init__(self, declared: int, cap: int) -> None:
        super().__init__(
            f"Upload size {declared} exceeds the configured cap of {cap} bytes",
        )
        self.declared = declared
        self.cap = cap


class ContentRangeError(InvalidQueryError):
    """The ``Content-Range`` header is malformed or inconsistent with state."""


# ``upload_id`` validator — the value flows into a filesystem path so we
# pin a strict character set. UUID hex output already matches this shape;
# rejecting anything wider closes the path-traversal blast radius.
_UPLOAD_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

# ``Content-Range: bytes <start>-<end>/<total or *>``. Also accepts the
# status-probe form ``bytes */<total>`` (used by clients to ask the
# server how much it received).
_CONTENT_RANGE_BYTES_RE = re.compile(
    r"^bytes\s+(?P<start>\d+)-(?P<end>\d+)/(?P<total>\d+|\*)$",
)
_CONTENT_RANGE_PROBE_RE = re.compile(r"^bytes\s+\*/(?P<total>\d+)$")


@dataclass(slots=True)
class UploadSession:
    """One resumable upload session.

    Attributes:
        session_id: opaque token returned in the initiation ``Location``
            header. The client echoes it back on every subsequent PUT
            either via ``upload_id=...`` query parameter or by hitting
            the same URI.
        project_id: project id from the initiation path. Used both for
            access logs and to scope the materialised load job.
        configuration: full ``configuration.load`` dict supplied at
            initiation. The chunk-upload handler hands this verbatim to
            :func:`execute_load_job` once the upload completes.
        staging_path: temp file under ``Settings.upload_staging_dir``
            that accumulates the inbound bytes.
        received_bytes: number of bytes appended to ``staging_path``
            so far. Tracked separately so a server can return
            ``308 Resume Incomplete`` with a correct ``Range`` header
            on a status probe.
        declared_total_bytes: when the client knows the total upload
            size up-front, it sets ``Content-Range: bytes
            <s>-<e>/<total>`` on its PUT. We store ``total`` here so
            the manager can detect completion (``received_bytes ==
            declared_total_bytes``). ``None`` when the client never
            declared a total (in which case ``finalize`` is called
            explicitly by the handler).
        finalized: ``True`` once the executor has consumed the
            staging file. A finalized session is removed from the
            manager on cleanup; until cleanup it remains queryable so
            a duplicate PUT can be NACK'd cleanly.
        created_at: monotonic-ish clock time of session creation. Used
            for TTL eviction.
        last_active_at: clock time of the most recent append. Reset on
            every successful PUT.
    """

    session_id: str
    project_id: str
    configuration: dict[str, Any]
    staging_path: Path
    received_bytes: int = 0
    declared_total_bytes: int | None = None
    finalized: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.fromtimestamp(0, tz=UTC))
    last_active_at: datetime = field(default_factory=lambda: datetime.fromtimestamp(0, tz=UTC))


class UploadSessionManager:
    """Thread-safe in-memory registry of active resumable upload sessions.

    The manager owns the staging directory's lifecycle: it creates
    sessions, materialises chunks, and unlinks the staging file when
    :meth:`remove` is called. Eviction is **lazy** — every call to
    :meth:`get` / :meth:`append` first prunes sessions older than the
    configured TTL.

    Thread safety: the manager guards its session map with a
    ``threading.Lock`` because the FastAPI request handlers may run on
    different worker threads under uvicorn's default setup. The lock is
    held only for the dict mutation; file I/O happens outside the lock
    so a slow disk on one session does not block lookups on others.
    """

    def __init__(
        self,
        *,
        staging_dir: Path | None,
        max_bytes: int,
        ttl_seconds: int,
        clock: Clock,
    ) -> None:
        self._max_bytes = max_bytes
        self._ttl = timedelta(seconds=ttl_seconds)
        self._clock = clock
        self._sessions: dict[str, UploadSession] = {}
        self._lock = threading.Lock()
        self._staging_dir = self._init_staging_dir(staging_dir)

    @staticmethod
    def _init_staging_dir(configured: Path | None) -> Path:
        """Resolve the staging directory, creating it if needed."""
        if configured is not None:
            configured.mkdir(parents=True, exist_ok=True)
            return configured
        # Fall back to a stable subdir of the system tempdir so multiple
        # emulator instances in CI don't collide. ``mkdtemp`` would
        # leak directories — we want one well-known dir whose contents
        # we manage.
        default = Path(tempfile.gettempdir()) / "bqemu_uploads"
        default.mkdir(parents=True, exist_ok=True)
        return default

    @property
    def max_bytes(self) -> int:
        """Configured per-session byte cap."""
        return self._max_bytes

    @property
    def staging_dir(self) -> Path:
        """The directory under which session staging files live."""
        return self._staging_dir

    def create(self, project_id: str, configuration: dict[str, Any]) -> UploadSession:
        """Mint a new session, allocating its staging file.

        The session id is a hex UUID — opaque to the client, but
        constrained to ``[A-Za-z0-9_-]{8,64}`` so the path component
        cannot escape the staging directory.
        """
        session_id = uuid4().hex
        staging_path = self._staging_dir / f"{session_id}.bin"
        # Touch the file so the OS owns it before the first chunk
        # arrives — protects against a race where two clients picked
        # the same uuid (impossible in practice, but cheap to guard).
        staging_path.touch(exist_ok=False)
        now = self._clock.now()
        session = UploadSession(
            session_id=session_id,
            project_id=project_id,
            configuration=configuration,
            staging_path=staging_path,
            created_at=now,
            last_active_at=now,
        )
        with self._lock:
            self._sessions[session_id] = session
        return session

    def get(self, session_id: str) -> UploadSession:
        """Look up a live session by id, raising ``NotFoundError`` if absent.

        Lazily evicts every session past its TTL before lookup so a
        long-idle session can't be revived from beyond the grave.
        """
        self._validate_session_id(session_id)
        self._evict_expired()
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise NotFoundError(f"Upload session {session_id} not found")
        return session

    def append(
        self,
        session_id: str,
        chunk: bytes,
        *,
        content_range: str | None = None,
    ) -> tuple[UploadSession, bool]:
        """Append a chunk to a session.

        ``content_range`` is the raw ``Content-Range`` header value
        when present. If supplied, it must match
        ``bytes <start>-<end>/<total|*>``; the manager validates that
        ``start`` equals the session's current ``received_bytes`` so
        out-of-order chunks fail loudly (BigQuery rejects them too).

        Returns ``(session, complete)`` where ``complete`` is ``True``
        when the session's ``received_bytes`` equals its declared total
        (or the client signalled completion via the byte range's
        upper bound matching ``total - 1``). The caller is expected to
        invoke :meth:`finalize` next when ``complete`` is ``True``.

        Raises :class:`UploadSizeExceededError` when the new total
        would exceed ``Settings.upload_max_bytes``.
        """
        session = self.get(session_id)
        start, end_inclusive, declared_total = self._extract_range_fields(content_range)
        self._verify_offset_matches(session, start)
        self._verify_size_caps(session, chunk, declared_total)
        self._reconcile_declared_total(session, declared_total)
        self._write_chunk(session, chunk)
        session.last_active_at = self._clock.now()
        complete = self._is_session_complete(session, end_inclusive, declared_total)
        return session, complete

    @staticmethod
    def _extract_range_fields(
        content_range: str | None,
    ) -> tuple[int | None, int | None, int | None]:
        """Parse ``content_range`` to ``(start, end_inclusive, total)`` or all-``None``."""
        if content_range is None:
            return None, None, None
        return UploadSessionManager._parse_content_range(content_range)

    @staticmethod
    def _verify_offset_matches(session: UploadSession, start: int | None) -> None:
        """Reject out-of-order chunks where ``start`` doesn't match the cursor."""
        if start is not None and start != session.received_bytes:
            raise ContentRangeError(
                f"Content-Range start={start} does not match the session's "
                f"current offset {session.received_bytes}",
            )

    def _verify_size_caps(
        self,
        session: UploadSession,
        chunk: bytes,
        declared_total: int | None,
    ) -> None:
        """Reject the append when chunk or declared total exceeds ``upload_max_bytes``."""
        prospective = session.received_bytes + len(chunk)
        if prospective > self._max_bytes:
            raise UploadSizeExceededError(declared=prospective, cap=self._max_bytes)
        if declared_total is not None and declared_total > self._max_bytes:
            raise UploadSizeExceededError(declared=declared_total, cap=self._max_bytes)

    @staticmethod
    def _reconcile_declared_total(
        session: UploadSession,
        declared_total: int | None,
    ) -> None:
        """Stash or cross-check a newly observed ``total`` against the session's running value."""
        if declared_total is None:
            return
        if session.declared_total_bytes is None:
            session.declared_total_bytes = declared_total
            return
        if session.declared_total_bytes != declared_total:
            raise ContentRangeError(
                f"Content-Range total={declared_total} disagrees with the "
                f"previously declared total {session.declared_total_bytes}",
            )

    @staticmethod
    def _write_chunk(session: UploadSession, chunk: bytes) -> None:
        """Append ``chunk`` to the staging file and advance the byte cursor."""
        if not chunk:
            return
        with session.staging_path.open("ab") as fh:
            fh.write(chunk)
        session.received_bytes += len(chunk)

    @staticmethod
    def _is_session_complete(
        session: UploadSession,
        end_inclusive: int | None,
        declared_total: int | None,
    ) -> bool:
        """Return True when the session has received its declared total.

        A chunk that spans the final byte (``end_inclusive == total - 1``)
        also indicates completion. If the client didn't supply
        Content-Range we can't infer completion from the chunk alone —
        the caller explicitly marks the session complete via
        :meth:`finalize`.
        """
        if session.declared_total_bytes is not None:
            return session.received_bytes >= session.declared_total_bytes
        if end_inclusive is not None and declared_total is not None:
            return end_inclusive + 1 == declared_total
        return False

    def status(self, session_id: str) -> UploadSession:
        """Return the current state of a session without mutating it.

        Used for ``Content-Range: bytes */<total>`` status probes. The
        client sends this when it lost track of the offset and wants
        the server to tell it how much was received.
        """
        return self.get(session_id)

    def finalize(self, session_id: str) -> UploadSession:
        """Mark a session finalized and return it.

        The handler calls this once the executor has consumed the
        staging file. The session stays in the map for one more get/
        append cycle so a duplicate PUT can be NACK'd cleanly with a
        ``finalized`` flag; :meth:`remove` is the final cleanup.
        """
        session = self.get(session_id)
        session.finalized = True
        session.last_active_at = self._clock.now()
        return session

    def remove(self, session_id: str) -> None:
        """Remove a session and unlink its staging file (idempotent)."""
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            return
        with suppress(FileNotFoundError):  # pragma: no cover — race-free in tests
            session.staging_path.unlink()

    def clear(self) -> None:
        """Remove every session. Used by integration tests and shutdown."""
        with self._lock:
            session_ids = list(self._sessions.keys())
        for session_id in session_ids:
            self.remove(session_id)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_session_id(session_id: str) -> None:
        if not _UPLOAD_ID_RE.match(session_id):
            raise NotFoundError(f"Upload session {session_id!r} not found")

    @staticmethod
    def _parse_content_range(value: str) -> tuple[int | None, int | None, int | None]:
        """Parse ``Content-Range`` returning ``(start, end_inclusive, total)``.

        Accepts the two BigQuery client variants:
        - ``bytes <s>-<e>/<total>`` — normal chunk
        - ``bytes <s>-<e>/*`` — chunk-of-unknown-total
        - ``bytes */<total>`` — status probe (no bytes attached)
        """
        probe = _CONTENT_RANGE_PROBE_RE.match(value.strip())
        if probe is not None:
            return None, None, int(probe.group("total"))
        match = _CONTENT_RANGE_BYTES_RE.match(value.strip())
        if match is None:
            raise ContentRangeError(f"Malformed Content-Range header: {value!r}")
        start = int(match.group("start"))
        end_inclusive = int(match.group("end"))
        total_raw = match.group("total")
        total = None if total_raw == "*" else int(total_raw)
        if end_inclusive < start:
            raise ContentRangeError(
                f"Content-Range end={end_inclusive} precedes start={start}",
            )
        if total is not None and end_inclusive >= total:
            raise ContentRangeError(
                f"Content-Range end={end_inclusive} not less than total={total}",
            )
        return start, end_inclusive, total

    def _evict_expired(self) -> None:
        """Drop sessions whose ``last_active_at`` is older than the TTL."""
        cutoff = self._clock.now() - self._ttl
        with self._lock:
            expired = [
                sid for sid, session in self._sessions.items() if session.last_active_at < cutoff
            ]
        for sid in expired:
            self.remove(sid)


__all__ = [
    "ContentRangeError",
    "UploadSession",
    "UploadSessionManager",
    "UploadSizeExceededError",
]
