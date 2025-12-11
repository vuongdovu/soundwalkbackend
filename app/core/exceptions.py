"""
Base exception classes for application-wide error handling.

This module provides a standardized exception hierarchy that enables:
- Consistent error responses across the application
- Machine-readable error codes for client handling
- Detailed error information for debugging

Exception Hierarchy:
    BaseApplicationError (base)
    ├── ValidationError - Input validation failures
    ├── NotFoundError - Resource not found
    ├── PermissionDeniedError - Authorization failures
    ├── ConflictError - State conflicts (duplicates, concurrent modifications)
    ├── RateLimitError - Rate limit exceeded
    └── ExternalServiceError - Third-party service failures

Usage:
    from core.exceptions import ValidationError, NotFoundError

    # Raise with message only
    raise ValidationError("Invalid email format")

    # Raise with error code for client handling
    raise ValidationError("Email already exists", error_code="EMAIL_DUPLICATE")

    # Raise with additional details
    raise ValidationError(
        "Validation failed",
        error_code="VALIDATION_ERROR",
        details={"email": ["Invalid format"], "password": ["Too short"]}
    )

    # Convert to dict for API response
    try:
        ...
    except BaseApplicationError as e:
        return Response(e.to_dict(), status=400)

Note:
    These exceptions are for domain/business logic errors.
    DRF handles API-layer exceptions (serialization, authentication, etc.).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any


class BaseApplicationError(Exception):
    """
    Base exception for all application-specific errors.

    Provides a consistent interface for error handling across the application.
    All custom exceptions should inherit from this class.

    Attributes:
        message: Human-readable error description
        error_code: Machine-readable code for client-side handling
        details: Additional error context (field errors, metadata, etc.)

    Example:
        try:
            user = UserService.get_by_id(user_id)
        except NotFoundError as e:
            logger.warning(f"User not found: {e.error_code}")
            return Response(e.to_dict(), status=404)
    """

    default_error_code: str = "APPLICATION_ERROR"

    def __init__(
        self,
        message: str,
        error_code: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        """
        Initialize the exception.

        Args:
            message: Human-readable error description
            error_code: Machine-readable error code (defaults to class default)
            details: Additional error context
        """
        self.message = message
        self.error_code = error_code or self.default_error_code
        self.details = details or {}
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        """
        Convert exception to dictionary for API response.

        Returns:
            Dict with error, error_code, and details keys

        Example:
            {
                "error": "User not found",
                "error_code": "USER_NOT_FOUND",
                "details": {"user_id": 123}
            }
        """
        result: dict[str, Any] = {
            "error": self.message,
            "error_code": self.error_code,
        }
        if self.details:
            result["details"] = self.details
        return result

    def __str__(self) -> str:
        """Return string representation with error code."""
        return f"[{self.error_code}] {self.message}"

    def __repr__(self) -> str:
        """Return detailed representation for debugging."""
        return (
            f"{self.__class__.__name__}("
            f"message={self.message!r}, "
            f"error_code={self.error_code!r}, "
            f"details={self.details!r})"
        )


class ValidationError(BaseApplicationError):
    """
    Raised when input validation fails.

    Use for:
    - Invalid field formats (email, phone, etc.)
    - Business rule violations (age requirements, etc.)
    - Missing required fields
    - Field-level validation errors

    Example:
        # Single field error
        raise ValidationError("Invalid email format", error_code="INVALID_EMAIL")

        # Multiple field errors
        raise ValidationError(
            "Validation failed",
            error_code="VALIDATION_ERROR",
            details={
                "email": ["Invalid format", "Already exists"],
                "password": ["Must be at least 8 characters"]
            }
        )

    Note:
        For DRF serializer validation, use DRF's built-in validation.
        Use this for service-layer validation logic.
    """

    default_error_code: str = "VALIDATION_ERROR"


class NotFoundError(BaseApplicationError):
    """
    Raised when a requested resource is not found.

    Use for:
    - Database record not found
    - External resource not found
    - File not found

    Example:
        user = User.objects.filter(id=user_id).first()
        if not user:
            raise NotFoundError(
                f"User with ID {user_id} not found",
                error_code="USER_NOT_FOUND",
                details={"user_id": user_id}
            )

    Note:
        Consider returning None or empty results for list queries.
        Use NotFoundError for single-resource lookups where existence is expected.
    """

    default_error_code: str = "NOT_FOUND"


class PermissionDeniedError(BaseApplicationError):
    """
    Raised when user lacks permission for an operation.

    Use for:
    - Unauthorized resource access
    - Insufficient subscription tier
    - Role-based access control violations
    - Feature access restrictions

    Example:
        if not user.has_permission("admin"):
            raise PermissionDeniedError(
                "Admin access required",
                error_code="ADMIN_REQUIRED"
            )

        if user.subscription_tier != "pro":
            raise PermissionDeniedError(
                "This feature requires a Pro subscription",
                error_code="SUBSCRIPTION_REQUIRED",
                details={"required_tier": "pro", "current_tier": user.subscription_tier}
            )

    Note:
        For authentication failures (missing/invalid token), use DRF's
        AuthenticationFailed. Use this for authorization failures.
    """

    default_error_code: str = "PERMISSION_DENIED"


class ConflictError(BaseApplicationError):
    """
    Raised when operation conflicts with current resource state.

    Use for:
    - Duplicate entries (unique constraint violations)
    - Concurrent modification conflicts
    - Invalid state transitions
    - Optimistic locking failures

    Example:
        # Duplicate check
        if User.objects.filter(email=email).exists():
            raise ConflictError(
                "Email already registered",
                error_code="EMAIL_EXISTS",
                details={"email": email}
            )

        # State transition
        if order.status != "pending":
            raise ConflictError(
                f"Cannot cancel order in {order.status} status",
                error_code="INVALID_STATE_TRANSITION",
                details={"current_status": order.status, "action": "cancel"}
            )

    Note:
        HTTP 409 Conflict is the appropriate status for these errors.
    """

    default_error_code: str = "CONFLICT"


class RateLimitError(BaseApplicationError):
    """
    Raised when rate limit is exceeded.

    Use for:
    - API rate limiting
    - Action throttling (login attempts, password resets)
    - Resource usage limits

    Example:
        if login_attempts > 5:
            raise RateLimitError(
                "Too many login attempts. Please try again later.",
                error_code="LOGIN_RATE_LIMIT",
                details={
                    "retry_after": 300,  # seconds
                    "attempts": login_attempts,
                    "max_attempts": 5
                }
            )

    Note:
        Include retry_after in details when possible to help clients.
        HTTP 429 Too Many Requests is the appropriate status.
    """

    default_error_code: str = "RATE_LIMIT_EXCEEDED"


class ExternalServiceError(BaseApplicationError):
    """
    Raised when an external service call fails.

    Use for:
    - Third-party API failures (Stripe, OpenAI, etc.)
    - Network timeouts
    - External service unavailability
    - Unexpected external service responses

    Example:
        try:
            stripe.Customer.create(email=email)
        except stripe.error.APIError as e:
            raise ExternalServiceError(
                "Payment service unavailable",
                error_code="STRIPE_ERROR",
                details={
                    "service": "stripe",
                    "original_error": str(e)
                }
            )

    Note:
        Log the original error for debugging but don't expose
        internal details to clients in production.
        HTTP 502 Bad Gateway or 503 Service Unavailable are appropriate.
    """

    default_error_code: str = "EXTERNAL_SERVICE_ERROR"
