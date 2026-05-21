"""Domain layer — framework-free core.

Modules in this package MUST NOT import from :mod:`bqemulator.api`,
:mod:`bqemulator.grpc_api`, or any web/RPC framework. The domain defines
pure types, errors, and protocols that adapters depend on.
"""

from __future__ import annotations

from bqemulator.domain.clock import Clock, FrozenClock, SystemClock
from bqemulator.domain.errors import (
    AlreadyExistsError,
    DomainError,
    InternalError,
    InvalidQueryError,
    NotFoundError,
    PermissionDeniedError,
    QuotaExceededError,
    UnsupportedFeatureError,
    ValidationError,
)
from bqemulator.domain.ids import (
    DatasetId,
    JobId,
    ProjectId,
    RoutineId,
    TableId,
)
from bqemulator.domain.result import Err, Ok, Result

__all__ = [
    "AlreadyExistsError",
    "Clock",
    "DatasetId",
    "DomainError",
    "Err",
    "FrozenClock",
    "InternalError",
    "InvalidQueryError",
    "JobId",
    "NotFoundError",
    "Ok",
    "PermissionDeniedError",
    "ProjectId",
    "QuotaExceededError",
    "Result",
    "RoutineId",
    "SystemClock",
    "TableId",
    "UnsupportedFeatureError",
    "ValidationError",
]
