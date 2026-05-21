"""Job state machine.

BigQuery jobs transition through:

    PENDING → RUNNING → DONE

Cancellation can happen from PENDING or RUNNING, always landing in DONE
with an error_result indicating cancellation.

This module enforces valid transitions and rejects illegal ones.
"""

from __future__ import annotations

from enum import StrEnum

from bqemulator.domain.errors import InternalError


class JobState(StrEnum):
    """Valid job states matching the BigQuery REST API."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"


class JobTransition(StrEnum):
    """Named transitions between job states."""

    START = "START"  # PENDING → RUNNING
    COMPLETE = "COMPLETE"  # RUNNING → DONE
    FAIL = "FAIL"  # RUNNING → DONE (with error)
    CANCEL = "CANCEL"  # PENDING|RUNNING → DONE (with cancellation error)
    # For synchronous jobs that skip RUNNING entirely:
    COMPLETE_SYNC = "COMPLETE_SYNC"  # PENDING → DONE


_VALID_TRANSITIONS: dict[tuple[JobState, JobTransition], JobState] = {
    (JobState.PENDING, JobTransition.START): JobState.RUNNING,
    (JobState.PENDING, JobTransition.CANCEL): JobState.DONE,
    (JobState.PENDING, JobTransition.COMPLETE_SYNC): JobState.DONE,
    (JobState.RUNNING, JobTransition.COMPLETE): JobState.DONE,
    (JobState.RUNNING, JobTransition.FAIL): JobState.DONE,
    (JobState.RUNNING, JobTransition.CANCEL): JobState.DONE,
}


def advance_job(current: JobState, transition: JobTransition) -> JobState:
    """Apply a transition to a job state.

    Returns:
        The new state.

    Raises:
        :class:`InternalError` if the transition is invalid.
    """
    key = (current, transition)
    new_state = _VALID_TRANSITIONS.get(key)
    if new_state is None:
        raise InternalError(
            f"Invalid job transition: {current.value} + {transition.value}",
        )
    return new_state


__all__ = ["JobState", "JobTransition", "advance_job"]
