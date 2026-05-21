"""Tests for the domain event bus."""

from __future__ import annotations

import pytest

from bqemulator.domain.events import (
    DatasetCreated,
    DomainEvent,
    EventBus,
    JobCompleted,
    TableDataChanged,
)

pytestmark = pytest.mark.unit


class TestEventBus:
    def test_subscribe_and_publish_dispatches_to_handler(self) -> None:
        bus = EventBus()
        calls: list[DomainEvent] = []
        bus.subscribe(TableDataChanged, calls.append)

        event = TableDataChanged("p", "sales", "orders")
        bus.publish(event)

        assert calls == [event]

    def test_multiple_handlers_all_called(self) -> None:
        bus = EventBus()
        calls: list[str] = []
        bus.subscribe(DatasetCreated, lambda _e: calls.append("a"))
        bus.subscribe(DatasetCreated, lambda _e: calls.append("b"))
        bus.publish(DatasetCreated("p", "sales"))
        assert calls == ["a", "b"]

    def test_handler_only_fires_for_its_type(self) -> None:
        bus = EventBus()
        fired: list[str] = []
        bus.subscribe(DatasetCreated, lambda _e: fired.append("dataset"))
        bus.subscribe(TableDataChanged, lambda _e: fired.append("table"))

        bus.publish(DatasetCreated("p", "sales"))
        assert fired == ["dataset"]

        bus.publish(TableDataChanged("p", "sales", "orders"))
        assert fired == ["dataset", "table"]

    def test_publish_without_subscribers_is_noop(self) -> None:
        bus = EventBus()
        bus.publish(JobCompleted("p", "j-1", successful=True))  # no raise

    def test_unsubscribe_removes_handler(self) -> None:
        bus = EventBus()
        calls: list[DomainEvent] = []

        def handler(event: DomainEvent) -> None:
            calls.append(event)

        bus.subscribe(TableDataChanged, handler)
        bus.unsubscribe(TableDataChanged, handler)
        bus.publish(TableDataChanged("p", "s", "t"))
        assert calls == []

    def test_unsubscribe_unknown_handler_is_noop(self) -> None:
        bus = EventBus()
        bus.unsubscribe(TableDataChanged, lambda _e: None)  # must not raise


class TestDomainEventImmutability:
    def test_events_are_frozen(self) -> None:
        event = DatasetCreated(project_id="p", dataset_id="s")
        with pytest.raises((AttributeError, Exception)):
            event.project_id = "other"  # type: ignore[misc]

    def test_events_are_hashable(self) -> None:
        a = DatasetCreated("p", "s")
        b = DatasetCreated("p", "s")
        assert hash(a) == hash(b)
        assert {a, b} == {a}
