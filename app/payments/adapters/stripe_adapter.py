"""
Stripe API adapter for payment operations.

This module provides the StripeAdapter class which encapsulates all
Stripe API interactions. All Stripe calls should go through this
adapter to ensure consistent error handling, timeouts, idempotency,
and observability.

Features:
- Configurable timeouts on all API calls
- Automatic error translation to domain exceptions
- Structured logging with timing metrics
- Idempotency support for safe retries
- Thread-safe for use from Celery workers

Configuration (via settings):
- STRIPE_SECRET_KEY: Stripe API secret key
- STRIPE_WEBHOOK_SECRET: Webhook signing secret
- STRIPE_API_TIMEOUT_SECONDS: API call timeout (default: 10)
- STRIPE_MAX_RETRIES: Max retry attempts (default: 3)

Usage:
    from payments.adapters import StripeAdapter, CreatePaymentIntentParams

    # Create a PaymentIntent
    result = StripeAdapter.create_payment_intent(
        CreatePaymentIntentParams(
            amount_cents=5000,
            currency='usd',
            metadata={'payment_order_id': str(order.id)},
            idempotency_key='create_intent:order_123:1',
        )
    )

    # Capture a PaymentIntent
    result = StripeAdapter.capture_payment_intent(
        payment_intent_id='pi_xxx',
        idempotency_key='capture:order_123:1',
    )
"""

from __future__ import annotations

import hashlib
import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

import stripe
from django.conf import settings

from payments.exceptions import (
    StripeAPIUnavailableError,
    StripeCardDeclinedError,
    StripeError,
    StripeInsufficientFundsError,
    StripeInvalidAccountError,
    StripeInvalidRequestError,
    StripeRateLimitError,
)

if TYPE_CHECKING:
    pass


# =============================================================================
# Data Types
# =============================================================================


@dataclass
class CreatePaymentIntentParams:
    """
    Parameters for creating a Stripe PaymentIntent.

    Attributes:
        amount_cents: Payment amount in smallest currency unit (e.g., cents)
        currency: ISO 4217 currency code (default: 'usd')
        idempotency_key: Unique key for idempotent creation
        metadata: Key-value pairs to attach to the PaymentIntent
        customer_id: Optional Stripe Customer ID
        payment_method_types: Allowed payment methods (default: ['card'])
        capture_method: 'automatic' or 'manual' (default: 'automatic')
        transfer_data: Connect transfer destination (optional)
    """

    amount_cents: int
    currency: str
    idempotency_key: str
    metadata: dict[str, str] = field(default_factory=dict)
    customer_id: str | None = None
    payment_method_types: list[str] = field(default_factory=lambda: ["card"])
    capture_method: str = "automatic"
    transfer_data: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        """Validate parameters after initialization."""
        if self.amount_cents <= 0:
            raise ValueError("amount_cents must be positive")
        if not self.idempotency_key:
            raise ValueError("idempotency_key is required")
        if not self.currency:
            raise ValueError("currency is required")


@dataclass
class PaymentIntentResult:
    """
    Result from Stripe PaymentIntent operations.

    Attributes:
        id: PaymentIntent ID (pi_xxx)
        status: Current status (requires_payment_method, succeeded, etc.)
        amount_cents: Amount in cents
        currency: Currency code
        client_secret: Secret for client-side confirmation (None after capture)
        captured: Whether payment has been captured
        metadata: Attached metadata
        raw_response: Full Stripe response dict (for debugging)
    """

    id: str
    status: str
    amount_cents: int
    currency: str
    client_secret: str | None = None
    captured: bool = False
    metadata: dict[str, str] = field(default_factory=dict)
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass
class TransferResult:
    """
    Result from Stripe Transfer operations.

    Attributes:
        id: Transfer ID (tr_xxx)
        amount_cents: Amount transferred in cents
        currency: Currency code
        destination_account: Destination Stripe account ID
        metadata: Attached metadata
        raw_response: Full Stripe response dict
    """

    id: str
    amount_cents: int
    currency: str
    destination_account: str
    metadata: dict[str, str] = field(default_factory=dict)
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass
class RefundResult:
    """
    Result from Stripe Refund operations.

    Attributes:
        id: Refund ID (re_xxx)
        amount_cents: Refunded amount in cents
        currency: Currency code
        status: Refund status (succeeded, pending, failed)
        payment_intent_id: Original PaymentIntent ID
        metadata: Attached metadata
        raw_response: Full Stripe response dict
    """

    id: str
    amount_cents: int
    currency: str
    status: str
    payment_intent_id: str
    metadata: dict[str, str] = field(default_factory=dict)
    raw_response: dict[str, Any] = field(default_factory=dict)


# =============================================================================
# Idempotency Key Generator
# =============================================================================


class IdempotencyKeyGenerator:
    """
    Generate idempotency keys for Stripe API calls.

    Format: "{operation}:{entity_id}:{attempt}:{hash}"

    The hash component provides uniqueness across service restarts
    while the structured format aids debugging and correlation.

    Example:
        key = IdempotencyKeyGenerator.generate(
            operation='create_intent',
            entity_id=payment_order.id,
            attempt=1,
        )
        # Result: "create_intent:550e8400-e29b-41d4-a716-446655440000:1:a1b2c3d4"
    """

    @staticmethod
    def generate(
        operation: str,
        entity_id: uuid.UUID | str,
        attempt: int = 1,
    ) -> str:
        """
        Generate a unique idempotency key.

        Args:
            operation: The Stripe operation (create_intent, capture, refund, etc.)
            entity_id: The domain entity ID (payment_order_id, etc.)
            attempt: Attempt number for retries (default: 1)

        Returns:
            Formatted idempotency key string
        """
        entity_str = str(entity_id)
        # Create a short hash for uniqueness using SECRET_KEY
        hash_input = f"{operation}:{entity_str}:{attempt}:{settings.SECRET_KEY}"
        short_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:8]

        return f"{operation}:{entity_str}:{attempt}:{short_hash}"


# =============================================================================
# Retry Logic Helpers
# =============================================================================


def is_retryable_stripe_error(error: Exception) -> bool:
    """
    Check if a Stripe error is retryable.

    Use this in Celery tasks to decide whether to retry:

        @celery_app.task(bind=True, max_retries=3)
        def process_payment(self, payment_id):
            try:
                StripeAdapter.create_payment_intent(...)
            except Exception as e:
                if is_retryable_stripe_error(e):
                    raise self.retry(exc=e, countdown=backoff_delay(self.request.retries))
                raise

    Args:
        error: The exception to check

    Returns:
        True if the error is a transient Stripe error that can be retried
    """
    if isinstance(error, StripeError):
        return getattr(error, "is_retryable", False)
    return False


def backoff_delay(attempt: int, base: float = 1.0, max_delay: float = 60.0) -> float:
    """
    Calculate exponential backoff delay with jitter.

    Jitter prevents thundering herd when multiple workers retry simultaneously.

    Args:
        attempt: Current attempt number (0-indexed)
        base: Base delay in seconds (default: 1.0)
        max_delay: Maximum delay in seconds (default: 60.0)

    Returns:
        Delay in seconds with jitter (0-25% of calculated delay)

    Example:
        # Attempt 0: 1.0 - 1.25 seconds
        # Attempt 1: 2.0 - 2.5 seconds
        # Attempt 2: 4.0 - 5.0 seconds
        delay = backoff_delay(attempt=2)
    """
    delay = min(base * (2**attempt), max_delay)
    # Add jitter (0-25% of delay)
    jitter = delay * random.uniform(0, 0.25)
    return delay + jitter


# =============================================================================
# Stripe Adapter
# =============================================================================


class StripeAdapter:
    """
    Adapter for Stripe API operations.

    All methods are static - no instance state is maintained.
    Thread-safe for use from Celery workers.

    Features:
    - Configurable timeouts on all API calls
    - Automatic error translation to domain exceptions
    - Structured logging with timing metrics
    - Idempotency support for safe retries

    Configuration (via settings):
    - STRIPE_SECRET_KEY: Stripe API secret key
    - STRIPE_API_TIMEOUT_SECONDS: API call timeout (default: 10)
    - STRIPE_MAX_RETRIES: Max retry attempts (default: 3)

    Usage:
        result = StripeAdapter.create_payment_intent(params)
        result = StripeAdapter.capture_payment_intent(pi_id, idem_key)
    """

    # =========================================================================
    # Configuration
    # =========================================================================

    @staticmethod
    def _configure_stripe() -> None:
        """Configure Stripe client with API key and timeout."""
        stripe.api_key = settings.STRIPE_SECRET_KEY
        # Set timeout for this thread's requests
        timeout = getattr(settings, "STRIPE_API_TIMEOUT_SECONDS", 10)
        stripe.default_http_client = stripe.http_client.RequestsClient(timeout=timeout)

    @classmethod
    def get_logger(cls) -> logging.Logger:
        """Get logger for this adapter."""
        return logging.getLogger(f"{cls.__module__}.{cls.__name__}")

    # =========================================================================
    # Core Operations
    # =========================================================================

    @classmethod
    def create_payment_intent(
        cls,
        params: CreatePaymentIntentParams,
        trace_id: str | None = None,
    ) -> PaymentIntentResult:
        """
        Create a Stripe PaymentIntent.

        Args:
            params: Parameters for creating the PaymentIntent
            trace_id: Optional trace ID for distributed tracing

        Returns:
            PaymentIntentResult with PaymentIntent details including client_secret

        Raises:
            StripeCardDeclinedError: Card was declined
            StripeInvalidRequestError: Invalid parameters
            StripeAPIUnavailableError: Stripe service unavailable
            StripeTimeoutError: Request timed out
        """
        cls._configure_stripe()
        logger = cls.get_logger()

        log_context = {
            "operation": "create_payment_intent",
            "amount_cents": params.amount_cents,
            "currency": params.currency,
            "idempotency_key": params.idempotency_key,
            "trace_id": trace_id,
        }

        start_time = time.time()
        logger.info("Starting Stripe operation", extra=log_context)

        try:
            intent = stripe.PaymentIntent.create(
                amount=params.amount_cents,
                currency=params.currency,
                metadata=params.metadata,
                customer=params.customer_id,
                payment_method_types=params.payment_method_types,
                capture_method=params.capture_method,
                transfer_data=params.transfer_data,
                idempotency_key=params.idempotency_key,
            )

            duration_ms = (time.time() - start_time) * 1000
            logger.info(
                "Stripe operation completed",
                extra={
                    **log_context,
                    "payment_intent_id": intent.id,
                    "status": intent.status,
                    "duration_ms": duration_ms,
                },
            )

            return PaymentIntentResult(
                id=intent.id,
                status=intent.status,
                amount_cents=intent.amount,
                currency=intent.currency,
                client_secret=intent.client_secret,
                captured=intent.amount_received > 0,
                metadata=dict(intent.metadata or {}),
                raw_response=intent.to_dict(),
            )

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            cls._handle_stripe_error(e, log_context, duration_ms)
            raise  # Never reached, but satisfies type checker

    @classmethod
    def capture_payment_intent(
        cls,
        payment_intent_id: str,
        idempotency_key: str,
        amount_to_capture: int | None = None,
        trace_id: str | None = None,
    ) -> PaymentIntentResult:
        """
        Capture a PaymentIntent.

        Args:
            payment_intent_id: Stripe PaymentIntent ID (pi_xxx)
            idempotency_key: Unique key for idempotent capture
            amount_to_capture: Optional amount (for partial capture)
            trace_id: Optional trace ID for distributed tracing

        Returns:
            PaymentIntentResult with captured PaymentIntent details

        Raises:
            StripeInvalidRequestError: PaymentIntent not capturable
            StripeAPIUnavailableError: Stripe service unavailable
        """
        cls._configure_stripe()
        logger = cls.get_logger()

        log_context = {
            "operation": "capture_payment_intent",
            "payment_intent_id": payment_intent_id,
            "idempotency_key": idempotency_key,
            "amount_to_capture": amount_to_capture,
            "trace_id": trace_id,
        }

        start_time = time.time()
        logger.info("Starting Stripe operation", extra=log_context)

        try:
            capture_params: dict[str, Any] = {}
            if amount_to_capture is not None:
                capture_params["amount_to_capture"] = amount_to_capture

            intent = stripe.PaymentIntent.capture(
                payment_intent_id,
                idempotency_key=idempotency_key,
                **capture_params,
            )

            duration_ms = (time.time() - start_time) * 1000
            logger.info(
                "Stripe operation completed",
                extra={
                    **log_context,
                    "status": intent.status,
                    "amount_captured": intent.amount_received,
                    "duration_ms": duration_ms,
                },
            )

            return PaymentIntentResult(
                id=intent.id,
                status=intent.status,
                amount_cents=intent.amount,
                currency=intent.currency,
                captured=True,
                metadata=dict(intent.metadata or {}),
                raw_response=intent.to_dict(),
            )

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            cls._handle_stripe_error(e, log_context, duration_ms)
            raise

    @classmethod
    def create_transfer(
        cls,
        amount_cents: int,
        destination_account: str,
        idempotency_key: str,
        currency: str = "usd",
        metadata: dict[str, str] | None = None,
        source_transaction: str | None = None,
        trace_id: str | None = None,
    ) -> TransferResult:
        """
        Create a transfer to a connected Stripe account.

        Args:
            amount_cents: Amount to transfer in cents
            destination_account: Stripe Connect account ID (acct_xxx)
            idempotency_key: Unique key for idempotent transfer
            currency: Currency code (default: 'usd')
            metadata: Optional metadata dict
            source_transaction: Optional source charge/payment_intent
            trace_id: Optional trace ID for distributed tracing

        Returns:
            TransferResult with transfer details

        Raises:
            StripeInvalidAccountError: Invalid destination account
            StripeInsufficientFundsError: Insufficient platform balance
        """
        cls._configure_stripe()
        logger = cls.get_logger()

        log_context = {
            "operation": "create_transfer",
            "amount_cents": amount_cents,
            "destination_account": destination_account,
            "idempotency_key": idempotency_key,
            "trace_id": trace_id,
        }

        start_time = time.time()
        logger.info("Starting Stripe operation", extra=log_context)

        try:
            transfer_params: dict[str, Any] = {
                "amount": amount_cents,
                "currency": currency,
                "destination": destination_account,
                "metadata": metadata or {},
            }
            if source_transaction:
                transfer_params["source_transaction"] = source_transaction

            transfer = stripe.Transfer.create(
                idempotency_key=idempotency_key,
                **transfer_params,
            )

            duration_ms = (time.time() - start_time) * 1000
            logger.info(
                "Stripe operation completed",
                extra={
                    **log_context,
                    "transfer_id": transfer.id,
                    "duration_ms": duration_ms,
                },
            )

            return TransferResult(
                id=transfer.id,
                amount_cents=transfer.amount,
                currency=transfer.currency,
                destination_account=transfer.destination,
                metadata=dict(transfer.metadata or {}),
                raw_response=transfer.to_dict(),
            )

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            cls._handle_stripe_error(e, log_context, duration_ms)
            raise

    @classmethod
    def create_refund(
        cls,
        payment_intent_id: str,
        idempotency_key: str,
        amount_cents: int | None = None,
        reason: str | None = None,
        metadata: dict[str, str] | None = None,
        trace_id: str | None = None,
    ) -> RefundResult:
        """
        Create a refund for a PaymentIntent.

        Args:
            payment_intent_id: Stripe PaymentIntent ID (pi_xxx)
            idempotency_key: Unique key for idempotent refund
            amount_cents: Amount to refund (None for full refund)
            reason: Refund reason (duplicate, fraudulent, requested_by_customer)
            metadata: Optional metadata dict
            trace_id: Optional trace ID for distributed tracing

        Returns:
            RefundResult with refund details

        Raises:
            StripeInvalidRequestError: Refund not possible
        """
        cls._configure_stripe()
        logger = cls.get_logger()

        log_context = {
            "operation": "create_refund",
            "payment_intent_id": payment_intent_id,
            "amount_cents": amount_cents,
            "idempotency_key": idempotency_key,
            "trace_id": trace_id,
        }

        start_time = time.time()
        logger.info("Starting Stripe operation", extra=log_context)

        try:
            refund_params: dict[str, Any] = {
                "payment_intent": payment_intent_id,
                "metadata": metadata or {},
            }
            if amount_cents is not None:
                refund_params["amount"] = amount_cents
            if reason:
                refund_params["reason"] = reason

            refund = stripe.Refund.create(
                idempotency_key=idempotency_key,
                **refund_params,
            )

            duration_ms = (time.time() - start_time) * 1000
            logger.info(
                "Stripe operation completed",
                extra={
                    **log_context,
                    "refund_id": refund.id,
                    "status": refund.status,
                    "duration_ms": duration_ms,
                },
            )

            return RefundResult(
                id=refund.id,
                amount_cents=refund.amount,
                currency=refund.currency,
                status=refund.status,
                payment_intent_id=refund.payment_intent,
                metadata=dict(refund.metadata or {}),
                raw_response=refund.to_dict(),
            )

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            cls._handle_stripe_error(e, log_context, duration_ms)
            raise

    @classmethod
    def retrieve_payment_intent(
        cls,
        payment_intent_id: str,
        trace_id: str | None = None,
    ) -> PaymentIntentResult:
        """
        Retrieve a PaymentIntent by ID.

        Args:
            payment_intent_id: Stripe PaymentIntent ID (pi_xxx)
            trace_id: Optional trace ID for distributed tracing

        Returns:
            PaymentIntentResult with PaymentIntent details

        Raises:
            StripeInvalidRequestError: PaymentIntent not found
        """
        cls._configure_stripe()
        logger = cls.get_logger()

        log_context = {
            "operation": "retrieve_payment_intent",
            "payment_intent_id": payment_intent_id,
            "trace_id": trace_id,
        }

        start_time = time.time()
        logger.debug("Starting Stripe operation", extra=log_context)

        try:
            intent = stripe.PaymentIntent.retrieve(payment_intent_id)

            duration_ms = (time.time() - start_time) * 1000
            logger.debug(
                "Stripe operation completed",
                extra={
                    **log_context,
                    "status": intent.status,
                    "duration_ms": duration_ms,
                },
            )

            return PaymentIntentResult(
                id=intent.id,
                status=intent.status,
                amount_cents=intent.amount,
                currency=intent.currency,
                client_secret=intent.client_secret,
                captured=intent.amount_received > 0,
                metadata=dict(intent.metadata or {}),
                raw_response=intent.to_dict(),
            )

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            cls._handle_stripe_error(e, log_context, duration_ms)
            raise

    @classmethod
    def list_recent_payment_intents(
        cls,
        created_after: datetime,
        limit: int = 100,
        trace_id: str | None = None,
    ) -> list[PaymentIntentResult]:
        """
        List recent PaymentIntents for reconciliation.

        Args:
            created_after: Only return PaymentIntents created after this time
            limit: Maximum number to return (default: 100, max: 100)
            trace_id: Optional trace ID for distributed tracing

        Returns:
            List of PaymentIntentResult objects
        """
        cls._configure_stripe()
        logger = cls.get_logger()

        created_timestamp = int(created_after.timestamp())

        log_context = {
            "operation": "list_recent_payment_intents",
            "created_after": created_timestamp,
            "limit": limit,
            "trace_id": trace_id,
        }

        start_time = time.time()
        logger.info("Starting Stripe operation", extra=log_context)

        try:
            intents = stripe.PaymentIntent.list(
                created={"gte": created_timestamp},
                limit=min(limit, 100),
            )

            duration_ms = (time.time() - start_time) * 1000
            logger.info(
                "Stripe operation completed",
                extra={
                    **log_context,
                    "count": len(intents.data),
                    "duration_ms": duration_ms,
                },
            )

            return [
                PaymentIntentResult(
                    id=intent.id,
                    status=intent.status,
                    amount_cents=intent.amount,
                    currency=intent.currency,
                    captured=intent.amount_received > 0,
                    metadata=dict(intent.metadata or {}),
                    raw_response=intent.to_dict(),
                )
                for intent in intents.data
            ]

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            cls._handle_stripe_error(e, log_context, duration_ms)
            raise

    @classmethod
    def retrieve_transfer(
        cls,
        transfer_id: str,
        trace_id: str | None = None,
    ) -> TransferResult:
        """
        Retrieve a Transfer by ID.

        Used by the reconciliation service to verify transfer status
        against local Payout records.

        Args:
            transfer_id: Stripe Transfer ID (tr_xxx)
            trace_id: Optional trace ID for distributed tracing

        Returns:
            TransferResult with transfer details including status in raw_response

        Raises:
            StripeInvalidRequestError: Transfer not found
        """
        cls._configure_stripe()
        logger = cls.get_logger()

        log_context = {
            "operation": "retrieve_transfer",
            "transfer_id": transfer_id,
            "trace_id": trace_id,
        }

        start_time = time.time()
        logger.debug("Starting Stripe operation", extra=log_context)

        try:
            transfer = stripe.Transfer.retrieve(transfer_id)

            duration_ms = (time.time() - start_time) * 1000
            logger.debug(
                "Stripe operation completed",
                extra={
                    **log_context,
                    "amount": transfer.amount,
                    "destination": transfer.destination,
                    "duration_ms": duration_ms,
                },
            )

            return TransferResult(
                id=transfer.id,
                amount_cents=transfer.amount,
                currency=transfer.currency,
                destination_account=transfer.destination,
                metadata=dict(transfer.metadata or {}),
                raw_response=transfer.to_dict(),
            )

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            cls._handle_stripe_error(e, log_context, duration_ms)
            raise

    @classmethod
    def list_recent_transfers(
        cls,
        created_after: datetime,
        limit: int = 100,
        trace_id: str | None = None,
    ) -> list[TransferResult]:
        """
        List recent Transfers for reconciliation.

        Used by the reconciliation service to compare local Payout records
        against Stripe's Transfer records.

        Args:
            created_after: Only return Transfers created after this time
            limit: Maximum number to return (default: 100, max: 100)
            trace_id: Optional trace ID for distributed tracing

        Returns:
            List of TransferResult objects
        """
        cls._configure_stripe()
        logger = cls.get_logger()

        created_timestamp = int(created_after.timestamp())

        log_context = {
            "operation": "list_recent_transfers",
            "created_after": created_timestamp,
            "limit": limit,
            "trace_id": trace_id,
        }

        start_time = time.time()
        logger.info("Starting Stripe operation", extra=log_context)

        try:
            transfers = stripe.Transfer.list(
                created={"gte": created_timestamp},
                limit=min(limit, 100),
            )

            duration_ms = (time.time() - start_time) * 1000
            logger.info(
                "Stripe operation completed",
                extra={
                    **log_context,
                    "count": len(transfers.data),
                    "duration_ms": duration_ms,
                },
            )

            return [
                TransferResult(
                    id=transfer.id,
                    amount_cents=transfer.amount,
                    currency=transfer.currency,
                    destination_account=transfer.destination,
                    metadata=dict(transfer.metadata or {}),
                    raw_response=transfer.to_dict(),
                )
                for transfer in transfers.data
            ]

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            cls._handle_stripe_error(e, log_context, duration_ms)
            raise

    # =========================================================================
    # Webhook Verification
    # =========================================================================

    @classmethod
    def verify_webhook_signature(
        cls,
        payload: bytes,
        signature: str,
    ) -> dict[str, Any]:
        """
        Verify and parse a Stripe webhook event.

        Args:
            payload: Raw webhook payload bytes
            signature: Stripe-Signature header value

        Returns:
            Parsed event data dict

        Raises:
            StripeInvalidRequestError: Invalid signature
        """
        try:
            event = stripe.Webhook.construct_event(
                payload,
                signature,
                settings.STRIPE_WEBHOOK_SECRET,
            )
            return event.to_dict()
        except stripe.error.SignatureVerificationError as e:
            raise StripeInvalidRequestError(
                "Invalid webhook signature",
                stripe_code="signature_verification_failed",
                details={"error": str(e)},
            )

    # =========================================================================
    # Error Handling
    # =========================================================================

    @classmethod
    def _handle_stripe_error(
        cls,
        error: Exception,
        log_context: dict[str, Any],
        duration_ms: float,
    ) -> None:
        """
        Translate Stripe exceptions to domain exceptions.

        Maps Stripe SDK errors to appropriate domain exceptions
        with proper error categorization for retry decisions.

        Args:
            error: The Stripe exception
            log_context: Logging context dict
            duration_ms: Operation duration for logging

        Raises:
            StripeCardDeclinedError: Card was declined
            StripeInsufficientFundsError: Insufficient funds
            StripeInvalidAccountError: Invalid Connect account
            StripeInvalidRequestError: Invalid request parameters
            StripeRateLimitError: Rate limited
            StripeAPIUnavailableError: API unavailable
            StripeTimeoutError: Request timed out
        """
        logger = cls.get_logger()

        # Add timing to context
        log_context = {**log_context, "duration_ms": duration_ms}

        if isinstance(error, stripe.error.CardError):
            # Card declined or insufficient funds
            decline_code = getattr(error, "decline_code", None)
            logger.warning(
                "Card error from Stripe",
                extra={**log_context, "decline_code": decline_code},
            )

            if decline_code == "insufficient_funds":
                raise StripeInsufficientFundsError(
                    str(error.user_message or error),
                    stripe_code=error.code,
                    decline_code=decline_code,
                )

            raise StripeCardDeclinedError(
                str(error.user_message or error),
                stripe_code=error.code,
                decline_code=decline_code,
            )

        elif isinstance(error, stripe.error.InvalidRequestError):
            # Invalid parameters or resource not found
            logger.error(
                "Invalid request to Stripe",
                extra={**log_context, "stripe_code": error.code},
            )

            # Check for account-related errors
            if "account" in str(error).lower():
                raise StripeInvalidAccountError(
                    str(error),
                    stripe_code=error.code,
                )

            raise StripeInvalidRequestError(
                str(error),
                stripe_code=error.code,
            )

        elif isinstance(error, stripe.error.RateLimitError):
            # Rate limited - retry with backoff
            logger.warning(
                "Rate limited by Stripe",
                extra=log_context,
            )
            raise StripeRateLimitError(
                "Stripe rate limit exceeded. Please retry.",
                stripe_code="rate_limit",
            )

        elif isinstance(error, stripe.error.APIConnectionError):
            # Network error - retry with backoff
            logger.error(
                "Connection error to Stripe",
                extra=log_context,
                exc_info=True,
            )
            raise StripeAPIUnavailableError(
                "Could not connect to Stripe. Please retry.",
                stripe_code="api_connection_error",
            )

        elif isinstance(error, stripe.error.APIError):
            # Stripe server error - retry with backoff
            logger.error(
                "Stripe API error",
                extra=log_context,
                exc_info=True,
            )
            raise StripeAPIUnavailableError(
                "Stripe service error. Please retry.",
                stripe_code="api_error",
            )

        elif isinstance(error, stripe.error.AuthenticationError):
            # Invalid API key - permanent, operational issue
            logger.critical(
                "Stripe authentication failed - check API key",
                extra=log_context,
            )
            raise StripeInvalidRequestError(
                "Stripe authentication failed",
                stripe_code="authentication_error",
            )

        else:
            # Unknown error - log and wrap
            logger.error(
                f"Unexpected error from Stripe: {type(error).__name__}",
                extra=log_context,
                exc_info=True,
            )
            raise StripeAPIUnavailableError(
                f"Unexpected Stripe error: {error}",
                stripe_code="unknown_error",
            )
