"""Job lifecycle management.

Jobs transition through a state machine (PENDING → RUNNING → DONE) and
are dispatched via the command pattern. Each job type (query, load,
extract, copy) is a command that the executor runs asynchronously.
"""

from __future__ import annotations

from bqemulator.jobs.state_machine import JobState, JobTransition, advance_job

__all__ = ["JobState", "JobTransition", "advance_job"]
