"""Domain-error hierarchy.

Every expected error in the emulator inherits from :class:`DomainError` and
maps to the exact JSON shape the real BigQuery service returns via
:meth:`DomainError.to_bigquery_error`.

Reference: https://cloud.google.com/bigquery/docs/error-messages

The HTTP status code, BigQuery ``reason`` string, and gRPC canonical
status code are attached at the subclass level and used by adapter layers
to render responses consistently.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar


@dataclass(slots=True)
class ErrorDetail:
    """A single error entry in BigQuery's ``ErrorProto.errors`` array."""

    reason: str
    message: str
    domain: str = "global"
    location: str | None = None
    location_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Render as a dict matching BigQuery's JSON ``ErrorProto`` shape."""
        out: dict[str, Any] = {
            "domain": self.domain,
            "reason": self.reason,
            "message": self.message,
        }
        if self.location is not None:
            out["location"] = self.location
        if self.location_type is not None:
            out["locationType"] = self.location_type
        return out


class DomainError(Exception):
    """Base class for all expected domain errors.

    Subclasses define three class variables that adapters use to render the
    error:

    * ``http_status`` — HTTP status code for REST responses.
    * ``bq_reason`` — BigQuery ``reason`` string. Matches the values the
      real service returns in ``ErrorProto.reason``.
    * ``grpc_status_name`` — canonical gRPC status name
      (``INVALID_ARGUMENT``, ``NOT_FOUND``, etc.).
    """

    http_status: ClassVar[int] = 500
    bq_reason: ClassVar[str] = "internalError"
    grpc_status_name: ClassVar[str] = "INTERNAL"

    def __init__(
        self,
        message: str,
        *,
        details: list[ErrorDetail] | None = None,
        location: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details: list[ErrorDetail] = details or []
        self.location = location

    def to_bigquery_error(self) -> dict[str, Any]:
        """Render as BigQuery's JSON error shape.

        Matches::

            {
                "error": {
                    "code": 400,
                    "message": "...",
                    "errors": [{"domain": "global", "reason": "...", "message": "..."}],
                    "status": "INVALID_ARGUMENT",
                }
            }
        """
        errors: list[dict[str, Any]]
        if self.details:
            errors = [d.to_dict() for d in self.details]
        else:
            errors = [
                ErrorDetail(
                    reason=self.bq_reason,
                    message=self.message,
                    location=self.location,
                ).to_dict()
            ]
        return {
            "error": {
                "code": self.http_status,
                "message": self.message,
                "errors": errors,
                "status": self.grpc_status_name,
            }
        }


# -- 4xx --------------------------------------------------------------------


class InvalidQueryError(DomainError):
    """Malformed SQL, unknown function, or semantic analysis failure."""

    http_status = 400
    bq_reason = "invalidQuery"
    grpc_status_name = "INVALID_ARGUMENT"


class ValidationError(DomainError):
    """Request failed schema or semantic validation (non-SQL)."""

    http_status = 400
    bq_reason = "invalid"
    grpc_status_name = "INVALID_ARGUMENT"


class NotFoundError(DomainError):
    """A resource (dataset, table, job, routine, model) was not found."""

    http_status = 404
    bq_reason = "notFound"
    grpc_status_name = "NOT_FOUND"


class AlreadyExistsError(DomainError):
    """Create request targeted a resource that already exists."""

    http_status = 409
    bq_reason = "duplicate"
    grpc_status_name = "ALREADY_EXISTS"


class PermissionDeniedError(DomainError):
    """Row-access policy, authorized view, or other policy check failed.

    The emulator does not enforce IAM, but it does enforce row-access
    policies on configured tables. This is raised when a query would
    return rows the caller is not permitted to see.
    """

    http_status = 403
    bq_reason = "accessDenied"
    grpc_status_name = "PERMISSION_DENIED"


class QuotaExceededError(DomainError):
    """A configurable emulator quota was exceeded (e.g. max concurrent jobs)."""

    http_status = 429
    bq_reason = "quotaExceeded"
    grpc_status_name = "RESOURCE_EXHAUSTED"


class UnsupportedFeatureError(DomainError):
    """A feature explicitly out of scope for v1 was invoked.

    Raised for BigQuery ML statements, scheduled queries, Data Transfer
    Service operations, and any other feature enumerated in
    ``docs/reference/out-of-scope.md``.
    """

    http_status = 501
    bq_reason = "notImplemented"
    grpc_status_name = "UNIMPLEMENTED"


# -- 5xx --------------------------------------------------------------------


class InternalError(DomainError):
    """Unexpected condition that should never happen in a healthy build."""

    http_status = 500
    bq_reason = "internalError"
    grpc_status_name = "INTERNAL"


class OutOfRangeError(DomainError):
    """A requested position/time fell outside the valid range.

    BigQuery returns ``outOfRange`` when ``FOR SYSTEM_TIME AS OF`` is
    called with a timestamp outside the time-travel retention window or
    before the table existed.
    """

    http_status = 400
    bq_reason = "outOfRange"
    grpc_status_name = "OUT_OF_RANGE"


# -- catalog-specific ------------------------------------------------------


@dataclass(slots=True, frozen=True)
class ResourceRef:
    """Reference to a BigQuery-style resource (for error messages)."""

    kind: str  # "dataset" | "table" | "job" | "routine" | "model"
    project_id: str
    dataset_id: str | None = None
    resource_id: str | None = None

    def format(self) -> str:
        """Human-readable ``project.dataset.resource`` form."""
        parts: list[str] = [self.project_id]
        if self.dataset_id is not None:
            parts.append(self.dataset_id)
        if self.resource_id is not None:
            parts.append(self.resource_id)
        return f"{self.kind}:{'.'.join(parts)}"


def resource_not_found(ref: ResourceRef) -> NotFoundError:
    """Helper to raise a consistent 'not found' error for any resource."""
    return NotFoundError(
        f"Not found: {ref.format()}",
        details=[
            ErrorDetail(
                reason="notFound",
                message=f"Not found: {ref.format()}",
            )
        ],
    )


def resource_already_exists(ref: ResourceRef) -> AlreadyExistsError:
    """Helper to raise a consistent 'already exists' error for any resource."""
    return AlreadyExistsError(
        f"Already Exists: {ref.format()}",
        details=[
            ErrorDetail(
                reason="duplicate",
                message=f"Already Exists: {ref.format()}",
            )
        ],
    )


__all__ = [
    "AlreadyExistsError",
    "DomainError",
    "ErrorDetail",
    "InternalError",
    "InvalidQueryError",
    "NotFoundError",
    "OutOfRangeError",
    "PermissionDeniedError",
    "QuotaExceededError",
    "ResourceRef",
    "UnsupportedFeatureError",
    "ValidationError",
    "resource_already_exists",
    "resource_not_found",
]
