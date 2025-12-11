"""
Base service layer patterns for business logic encapsulation.

This module provides foundational patterns for the service layer:
- ServiceResult: Standard result wrapper for consistent success/failure handling
- BaseService: Base class with common service utilities

Service Layer Philosophy:
    Services encapsulate business logic separate from views and models.
    Views handle HTTP concerns, models handle data, services handle logic.

Pattern Comparison:
    - ServiceResult: Use for expected failures (validation, business rules)
    - Exceptions: Use for unexpected failures (database errors, bugs)

Usage:
    from core.services import BaseService, ServiceResult

    class UserService(BaseService):
        @classmethod
        def create_user(cls, email: str, password: str) -> ServiceResult[User]:
            # Validate
            if User.objects.filter(email=email).exists():
                return ServiceResult.failure(
                    "Email already registered",
                    error_code="EMAIL_EXISTS"
                )

            # Create within transaction
            with cls.atomic():
                user = User.objects.create_user(email=email, password=password)
                Profile.objects.create(user=user)

            cls.get_logger().info(f"Created user {user.id}")
            return ServiceResult.success(user)

    # In view
    result = UserService.create_user(email, password)
    if result.success:
        return Response(UserSerializer(result.data).data, status=201)
    return Response(result.to_response(), status=400)

Related:
    - core.exceptions: For unexpected/exceptional errors
    - utils.decorators: For cross-cutting concerns (caching, rate limiting)
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Generic, TypeVar

if TYPE_CHECKING:
    from collections.abc import Generator
    from typing import Any

# Generic type for ServiceResult data
T = TypeVar("T")


@dataclass
class ServiceResult(Generic[T]):
    """
    Standard result wrapper for service operations.

    Provides consistent success/failure handling without exceptions.
    Use this for expected failures (validation errors, business rule violations).

    Attributes:
        success: Whether the operation succeeded
        data: Result data if successful (None if failed)
        error: Error message if failed (None if successful)
        error_code: Machine-readable error code for client handling
        errors: Field-level errors for validation failures

    Usage:
        # Success case
        user = User.objects.create(email=email)
        return ServiceResult.success(user)

        # Failure case
        return ServiceResult.failure("Email already exists", "EMAIL_EXISTS")

        # Validation errors with field details
        return ServiceResult.failure(
            "Validation failed",
            error_code="VALIDATION_ERROR",
            errors={"email": ["Invalid format"], "password": ["Too short"]}
        )

        # Check result
        result = UserService.create_user(email, password)
        if result.success:
            user = result.data
        else:
            print(f"Error: {result.error} ({result.error_code})")

    Note:
        This pattern is inspired by Result types in Rust/Swift.
        It makes error handling explicit without try/except blocks.
    """

    success: bool
    data: T | None = None
    error: str | None = None
    error_code: str | None = None
    errors: dict[str, list[str]] | None = field(default=None)

    @classmethod
    def ok(cls, data: T) -> ServiceResult[T]:
        """
        Create a successful result.

        Alias for success() - use whichever reads better in context.

        Args:
            data: The result data

        Returns:
            ServiceResult with success=True and data set
        """
        return cls(success=True, data=data)

    @classmethod
    def success(cls, data: T) -> ServiceResult[T]:
        """
        Create a successful result.

        Args:
            data: The result data

        Returns:
            ServiceResult with success=True and data set

        Example:
            user = User.objects.create(email=email)
            return ServiceResult.success(user)
        """
        return cls(success=True, data=data)

    @classmethod
    def failure(
        cls,
        error: str,
        error_code: str | None = None,
        errors: dict[str, list[str]] | None = None,
    ) -> ServiceResult[T]:
        """
        Create a failed result.

        Args:
            error: Human-readable error message
            error_code: Machine-readable error code for client handling
            errors: Field-level errors (for validation failures)

        Returns:
            ServiceResult with success=False and error details

        Example:
            # Simple error
            return ServiceResult.failure("User not found", "USER_NOT_FOUND")

            # Validation errors
            return ServiceResult.failure(
                "Validation failed",
                error_code="VALIDATION_ERROR",
                errors={"email": ["Already exists"], "username": ["Too short"]}
            )
        """
        return cls(
            success=False,
            error=error,
            error_code=error_code,
            errors=errors,
        )

    @classmethod
    def from_exception(cls, exc: Exception, error_code: str | None = None) -> ServiceResult[T]:
        """
        Create a failed result from an exception.

        Useful for converting caught exceptions to ServiceResult.

        Args:
            exc: The caught exception
            error_code: Optional error code (defaults to exception class name)

        Returns:
            ServiceResult with error details from exception

        Example:
            try:
                external_api.call()
            except ExternalAPIError as e:
                return ServiceResult.from_exception(e, "API_ERROR")
        """
        return cls(
            success=False,
            error=str(exc),
            error_code=error_code or exc.__class__.__name__.upper(),
        )

    def to_response(self) -> dict[str, Any]:
        """
        Convert to API response format.

        Returns a dictionary suitable for returning from a DRF view.

        Returns:
            Dict with success status and data or error details

        Example:
            result = UserService.create_user(email)
            if result.success:
                return Response({"user": result.data.id}, status=201)
            return Response(result.to_response(), status=400)
        """
        if self.success:
            return {"success": True, "data": self.data}

        response: dict[str, Any] = {
            "success": False,
            "error": self.error,
        }
        if self.error_code:
            response["error_code"] = self.error_code
        if self.errors:
            response["errors"] = self.errors
        return response

    def map(self, func) -> ServiceResult:
        """
        Transform the data if successful.

        Applies a function to the data if the result is successful.
        Returns unchanged if failed.

        Args:
            func: Function to apply to data

        Returns:
            New ServiceResult with transformed data

        Example:
            result = UserService.get_user(user_id)
            serialized = result.map(lambda u: UserSerializer(u).data)
        """
        if self.success and self.data is not None:
            return ServiceResult.success(func(self.data))
        return self  # type: ignore

    def __bool__(self) -> bool:
        """
        Allow using result in boolean context.

        Example:
            result = UserService.create_user(email)
            if result:  # Same as: if result.success
                print("Success!")
        """
        return self.success


class BaseService:
    """
    Base class for service layer classes.

    Provides common utilities for services:
    - Logging setup per service
    - Database transaction management
    - Exception handling patterns

    Usage:
        class UserService(BaseService):
            @classmethod
            def create_user(cls, email: str) -> ServiceResult[User]:
                with cls.atomic():
                    # All operations in this block are in a transaction
                    user = User.objects.create(email=email)
                    Profile.objects.create(user=user)

                cls.get_logger().info(f"Created user {user.id}")
                return ServiceResult.success(user)

    Design Notes:
        - Use @staticmethod or @classmethod (no instance state)
        - Services should be stateless
        - Use ServiceResult for expected failures
        - Raise exceptions for unexpected failures
    """

    @classmethod
    def get_logger(cls) -> logging.Logger:
        """
        Get logger for this service.

        Returns a logger named after the service class for
        easy filtering in logs.

        Returns:
            Logger instance for this service

        Example:
            class PaymentService(BaseService):
                @classmethod
                def process_payment(cls, amount):
                    cls.get_logger().info(f"Processing payment: {amount}")
        """
        return logging.getLogger(f"{cls.__module__}.{cls.__name__}")

    @classmethod
    @contextmanager
    def atomic(cls) -> Generator[None, None, None]:
        """
        Execute operations in a database transaction.

        All database operations within this context manager are
        wrapped in a transaction. If any operation fails, all
        changes are rolled back.

        Yields:
            None

        Example:
            with cls.atomic():
                user = User.objects.create(email=email)
                Profile.objects.create(user=user)
                # If Profile creation fails, User is also rolled back

        Note:
            This is a thin wrapper around Django's transaction.atomic().
            Use it to make transaction boundaries explicit in service code.
        """
        # TODO: Implement
        # from django.db import transaction
        # with transaction.atomic():
        #     yield
        yield

    @classmethod
    def handle_exception(
        cls,
        exc: Exception,
        context: str = "",
        log_level: int = logging.ERROR,
    ) -> ServiceResult:
        """
        Convert exception to ServiceResult with logging.

        Provides consistent exception handling across services.
        Logs the exception and returns a ServiceResult.

        Args:
            exc: The caught exception
            context: Additional context for logging
            log_level: Logging level (default ERROR)

        Returns:
            ServiceResult with error details

        Example:
            try:
                stripe.Charge.create(amount=amount)
            except stripe.error.CardError as e:
                return cls.handle_exception(e, "payment processing")
        """
        # TODO: Implement
        # logger = cls.get_logger()
        # message = f"{context}: {exc}" if context else str(exc)
        # logger.log(log_level, message, exc_info=True)
        # return ServiceResult.from_exception(exc)
        return ServiceResult.failure(str(exc), exc.__class__.__name__.upper())

    @classmethod
    def validate_required(cls, **kwargs) -> ServiceResult | None:
        """
        Validate that required fields are provided.

        Returns a failure result if any required field is None or empty.
        Returns None if all fields are valid.

        Args:
            **kwargs: Field names and their values

        Returns:
            ServiceResult.failure if validation fails, None otherwise

        Example:
            validation = cls.validate_required(email=email, password=password)
            if validation:
                return validation  # Return the error

            # Continue with valid data...
        """
        # TODO: Implement
        # errors = {}
        # for field_name, value in kwargs.items():
        #     if value is None or (isinstance(value, str) and not value.strip()):
        #         errors[field_name] = ["This field is required."]
        #
        # if errors:
        #     return ServiceResult.failure(
        #         "Required fields missing",
        #         error_code="VALIDATION_ERROR",
        #         errors=errors,
        #     )
        # return None
        pass
