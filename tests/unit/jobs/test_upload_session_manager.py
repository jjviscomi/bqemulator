"""Unit tests for :mod:`bqemulator.jobs.upload_session_manager` (G2)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from bqemulator.domain.clock import FrozenClock
from bqemulator.domain.errors import NotFoundError
from bqemulator.jobs.upload_session_manager import (
    ContentRangeError,
    UploadSessionManager,
    UploadSizeExceededError,
)

pytestmark = pytest.mark.unit


def _manager(
    tmp_path: Path,
    *,
    max_bytes: int = 1024 * 1024,
    ttl_seconds: int = 3600,
    clock: FrozenClock | None = None,
) -> UploadSessionManager:
    """Build a manager with a per-test staging directory."""
    return UploadSessionManager(
        staging_dir=tmp_path / "uploads",
        max_bytes=max_bytes,
        ttl_seconds=ttl_seconds,
        clock=clock or FrozenClock(datetime(2026, 5, 20, tzinfo=UTC)),
    )


def _load_config() -> dict[str, object]:
    """Minimal valid load configuration for session creation."""
    return {
        "load": {
            "destinationTable": {
                "projectId": "p",
                "datasetId": "d",
                "tableId": "t",
            },
            "sourceFormat": "CSV",
            "writeDisposition": "WRITE_APPEND",
        }
    }


class TestCreate:
    def test_create_returns_session_with_staging_file(self, tmp_path: Path) -> None:
        manager = _manager(tmp_path)
        session = manager.create("proj-1", _load_config())
        assert session.session_id
        assert session.project_id == "proj-1"
        assert session.staging_path.parent == tmp_path / "uploads"
        assert session.staging_path.is_file()
        assert session.received_bytes == 0
        assert session.declared_total_bytes is None
        assert not session.finalized

    def test_create_in_default_tempdir_when_none(self, tmp_path: Path) -> None:  # noqa: ARG002 — tmp_path fixture isolates other tests
        # Pass None for staging_dir; manager falls back to system tempdir.
        manager = UploadSessionManager(
            staging_dir=None,
            max_bytes=1024,
            ttl_seconds=60,
            clock=FrozenClock(datetime(2026, 5, 20, tzinfo=UTC)),
        )
        assert manager.staging_dir.is_dir()
        # Don't pollute the tempdir on success — clean up the session.
        session = manager.create("proj", _load_config())
        manager.remove(session.session_id)


class TestGet:
    def test_get_returns_session(self, tmp_path: Path) -> None:
        manager = _manager(tmp_path)
        session = manager.create("p", _load_config())
        retrieved = manager.get(session.session_id)
        assert retrieved is session

    def test_get_unknown_session_id_raises_not_found(self, tmp_path: Path) -> None:
        manager = _manager(tmp_path)
        with pytest.raises(NotFoundError):
            manager.get("a" * 32)

    def test_get_with_path_traversal_id_raises_not_found(self, tmp_path: Path) -> None:
        manager = _manager(tmp_path)
        with pytest.raises(NotFoundError):
            manager.get("../etc/passwd")

    def test_get_with_too_short_id_raises_not_found(self, tmp_path: Path) -> None:
        manager = _manager(tmp_path)
        with pytest.raises(NotFoundError):
            manager.get("abc")

    def test_get_with_too_long_id_raises_not_found(self, tmp_path: Path) -> None:
        manager = _manager(tmp_path)
        with pytest.raises(NotFoundError):
            manager.get("a" * 65)


class TestAppend:
    def test_single_chunk_no_range(self, tmp_path: Path) -> None:
        manager = _manager(tmp_path)
        session = manager.create("p", _load_config())
        result, complete = manager.append(session.session_id, b"hello")
        assert result.received_bytes == 5
        assert complete is False  # no Content-Range = explicit finalize required

    def test_two_chunks_with_content_range(self, tmp_path: Path) -> None:
        manager = _manager(tmp_path)
        session = manager.create("p", _load_config())
        _, complete = manager.append(
            session.session_id,
            b"hello",
            content_range="bytes 0-4/10",
        )
        assert complete is False
        result, complete = manager.append(
            session.session_id,
            b"world",
            content_range="bytes 5-9/10",
        )
        assert result.received_bytes == 10
        assert complete is True

    def test_chunk_exceeding_size_cap_raises(self, tmp_path: Path) -> None:
        manager = _manager(tmp_path, max_bytes=4)
        session = manager.create("p", _load_config())
        with pytest.raises(UploadSizeExceededError) as exc:
            manager.append(session.session_id, b"hello")
        assert exc.value.cap == 4

    def test_declared_total_exceeding_cap_raises(self, tmp_path: Path) -> None:
        manager = _manager(tmp_path, max_bytes=10)
        session = manager.create("p", _load_config())
        with pytest.raises(UploadSizeExceededError):
            manager.append(
                session.session_id,
                b"hi",
                content_range="bytes 0-1/9999",
            )

    def test_out_of_order_chunk_raises(self, tmp_path: Path) -> None:
        manager = _manager(tmp_path)
        session = manager.create("p", _load_config())
        manager.append(session.session_id, b"a", content_range="bytes 0-0/3")
        with pytest.raises(ContentRangeError):
            manager.append(session.session_id, b"c", content_range="bytes 2-2/3")

    def test_total_disagrees_with_previous_total_raises(self, tmp_path: Path) -> None:
        manager = _manager(tmp_path)
        session = manager.create("p", _load_config())
        manager.append(session.session_id, b"a", content_range="bytes 0-0/10")
        with pytest.raises(ContentRangeError):
            manager.append(session.session_id, b"b", content_range="bytes 1-1/99")

    def test_malformed_content_range_raises(self, tmp_path: Path) -> None:
        manager = _manager(tmp_path)
        session = manager.create("p", _load_config())
        with pytest.raises(ContentRangeError):
            manager.append(session.session_id, b"a", content_range="bytes weird")

    def test_end_before_start_raises(self, tmp_path: Path) -> None:
        manager = _manager(tmp_path)
        session = manager.create("p", _load_config())
        with pytest.raises(ContentRangeError):
            manager.append(
                session.session_id,
                b"abc",
                content_range="bytes 5-2/10",
            )

    def test_end_at_or_past_total_raises(self, tmp_path: Path) -> None:
        manager = _manager(tmp_path)
        session = manager.create("p", _load_config())
        with pytest.raises(ContentRangeError):
            manager.append(
                session.session_id,
                b"abc",
                content_range="bytes 0-10/10",
            )

    def test_unknown_total_chunk_form(self, tmp_path: Path) -> None:
        manager = _manager(tmp_path)
        session = manager.create("p", _load_config())
        result, complete = manager.append(
            session.session_id,
            b"abc",
            content_range="bytes 0-2/*",
        )
        assert result.received_bytes == 3
        assert complete is False  # total unknown

    def test_empty_chunk_no_op(self, tmp_path: Path) -> None:
        manager = _manager(tmp_path)
        session = manager.create("p", _load_config())
        result, complete = manager.append(session.session_id, b"")
        assert result.received_bytes == 0
        assert complete is False

    def test_writes_to_disk_in_append_mode(self, tmp_path: Path) -> None:
        manager = _manager(tmp_path)
        session = manager.create("p", _load_config())
        manager.append(session.session_id, b"hello", content_range="bytes 0-4/10")
        manager.append(session.session_id, b"world", content_range="bytes 5-9/10")
        assert session.staging_path.read_bytes() == b"helloworld"


class TestStatusProbe:
    def test_status_probe_via_content_range(self, tmp_path: Path) -> None:
        manager = _manager(tmp_path)
        session = manager.create("p", _load_config())
        manager.append(session.session_id, b"hello", content_range="bytes 0-4/10")
        probed = manager.status(session.session_id)
        assert probed.received_bytes == 5


class TestFinalizeAndRemove:
    def test_finalize_marks_session(self, tmp_path: Path) -> None:
        manager = _manager(tmp_path)
        session = manager.create("p", _load_config())
        result = manager.finalize(session.session_id)
        assert result.finalized is True

    def test_remove_unlinks_staging_file(self, tmp_path: Path) -> None:
        manager = _manager(tmp_path)
        session = manager.create("p", _load_config())
        staging_path = session.staging_path
        assert staging_path.exists()
        manager.remove(session.session_id)
        assert not staging_path.exists()
        with pytest.raises(NotFoundError):
            manager.get(session.session_id)

    def test_remove_unknown_session_is_idempotent(self, tmp_path: Path) -> None:
        manager = _manager(tmp_path)
        # Pre-conditions on a path-traversal-shaped id are bypassed for
        # remove() since the value never lands in a lookup operation; the
        # session map simply doesn't contain it.
        manager.remove("a" * 32)  # no exception

    def test_remove_missing_staging_file_is_tolerated(self, tmp_path: Path) -> None:
        manager = _manager(tmp_path)
        session = manager.create("p", _load_config())
        # Simulate a teardown race by unlinking before remove().
        session.staging_path.unlink()
        manager.remove(session.session_id)

    def test_clear_removes_every_session(self, tmp_path: Path) -> None:
        manager = _manager(tmp_path)
        s1 = manager.create("p", _load_config())
        s2 = manager.create("p", _load_config())
        manager.clear()
        with pytest.raises(NotFoundError):
            manager.get(s1.session_id)
        with pytest.raises(NotFoundError):
            manager.get(s2.session_id)


class TestEviction:
    def test_expired_sessions_evicted_on_get(self, tmp_path: Path) -> None:
        clock = FrozenClock(datetime(2026, 5, 20, tzinfo=UTC))
        manager = _manager(tmp_path, ttl_seconds=60, clock=clock)
        session = manager.create("p", _load_config())
        staging_path = session.staging_path
        # Advance the clock past the TTL.
        clock.advance(seconds=120)
        with pytest.raises(NotFoundError):
            manager.get(session.session_id)
        assert not staging_path.exists()

    def test_active_session_survives_eviction_sweep(self, tmp_path: Path) -> None:
        clock = FrozenClock(datetime(2026, 5, 20, tzinfo=UTC))
        manager = _manager(tmp_path, ttl_seconds=60, clock=clock)
        session = manager.create("p", _load_config())
        clock.advance(seconds=30)
        # An append refreshes ``last_active_at``.
        manager.append(session.session_id, b"x")
        clock.advance(seconds=45)  # < ttl since append
        assert manager.get(session.session_id) is session


class TestSizeCapAttributes:
    def test_max_bytes_exposed(self, tmp_path: Path) -> None:
        manager = _manager(tmp_path, max_bytes=42)
        assert manager.max_bytes == 42

    def test_staging_dir_exposed(self, tmp_path: Path) -> None:
        manager = _manager(tmp_path)
        assert manager.staging_dir == tmp_path / "uploads"
        assert manager.staging_dir.is_dir()
