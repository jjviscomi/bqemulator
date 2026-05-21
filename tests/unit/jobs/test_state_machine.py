"""Tests for the job state machine."""

from __future__ import annotations

import pytest

from bqemulator.domain.errors import InternalError
from bqemulator.jobs.state_machine import JobState, JobTransition, advance_job

pytestmark = pytest.mark.unit


class TestValidTransitions:
    def test_pending_to_running(self) -> None:
        assert advance_job(JobState.PENDING, JobTransition.START) == JobState.RUNNING

    def test_running_to_done(self) -> None:
        assert advance_job(JobState.RUNNING, JobTransition.COMPLETE) == JobState.DONE

    def test_running_to_done_on_failure(self) -> None:
        assert advance_job(JobState.RUNNING, JobTransition.FAIL) == JobState.DONE

    def test_pending_cancel(self) -> None:
        assert advance_job(JobState.PENDING, JobTransition.CANCEL) == JobState.DONE

    def test_running_cancel(self) -> None:
        assert advance_job(JobState.RUNNING, JobTransition.CANCEL) == JobState.DONE

    def test_pending_complete_sync(self) -> None:
        assert advance_job(JobState.PENDING, JobTransition.COMPLETE_SYNC) == JobState.DONE


class TestInvalidTransitions:
    def test_done_cannot_start(self) -> None:
        with pytest.raises(InternalError, match="Invalid job transition"):
            advance_job(JobState.DONE, JobTransition.START)

    def test_done_cannot_complete(self) -> None:
        with pytest.raises(InternalError):
            advance_job(JobState.DONE, JobTransition.COMPLETE)

    def test_pending_cannot_complete(self) -> None:
        with pytest.raises(InternalError):
            advance_job(JobState.PENDING, JobTransition.COMPLETE)

    def test_pending_cannot_fail(self) -> None:
        with pytest.raises(InternalError):
            advance_job(JobState.PENDING, JobTransition.FAIL)


class TestFullLifecycle:
    def test_sync_lifecycle(self) -> None:
        """PENDING → DONE (synchronous query)."""
        state = JobState.PENDING
        state = advance_job(state, JobTransition.COMPLETE_SYNC)
        assert state == JobState.DONE

    def test_async_lifecycle(self) -> None:
        """PENDING → RUNNING → DONE."""
        state = JobState.PENDING
        state = advance_job(state, JobTransition.START)
        assert state == JobState.RUNNING
        state = advance_job(state, JobTransition.COMPLETE)
        assert state == JobState.DONE

    def test_cancelled_lifecycle(self) -> None:
        """PENDING → RUNNING → DONE (cancelled)."""
        state = JobState.PENDING
        state = advance_job(state, JobTransition.START)
        state = advance_job(state, JobTransition.CANCEL)
        assert state == JobState.DONE
