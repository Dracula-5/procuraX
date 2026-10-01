"""Domain errors. Framework-free: services and the pure domain layer raise these; the API
layer (app/api/error_handlers.py) maps them to HTTP responses."""

from typing import Any


class DomainError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, code: str | None = None, details: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details
        if code:
            self.code = code


class NotFoundError(DomainError):
    # Also used when a record exists but belongs to another tenant or is not visible to
    # the caller: we never reveal the existence of data the caller cannot access.
    status_code = 404
    code = "not_found"


class AuthenticationError(DomainError):
    status_code = 401
    code = "unauthenticated"


class PermissionDeniedError(DomainError):
    status_code = 403
    code = "forbidden"


class ConflictError(DomainError):
    status_code = 409
    code = "conflict"


class InvalidStateTransitionError(ConflictError):
    code = "invalid_state_transition"


class BusinessRuleViolation(DomainError):
    status_code = 422
    code = "business_rule_violation"


class RateLimitedError(DomainError):
    status_code = 429
    code = "rate_limited"
