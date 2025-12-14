"""
Payment-specific exceptions for payment operations.

This module provides a hierarchy of exceptions for payment operations,
including both payment domain errors and concurrency control errors.

Exception Hierarchy:
    PaymentError (base for payment domain)
    ├── PaymentNotFoundError - Payment entity lookup failures
    ├── PaymentValidationError - Payment validation failures
    └── PaymentProcessingError - Payment processing failures

    StaleRecordError - Optimistic locking conflict (inherits ConflictError)
    LockAcquisitionError - Distributed lock timeout (inherits ConflictError)
    InvalidStateTransitionError - FSM transition not allowed (inherits ConflictError)

Usage:
    from payments.exceptions import (
        PaymentError,
        StaleRecordError,
        LockAcquisitionError,
        InvalidStateTransitionError,
    )

    # Optimistic locking conflict
    if rows_updated == 0:
        raise StaleRecordError(
            f"PaymentOrder {pk} was modified by another process",
            details={"pk": str(pk), "expected_version": 3, "current_version": 5}
        )

    # Distributed lock timeout
    raise LockAcquisitionError(
        f"Could not acquire lock for payment:123 within 10s",
        details={"key": "payment:123", "timeout": 10}
    )

    # Invalid state transition
    raise InvalidStateTransitionError(
        "Cannot capture payment from 'draft' state",
        details={"current_state": "draft", "target_state": "captured"}
    )
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.exceptions import BaseApplicationError, ConflictError

if TYPE_CHECKING:
    from typing import Any


# =============================================================================
# Payment Domain Exceptions
# =============================================================================


class PaymentError(BaseApplicationError):
    """
    Base exception for all payment operations.

    All payment-specific exceptions inherit from this class,
    which itself inherits from BaseApplicationError for
    consistent API error responses.

    Example:
        try:
            payment_service.process_payment(order_id)
        except PaymentError as e:
            logger.error(f"Payment operation failed: {e}")
            return Response(e.to_dict(), status=400)
    """

    default_error_code: str = "PAYMENT_ERROR"


class PaymentNotFoundError(PaymentError):
    """
    Raised when a payment entity cannot be found.

    Use for:
    - PaymentOrder lookup fails
    - Payout lookup fails
    - Refund lookup fails
    - ConnectedAccount lookup fails

    Example:
        order = PaymentOrder.objects.filter(id=order_id).first()
        if not order:
            raise PaymentNotFoundError(
                f"PaymentOrder {order_id} not found",
                details={"payment_order_id": str(order_id)}
            )
    """

    default_error_code: str = "PAYMENT_NOT_FOUND"


class PaymentValidationError(PaymentError):
    """
    Raised when payment validation fails.

    Use for:
    - Invalid payment amount
    - Invalid currency
    - Missing required fields
    - Business rule violations

    Example:
        if amount_cents <= 0:
            raise PaymentValidationError(
                "Payment amount must be positive",
                details={"amount_cents": amount_cents}
            )
    """

    default_error_code: str = "PAYMENT_VALIDATION_ERROR"


class PaymentProcessingError(PaymentError):
    """
    Raised when payment processing fails.

    Use for:
    - Stripe API errors
    - Payment gateway failures
    - Processing timeouts

    Example:
        try:
            stripe.PaymentIntent.create(...)
        except stripe.error.CardError as e:
            raise PaymentProcessingError(
                "Card was declined",
                error_code="CARD_DECLINED",
                details={"decline_code": e.code}
            )
    """

    default_error_code: str = "PAYMENT_PROCESSING_ERROR"


# =============================================================================
# Concurrency Control Exceptions
# =============================================================================


class StaleRecordError(ConflictError):
    """
    Raised when optimistic locking detects concurrent modification.

    This exception indicates that the record was modified by another
    process between read and update operations. The caller should
    either retry the operation with fresh data or abort.

    Attributes:
        details: Contains pk, expected_version, and current_version

    Example:
        # In check_version utility
        rows = Model.objects.filter(pk=pk, version=expected).update(...)
        if rows == 0:
            current = Model.objects.get(pk=pk)
            raise StaleRecordError(
                f"{Model.__name__} {pk} has been modified",
                details={
                    "pk": str(pk),
                    "expected_version": expected,
                    "current_version": current.version,
                }
            )

    Note:
        This exception inherits from ConflictError (HTTP 409) because
        it represents a state conflict that prevents the operation.
    """

    default_error_code: str = "STALE_RECORD"


class LockAcquisitionError(ConflictError):
    """
    Raised when a distributed lock cannot be acquired.

    This exception indicates that another process holds the lock
    and it couldn't be acquired within the timeout period.

    Attributes:
        details: Contains key and timeout information

    Example:
        lock = DistributedLock("payment:123", ttl=30, timeout=10)
        if not lock.acquire():
            raise LockAcquisitionError(
                "Failed to acquire lock 'payment:123' within 10s",
                details={"key": "payment:123", "timeout": 10}
            )

    Note:
        This exception inherits from ConflictError (HTTP 409) because
        it represents a resource contention conflict.
    """

    default_error_code: str = "LOCK_ACQUISITION_FAILED"


class InvalidStateTransitionError(ConflictError):
    """
    Raised when a state machine transition is not allowed.

    This exception wraps django-fsm's TransitionNotAllowed to provide
    our standard error format with additional context.

    Attributes:
        details: Contains current_state, target_state, and transition name

    Example:
        from django_fsm import TransitionNotAllowed

        try:
            order.capture()  # django-fsm transition
        except TransitionNotAllowed:
            raise InvalidStateTransitionError(
                f"Cannot capture payment from '{order.state}' state",
                details={
                    "current_state": order.state,
                    "target_state": "captured",
                    "transition": "capture",
                }
            )

    Note:
        This exception inherits from ConflictError (HTTP 409) because
        the current state conflicts with the requested operation.
    """

    default_error_code: str = "INVALID_STATE_TRANSITION"


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    # Payment domain
    "PaymentError",
    "PaymentNotFoundError",
    "PaymentValidationError",
    "PaymentProcessingError",
    # Concurrency control
    "StaleRecordError",
    "LockAcquisitionError",
    "InvalidStateTransitionError",
]
