"""Domain exception types for the Case Assistant API.

These map to HTTP problem responses via the registered exception handlers
in ``app.api.exception_handlers``.  Raise these from service / repository
code; the exception-handler layer translates them to the appropriate HTTP
status code and RFC 7807 Problem Details body.
"""

from __future__ import annotations


class BadRequestException(Exception):
    """Raised when the request is syntactically valid but semantically wrong."""


class NotFoundException(Exception):
    """Raised when the requested resource does not exist."""


class ConflictException(Exception):
    """Raised when the operation conflicts with the current resource state."""


class UnauthorizedException(Exception):
    """Raised when the caller is not authenticated or lacks permission."""


class ValidationException(Exception):
    """Raised when one or more field-level validation rules fail.

    Args:
        message: Human-readable summary of the validation failure.
        errors:  Mapping of field name → list of error strings, mirroring the
                 shape that FastAPI's ``RequestValidationError`` produces so the
                 same ``ValidationProblemDetails`` serialiser can be reused.
    """

    def __init__(
        self,
        message: str,
        errors: dict[str, list[str]] | None = None,
    ) -> None:
        super().__init__(message)
        self.errors: dict[str, list[str]] = errors or {}
