"""Structured logging via structlog.

Configures JSON output in production, pretty console output in dev. Injects
a correlation id into every log line via contextvars so a single request's
traces can be reconstructed across async handlers.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from contextvars import ContextVar
import logging
from typing import Any

import structlog

from bqemulator.config import LogFormat, LogLevel

_correlation_id_var: ContextVar[str | None] = ContextVar("bqemu_correlation_id", default=None)


def _correlation_processor(
    _logger: Any,
    _method: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    cid = _correlation_id_var.get()
    if cid is not None:
        event_dict["correlation_id"] = cid
    return event_dict


def configure_logging(*, level: LogLevel, fmt: LogFormat) -> None:
    """Configure structlog and the stdlib logging root.

    Safe to call multiple times; the second call reconfigures.
    """
    py_level = _to_python_level(level)

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _correlation_processor,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer: structlog.types.Processor
    if fmt is LogFormat.JSON:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(py_level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Align stdlib logging with structlog.
    logging.basicConfig(level=py_level, format="%(message)s", force=True)
    logging.getLogger("uvicorn.access").setLevel(py_level)
    logging.getLogger("uvicorn.error").setLevel(py_level)


def _to_python_level(level: LogLevel) -> int:
    mapping = {
        LogLevel.TRACE: logging.DEBUG,
        LogLevel.DEBUG: logging.DEBUG,
        LogLevel.INFO: logging.INFO,
        LogLevel.WARNING: logging.WARNING,
        LogLevel.ERROR: logging.ERROR,
        LogLevel.CRITICAL: logging.CRITICAL,
    }
    return mapping[level]


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a structlog bound logger for the given module/component name."""
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger


def bind_correlation_id(correlation_id: str) -> None:
    """Bind the correlation id for the current async context.

    Call this from request middleware. The id appears on every subsequent
    log line emitted from within this task.
    """
    _correlation_id_var.set(correlation_id)


def clear_correlation_id() -> None:
    """Clear the correlation id from the current async context."""
    _correlation_id_var.set(None)


__all__ = [
    "bind_correlation_id",
    "clear_correlation_id",
    "configure_logging",
    "get_logger",
]
