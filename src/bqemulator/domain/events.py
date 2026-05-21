"""Internal domain events.

Events are emitted when observable state changes. They power:

* Materialized view auto-refresh (base-table DML invalidates cached MVs).
* Query result cache invalidation.
* Structured audit logging for debugging.

Events are consumed in-process via a simple synchronous bus. No persistent
event log; events never cross process boundaries.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar


@dataclass(slots=True, frozen=True)
class DomainEvent:
    """Base class for all domain events."""


@dataclass(slots=True, frozen=True)
class DatasetCreated(DomainEvent):
    """A dataset has been created."""

    project_id: str
    dataset_id: str


@dataclass(slots=True, frozen=True)
class DatasetDeleted(DomainEvent):
    """A dataset has been deleted."""

    project_id: str
    dataset_id: str


@dataclass(slots=True, frozen=True)
class TableCreated(DomainEvent):
    """A table has been created."""

    project_id: str
    dataset_id: str
    table_id: str


@dataclass(slots=True, frozen=True)
class TableSchemaChanged(DomainEvent):
    """A table's schema (columns, modes) has changed."""

    project_id: str
    dataset_id: str
    table_id: str


@dataclass(slots=True, frozen=True)
class TableDataChanged(DomainEvent):
    """Rows in a table have been inserted, updated, or deleted.

    Triggers query-cache invalidation for anything that depends on the table
    and materialized-view refresh for dependent MVs.
    """

    project_id: str
    dataset_id: str
    table_id: str


@dataclass(slots=True, frozen=True)
class TableDeleted(DomainEvent):
    """A table has been deleted."""

    project_id: str
    dataset_id: str
    table_id: str


@dataclass(slots=True, frozen=True)
class JobStarted(DomainEvent):
    """A job has transitioned from PENDING to RUNNING."""

    project_id: str
    job_id: str


@dataclass(slots=True, frozen=True)
class JobCompleted(DomainEvent):
    """A job has transitioned to DONE (success or failure)."""

    project_id: str
    job_id: str
    successful: bool


# ---------------------------------------------------------------------------
# In-process synchronous event bus
# ---------------------------------------------------------------------------

Handler = Callable[[DomainEvent], None]


class EventBus:
    """Synchronous, type-dispatched event bus.

    Intentionally minimal — no persistence, no async, no ordering guarantees
    beyond registration order. Adequate for in-process fan-out.

    Usage::

        bus = EventBus()
        bus.subscribe(TableDataChanged, invalidate_query_cache)
        bus.publish(TableDataChanged("proj", "sales", "orders"))
    """

    _handlers: dict[type[DomainEvent], list[Handler]]

    DEFAULT_BUS: ClassVar[EventBus | None] = None

    def __init__(self) -> None:
        self._handlers = {}

    def subscribe(self, event_type: type[DomainEvent], handler: Handler) -> None:
        """Register ``handler`` to be called for every event of ``event_type``."""
        self._handlers.setdefault(event_type, []).append(handler)

    def unsubscribe(self, event_type: type[DomainEvent], handler: Handler) -> None:
        """Remove a previously registered ``handler``. Silently skip if absent."""
        import contextlib

        handlers = self._handlers.get(event_type)
        if handlers is None:
            return
        with contextlib.suppress(ValueError):
            handlers.remove(handler)

    def publish(self, event: DomainEvent) -> None:
        """Invoke every handler registered for the event's type."""
        for handler in self._handlers.get(type(event), []):
            handler(event)


__all__ = [
    "DatasetCreated",
    "DatasetDeleted",
    "DomainEvent",
    "EventBus",
    "Handler",
    "JobCompleted",
    "JobStarted",
    "TableCreated",
    "TableDataChanged",
    "TableDeleted",
    "TableSchemaChanged",
]
