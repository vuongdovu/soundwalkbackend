"""
Payment-specific exceptions for payment operations.

This module provides a hierarchy of exceptions for payment operations,
including both payment domain errors, concurrency control errors, and
Stripe-specific errors.

Exception Hierarchy:
    PaymentError (base for payment domain)
    ├── PaymentNotFoundError - Payment entity lookup failures
    ├── PaymentValidationError - Payment validation failures
    └── PaymentProcessingError - Payment processing failures
        └── StripeError - Base for all Stripe errors
            ├── StripeCardDeclinedError - Card declined (permanent)
            ├── StripeInsufficientFundsError - Insufficient funds (permanent)
            ├── StripeInvalidAccountError - Invalid Stripe account (permanent)
            ├── StripeInvalidRequestError - Invalid request params (permanent)
            ├── StripeRateLimitError - Rate limited (transient, retry)
            ├── StripeAPIUnavailableError - API unavailable (transient, retry)
            └── StripeTimeoutError - Request timeout (transient, retry)

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
# Stripe-Specific Exceptions
# =============================================================================


class StripeError(PaymentProcessingError):
    """
    Base exception for all Stripe-related errors.

    Provides common attributes for Stripe error handling:
    - stripe_code: Stripe's internal error code
    - decline_code: Card decline code (if applicable)
    - is_retryable: Whether the operation can be retried

    Use is_retryable to determine retry behavior:
    - True: Transient error, safe to retry with backoff
    - False: Permanent error, do not retry

    Example:
        try:
            StripeAdapter.create_payment_intent(...)
        except StripeError as e:
            if e.is_retryable:
                schedule_retry(e, backoff=exponential)
            else:
                notify_user_permanent_failure(e)
    """

    default_error_code: str = "STRIPE_ERROR"
    is_retryable: bool = False

    def __init__(
        self,
        message: str,
        error_code: str | None = None,
        stripe_code: str | None = None,
        decline_code: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        details = details or {}
        if stripe_code:
            details["stripe_code"] = stripe_code
        if decline_code:
            details["decline_code"] = decline_code
        super().__init__(message, error_code=error_code, details=details)
        self.stripe_code = stripe_code
        self.decline_code = decline_code


# -----------------------------------------------------------------------------
# Permanent Errors (do not retry)
# -----------------------------------------------------------------------------


class StripeCardDeclinedError(StripeError):
    """
    Card was declined by the issuing bank.

    This is a permanent error - do not retry with the same card.
    The decline_code attribute contains the specific reason.

    Common decline codes:
    - generic_decline: Generic decline
    - insufficient_funds: Insufficient funds
    - lost_card: Card reported lost
    - stolen_card: Card reported stolen
    - expired_card: Card has expired
    - incorrect_cvc: CVC verification failed
    - processing_error: Processing error
    - incorrect_number: Invalid card number

    Example:
        except StripeCardDeclinedError as e:
            if e.decline_code == 'insufficient_funds':
                message = "Your card has insufficient funds."
            else:
                message = "Your card was declined."
    """

    default_error_code: str = "CARD_DECLINED"
    is_retryable: bool = False


class StripeInsufficientFundsError(StripeError):
    """
    Insufficient funds on the payment method.

    Separate from StripeCardDeclinedError for clearer error handling
    and user messaging. The customer should use a different payment
    method or add funds to their account.

    Note:
        This is a permanent error - do not retry automatically.
        User action is required before retry can succeed.
    """

    default_error_code: str = "INSUFFICIENT_FUNDS"
    is_retryable: bool = False


class StripeInvalidAccountError(StripeError):
    """
    Invalid Stripe Connect account.

    Raised when the destination account for a transfer is:
    - Not found
    - Disabled or restricted
    - Not properly onboarded
    - Unable to receive payouts

    This requires manual intervention to resolve the account status.

    Example:
        except StripeInvalidAccountError as e:
            alert_admin(f"Payout failed for account: {e.stripe_code}")
            payment_order.mark_payout_blocked()
    """

    default_error_code: str = "INVALID_STRIPE_ACCOUNT"
    is_retryable: bool = False


class StripeInvalidRequestError(StripeError):
    """
    Invalid request parameters sent to Stripe.

    This is a permanent error - the request itself is malformed
    and will never succeed with the same parameters.

    Possible causes:
    - Invalid payment intent ID
    - Invalid amount or currency
    - Missing required parameters
    - Operation not allowed (e.g., refund > captured amount)

    Check the stripe_code and details for specific information
    about what was invalid.

    Note:
        This usually indicates a bug in our code, not a user error.
        Log these errors for developer investigation.
    """

    default_error_code: str = "INVALID_STRIPE_REQUEST"
    is_retryable: bool = False


# -----------------------------------------------------------------------------
# Transient Errors (safe to retry with backoff)
# -----------------------------------------------------------------------------


class StripeRateLimitError(StripeError):
    """
    Rate limited by Stripe API.

    Stripe allows 100 requests/second in live mode, 25/second in test mode.
    This error indicates we've exceeded those limits.

    Retry Strategy:
    - Use exponential backoff starting at 1 second
    - Maximum 3 retries before failing
    - Consider circuit breaker for sustained rate limiting

    Example:
        @retry(
            retry=retry_if_exception(is_retryable_stripe_error),
            wait=wait_exponential(multiplier=1, max=60),
            stop=stop_after_attempt(3),
        )
        def call_stripe():
            ...
    """

    default_error_code: str = "STRIPE_RATE_LIMITED"
    is_retryable: bool = True


class StripeAPIUnavailableError(StripeError):
    """
    Stripe API is temporarily unavailable.

    This covers:
    - Network connectivity issues
    - Stripe server errors (5xx)
    - DNS resolution failures
    - SSL/TLS errors

    These are transient errors that typically resolve themselves.
    Retry with exponential backoff.

    Example:
        except StripeAPIUnavailableError:
            # Queue for retry via Celery
            process_payment.apply_async(
                args=[payment_order_id],
                countdown=backoff_delay(attempt),
            )
    """

    default_error_code: str = "STRIPE_UNAVAILABLE"
    is_retryable: bool = True


class StripeTimeoutError(StripeError):
    """
    Stripe API call timed out.

    The request was sent but no response was received within
    the configured timeout (STRIPE_API_TIMEOUT_SECONDS).

    IMPORTANT: The operation may have succeeded on Stripe's side.
    Always use idempotency keys to handle this safely. When retrying,
    the idempotency key ensures we don't duplicate the operation.

    Retry Strategy:
    - Retry with same idempotency key
    - Stripe will return the original response if it succeeded
    - Use exponential backoff

    Example:
        # Safe to retry because of idempotency key
        result = StripeAdapter.create_payment_intent(
            params,
            idempotency_key=f"create:{order_id}:{attempt}",
        )
    """

    default_error_code: str = "STRIPE_TIMEOUT"
    is_retryable: bool = True


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
    # Stripe-specific
    "StripeError",
    "StripeCardDeclinedError",
    "StripeInsufficientFundsError",
    "StripeInvalidAccountError",
    "StripeInvalidRequestError",
    "StripeRateLimitError",
    "StripeAPIUnavailableError",
    "StripeTimeoutError",
    # Concurrency control
    "StaleRecordError",
    "LockAcquisitionError",
    "InvalidStateTransitionError",
]
