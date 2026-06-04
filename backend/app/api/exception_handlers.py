"""Centralized exception handlers for the Case Assistant API.

Centralized exception filter: each domain exception type is mapped to a
specific HTTP status code and an RFC 7807 Problem Details response body.
All handlers attach the current OpenTelemetry trace ID and a UTC timestamp
so callers can correlate errors with distributed traces.

Registration
------------
Call ``register_exception_handlers(app)`` once inside ``create_app()`` after
the ``FastAPI`` instance is created:

    from app.api.exception_handlers import register_exception_handlers
    register_exception_handlers(app)

Exception → HTTP status mapping
---------------------------------
  BadRequestException         → 400  (https://tools.ietf.org/html/rfc7231#section-6.5.1)
  ValidationException         → 400  (field-level errors, same shape as 422 but 400)
  RequestValidationError      → 422  (Pydantic / FastAPI model parse failure)
  UnauthorizedException       → 401  (https://tools.ietf.org/html/rfc7235#section-3.1)
  NotFoundException           → 404  (https://tools.ietf.org/html/rfc7231#section-6.5.4)
  ConflictException           → 409  (https://www.rfc-editor.org/rfc/rfc7231#section-6.5.8)
  HTTPException               → pass-through (status already set by caller)
  Exception (catch-all)       → 500  (https://tools.ietf.org/html/rfc7231#section-6.6.1)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

try:
    from opentelemetry import trace as otel_trace

    def _trace_id() -> str | None:
        span = otel_trace.get_current_span()
        ctx = span.get_span_context() if span else None
        if ctx and ctx.is_valid:
            return format(ctx.trace_id, "032x")
        return None

except ImportError:  # opentelemetry not installed — degrade gracefully

    def _trace_id() -> str | None:  # type: ignore[misc]
        return None


from app.core.exceptions import (
    BadRequestException,
    ConflictException,
    NotFoundException,
    UnauthorizedException,
    ValidationException,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _problem(
    *,
    status: int,
    type_uri: str,
    title: str,
    detail: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an RFC 7807 Problem Details dict."""
    body: dict[str, Any] = {
        "type": type_uri,
        "title": title,
        "status": status,
        "detail": detail,
        "trace_id": _trace_id(),
        "timestamp": datetime.now(UTC).isoformat(),
    }
    if extra:
        body.update(extra)
    return body


def _validation_problem(
    *,
    title: str,
    errors: dict[str, list[str]],
) -> dict[str, Any]:
    """Build an RFC 7807 Validation Problem Details dict (field-level errors)."""
    return {
        "type": "https://tools.ietf.org/html/rfc7231#section-6.5.1",
        "title": title,
        "status": status.HTTP_400_BAD_REQUEST,
        "errors": errors,
        "trace_id": _trace_id(),
        "timestamp": datetime.now(UTC).isoformat(),
    }


# ---------------------------------------------------------------------------
# Individual handlers
# ---------------------------------------------------------------------------


async def _handle_bad_request(request: Request, exc: BadRequestException) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content=_problem(
            status=status.HTTP_400_BAD_REQUEST,
            type_uri="https://tools.ietf.org/html/rfc7231#section-6.5.1",
            title="The request was invalid.",
            detail=str(exc),
        ),
    )


async def _handle_validation_exception(request: Request, exc: ValidationException) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content=_validation_problem(
            title="One or more validation errors occurred.",
            errors=exc.errors if exc.errors else {"": [str(exc)]},
        ),
    )


async def _handle_request_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Handle Pydantic model parse / FastAPI request validation failures."""
    errors: dict[str, list[str]] = {}
    for error in exc.errors():
        # loc is a tuple like ("body", "field_name") — use the last segment as the key
        field = ".".join(str(loc) for loc in error["loc"] if loc != "body")
        errors.setdefault(field, []).append(error["msg"])

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=_validation_problem(
            title="One or more validation errors occurred.",
            errors=errors,
        ),
    )


async def _handle_unauthorized(request: Request, exc: UnauthorizedException) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content=_problem(
            status=status.HTTP_401_UNAUTHORIZED,
            type_uri="https://tools.ietf.org/html/rfc7235#section-3.1",
            title="Unauthorized.",
            detail=str(exc),
        ),
    )


async def _handle_not_found(request: Request, exc: NotFoundException) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content=_problem(
            status=status.HTTP_404_NOT_FOUND,
            type_uri="https://tools.ietf.org/html/rfc7231#section-6.5.4",
            title="The specified resource was not found.",
            detail=str(exc),
        ),
    )


async def _handle_conflict(request: Request, exc: ConflictException) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content=_problem(
            status=status.HTTP_409_CONFLICT,
            type_uri="https://www.rfc-editor.org/rfc/rfc7231#section-6.5.8",
            title="The specified resource experienced a conflict.",
            detail=str(exc),
        ),
    )


async def _handle_http_exception(request: Request, exc: HTTPException) -> JSONResponse:
    """Pass-through: preserve the status code and detail already set by the caller."""
    return JSONResponse(
        status_code=exc.status_code,
        content=_problem(
            status=exc.status_code,
            type_uri="https://tools.ietf.org/html/rfc7231",
            title="HTTP error.",
            detail=str(exc.detail),
        ),
    )


async def _handle_unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all: return 500 without leaking internal details."""
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_problem(
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            type_uri="https://tools.ietf.org/html/rfc7231#section-6.6.1",
            title="An unexpected error occurred.",
            # Expose the message in non-production; in production the
            # ENVIRONMENT variable should be set to "production" and you
            # may want to swap this for a generic string.
            detail=str(exc),
        ),
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_exception_handlers(app: FastAPI) -> None:
    """Register all domain and framework exception handlers on *app*.

    Call this once inside ``create_app()`` after the ``FastAPI`` instance is
    created, before any middleware or routers are added.
    """
    app.add_exception_handler(BadRequestException, _handle_bad_request)  # type: ignore[arg-type]
    app.add_exception_handler(ValidationException, _handle_validation_exception)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _handle_request_validation_error)  # type: ignore[arg-type]
    app.add_exception_handler(UnauthorizedException, _handle_unauthorized)  # type: ignore[arg-type]
    app.add_exception_handler(NotFoundException, _handle_not_found)  # type: ignore[arg-type]
    app.add_exception_handler(ConflictException, _handle_conflict)  # type: ignore[arg-type]
    app.add_exception_handler(HTTPException, _handle_http_exception)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _handle_unhandled_exception)  # type: ignore[arg-type]
