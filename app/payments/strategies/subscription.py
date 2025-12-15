"""
Subscription payment strategy for recurring payments via Stripe Billing.

This strategy handles subscription-based payments where:
1. Subscriber creates a subscription to a recipient
2. Stripe handles recurring billing via invoices
3. Each successful invoice creates a PaymentOrder linked to the Subscription
4. Platform fee is deducted at each renewal
5. Recipient balance accumulates in USER_BALANCE for monthly aggregated payout

State Flow for PaymentOrders:
    DRAFT -> PENDING -> PROCESSING -> CAPTURED -> SETTLED

Ledger Flow on Success:
    Entry 1: Debit EXTERNAL_STRIPE, Credit PLATFORM_ESCROW (full amount)
    Entry 2: Debit PLATFORM_ESCROW, Credit PLATFORM_REVENUE (platform fee)
    Entry 3: Debit PLATFORM_ESCROW, Credit USER_BALANCE (recipient's net amount)

Usage:
    from payments.strategies import SubscriptionPaymentStrategy

    strategy = SubscriptionPaymentStrategy()

    # Create a new subscription
    result = strategy.create_subscription(
        CreateSubscriptionParams(
            payer=subscriber,
            recipient_profile_id=recipient_profile.pk,  # Profile uses user as primary key
            price_id='price_xxx',
            amount_cents=10000,
            currency='usd',
            billing_interval='month',
        )
    )

    # Handle invoice.paid webhook (via webhook handler)
    result = strategy.handle_payment_succeeded(payment_order, event_data)
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone as dt_timezone
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.services import ServiceResult

from payments.adapters import (
    CreateSubscriptionParams as StripeCreateSubscriptionParams,
    IdempotencyKeyGenerator,
    StripeAdapter,
)
from payments.exceptions import StripeError
from payments.ledger.models import AccountType, EntryType
from payments.ledger.services import LedgerService
from payments.ledger.types import RecordEntryParams
from payments.models import PaymentOrder, Subscription
from payments.state_machines import (
    PaymentOrderState,
    SubscriptionState,
)
from payments.strategies.base import (
    CreatePaymentParams,
    PaymentResult,
    PaymentStrategy,
)

if TYPE_CHECKING:
    from authentication.models import User


logger = logging.getLogger(__name__)


# =============================================================================
# Parameter and Result Types
# =============================================================================


@dataclass
class CreateSubscriptionParams:
    """
    Parameters for creating a new subscription.

    Attributes:
        payer: User paying for the subscription
        recipient_profile_id: UUID of the subscription recipient's profile
        price_id: Stripe Price ID (price_xxx)
        amount_cents: Subscription amount in smallest currency unit (e.g., cents)
        currency: ISO 4217 currency code (default: 'usd')
        billing_interval: Billing frequency ('month' or 'year')
        metadata: Arbitrary key-value pairs for extensibility
    """

    payer: User
    recipient_profile_id: uuid.UUID
    price_id: str
    amount_cents: int
    currency: str = "usd"
    billing_interval: str = "month"
    metadata: dict[str, Any] | None = field(default=None)

    def __post_init__(self) -> None:
        """Validate parameters after initialization."""
        if self.amount_cents <= 0:
            raise ValueError("amount_cents must be positive")
        if not self.currency:
            raise ValueError("currency is required")
        if not self.price_id:
            raise ValueError("price_id is required")
        if self.recipient_profile_id is None:
            raise ValueError("recipient_profile_id is required")
        if self.billing_interval not in ("month", "year"):
            raise ValueError("billing_interval must be 'month' or 'year'")


@dataclass
class SubscriptionCreationResult:
    """
    Result from creating a subscription.

    Attributes:
        subscription: The created Subscription instance
        client_secret: Stripe client_secret for frontend payment completion (if any)
    """

    subscription: Subscription
    client_secret: str | None = None


# =============================================================================
# Subscription Payment Strategy
# =============================================================================


class SubscriptionPaymentStrategy(PaymentStrategy):
    """
    Strategy for subscription payments with recurring billing via Stripe.

    Subscription payments differ from direct/escrow payments:
    - Stripe handles the recurring billing via Subscriptions and Invoices
    - Each billing cycle triggers an invoice.paid webhook
    - PaymentOrders are created reactively from webhooks, not proactively
    - Recipient balance accumulates in USER_BALANCE for monthly aggregated payout
    - Platform fee is deducted at each renewal (same 15% as other strategies)

    State Flow for PaymentOrders:
        DRAFT -> PENDING -> PROCESSING -> CAPTURED -> SETTLED

    Subscription States:
        PENDING -> ACTIVE (on first payment)
        ACTIVE -> PAST_DUE (on payment failure)
        PAST_DUE -> ACTIVE (on successful retry)
        ACTIVE/PAST_DUE -> CANCELLED (on cancellation)

    Dependency Injection:
        The Stripe adapter can be injected for testing.
    """

    def __init__(self, stripe_adapter: type | None = None):
        """
        Initialize the strategy with optional Stripe adapter injection.

        Args:
            stripe_adapter: Optional Stripe adapter class for dependency injection.
        """
        self.stripe = stripe_adapter or StripeAdapter

    # =========================================================================
    # Subscription-Specific Methods
    # =========================================================================

    def create_subscription(
        self, params: CreateSubscriptionParams
    ) -> ServiceResult[SubscriptionCreationResult]:
        """
        Create a new subscription with Stripe.

        Steps:
        1. Get or create Stripe Customer for the payer
        2. Create Stripe Subscription with the price
        3. Create local Subscription record in PENDING state
        4. Return the result (invoice.paid webhook will activate it)

        Args:
            params: Subscription creation parameters

        Returns:
            ServiceResult containing SubscriptionCreationResult on success
        """
        logger.info(
            "Creating subscription",
            extra={
                "payer_id": str(params.payer.id),
                "recipient_profile_id": str(params.recipient_profile_id),
                "amount_cents": params.amount_cents,
                "billing_interval": params.billing_interval,
            },
        )

        try:
            with transaction.atomic():
                # Step 1: Get or create Stripe Customer
                customer = self.stripe.get_or_create_customer(
                    email=params.payer.email,
                    user_id=str(params.payer.id),
                )

                # Step 2: Create Stripe Subscription
                idempotency_key = IdempotencyKeyGenerator.generate(
                    operation="create_subscription",
                    entity_id=f"{params.payer.id}:{params.recipient_profile_id}",
                )

                stripe_params = StripeCreateSubscriptionParams(
                    customer_id=customer.id,
                    price_id=params.price_id,
                    idempotency_key=idempotency_key,
                    metadata={
                        "payer_id": str(params.payer.id),
                        "recipient_profile_id": str(params.recipient_profile_id),
                    },
                )

                stripe_subscription = self.stripe.create_subscription(stripe_params)

                # Step 3: Create local Subscription record
                subscription = Subscription.objects.create(
                    payer=params.payer,
                    recipient_profile_id=params.recipient_profile_id,
                    stripe_subscription_id=stripe_subscription.id,
                    stripe_customer_id=customer.id,
                    stripe_price_id=params.price_id,
                    amount_cents=params.amount_cents,
                    currency=params.currency,
                    billing_interval=params.billing_interval,
                    current_period_start=datetime.fromtimestamp(
                        stripe_subscription.current_period_start,
                        tz=dt_timezone.utc,
                    ),
                    current_period_end=datetime.fromtimestamp(
                        stripe_subscription.current_period_end,
                        tz=dt_timezone.utc,
                    ),
                    metadata=params.metadata or {},
                )

                logger.info(
                    "Subscription created",
                    extra={
                        "subscription_id": str(subscription.id),
                        "stripe_subscription_id": stripe_subscription.id,
                        "state": subscription.state,
                    },
                )

                return ServiceResult.success(
                    SubscriptionCreationResult(
                        subscription=subscription,
                        client_secret=None,  # Subscription uses invoice payment
                    )
                )

        except StripeError as e:
            logger.error(
                "Failed to create subscription: Stripe error",
                extra={
                    "error_code": e.error_code,
                    "is_retryable": e.is_retryable,
                },
                exc_info=True,
            )
            return ServiceResult.failure(
                str(e),
                error_code=e.error_code or "STRIPE_ERROR",
            )

        except Exception as e:
            logger.error(
                f"Failed to create subscription: {type(e).__name__}",
                exc_info=True,
            )
            return ServiceResult.failure(
                "An unexpected error occurred while creating the subscription",
                error_code="SUBSCRIPTION_CREATION_ERROR",
            )

    def cancel_subscription(
        self,
        subscription: Subscription,
        cancel_at_period_end: bool = True,
    ) -> ServiceResult[Subscription]:
        """
        Cancel a subscription.

        Args:
            subscription: The Subscription to cancel
            cancel_at_period_end: If True (default), cancel at end of billing period

        Returns:
            ServiceResult containing updated Subscription on success
        """
        logger.info(
            "Cancelling subscription",
            extra={
                "subscription_id": str(subscription.id),
                "cancel_at_period_end": cancel_at_period_end,
            },
        )

        try:
            idempotency_key = IdempotencyKeyGenerator.generate(
                operation="cancel_subscription",
                entity_id=subscription.id,
            )

            self.stripe.cancel_subscription(
                subscription_id=subscription.stripe_subscription_id,
                idempotency_key=idempotency_key,
                cancel_at_period_end=cancel_at_period_end,
            )

            if cancel_at_period_end:
                subscription.cancel_at_period_end = True
            else:
                subscription.cancel()

            subscription.save()

            logger.info(
                "Subscription cancelled",
                extra={
                    "subscription_id": str(subscription.id),
                    "state": subscription.state,
                },
            )

            return ServiceResult.success(subscription)

        except StripeError as e:
            logger.error(
                "Failed to cancel subscription: Stripe error",
                extra={"error_code": e.error_code},
                exc_info=True,
            )
            return ServiceResult.failure(
                str(e),
                error_code=e.error_code or "STRIPE_ERROR",
            )

    # =========================================================================
    # PaymentStrategy Interface Implementation
    # =========================================================================

    def create_payment(
        self, params: CreatePaymentParams
    ) -> ServiceResult[PaymentResult]:
        """
        Create a payment - not used for subscriptions.

        Subscription payments are created reactively via invoice.paid webhooks,
        not proactively like direct/escrow payments.

        For subscription creation, use create_subscription() instead.

        Args:
            params: Payment creation parameters (unused)

        Returns:
            ServiceResult with failure indicating to use create_subscription
        """
        return ServiceResult.failure(
            "Subscription payments are created via webhooks. Use create_subscription() instead.",
            error_code="USE_CREATE_SUBSCRIPTION",
        )

    def handle_payment_succeeded(
        self,
        payment_order: PaymentOrder,
        event_data: dict[str, Any],
    ) -> ServiceResult[PaymentOrder]:
        """
        Process a successful subscription payment.

        Called when invoice.paid webhook handler creates or updates a PaymentOrder.
        Handles state transitions and ledger entries.

        Steps:
        1. Validate current state is PENDING
        2. Transition: PENDING -> PROCESSING -> CAPTURED -> SETTLED
        3. Record ledger entries:
           - Payment received (EXTERNAL_STRIPE -> PLATFORM_ESCROW)
           - Platform fee (PLATFORM_ESCROW -> PLATFORM_REVENUE)
           - Recipient credit (PLATFORM_ESCROW -> USER_BALANCE)
        4. Update subscription state if needed
        5. Save the updated order

        Args:
            payment_order: The PaymentOrder to update
            event_data: Full webhook event data from Stripe

        Returns:
            ServiceResult containing updated PaymentOrder on success
        """
        logger.info(
            "Handling payment success for subscription payment",
            extra={
                "payment_order_id": str(payment_order.id),
                "subscription_id": str(payment_order.subscription_id),
                "current_state": payment_order.state,
            },
        )

        # Validate state
        if payment_order.state != PaymentOrderState.PENDING:
            if payment_order.state in [
                PaymentOrderState.CAPTURED,
                PaymentOrderState.SETTLED,
            ]:
                return ServiceResult.success(payment_order)

            return ServiceResult.failure(
                f"Cannot process payment from state: {payment_order.state}",
                error_code="INVALID_STATE",
            )

        try:
            # State transitions: PENDING -> PROCESSING -> CAPTURED -> SETTLED
            payment_order.process()
            payment_order.capture()
            payment_order.settle_from_captured()
            payment_order.save()

            # Record ledger entries (payment + fee + recipient credit)
            self._record_subscription_payment_ledger_entries(payment_order)

            # Update subscription state if needed
            subscription = payment_order.subscription
            if subscription:
                if subscription.state == SubscriptionState.PENDING:
                    subscription.activate()
                    subscription.save()
                elif subscription.state == SubscriptionState.PAST_DUE:
                    subscription.reactivate()
                    subscription.save()

                # Update subscription tracking fields
                subscription.last_payment_at = timezone.now()
                subscription.last_invoice_id = payment_order.stripe_invoice_id
                subscription.save(update_fields=["last_payment_at", "last_invoice_id"])

            logger.info(
                "Subscription payment settled successfully",
                extra={
                    "payment_order_id": str(payment_order.id),
                    "final_state": payment_order.state,
                },
            )

            return ServiceResult.success(payment_order)

        except Exception as e:
            logger.error(
                f"Failed to process subscription payment success: {type(e).__name__}",
                extra={"payment_order_id": str(payment_order.id)},
                exc_info=True,
            )
            return ServiceResult.failure(
                str(e),
                error_code="PAYMENT_PROCESSING_ERROR",
            )

    def _record_subscription_payment_ledger_entries(
        self, payment_order: PaymentOrder
    ) -> None:
        """
        Record ledger entries for a successful subscription payment.

        Creates three entries atomically:
        1. Payment received: EXTERNAL_STRIPE -> PLATFORM_ESCROW (full amount)
        2. Platform fee: PLATFORM_ESCROW -> PLATFORM_REVENUE (fee amount)
        3. Recipient credit: PLATFORM_ESCROW -> USER_BALANCE (net amount)

        Uses stripe_invoice_id-based idempotency keys to ensure entries are
        only created once, even if webhook is delivered multiple times.

        Args:
            payment_order: The settled PaymentOrder
        """
        subscription = payment_order.subscription
        if not subscription:
            logger.warning(
                "PaymentOrder has no subscription, skipping ledger entries",
                extra={"payment_order_id": str(payment_order.id)},
            )
            return

        # Get or create accounts
        external_account = LedgerService.get_or_create_account(
            AccountType.EXTERNAL_STRIPE,
            owner_id=None,
            currency=payment_order.currency,
            allow_negative=True,
        )

        escrow_account = LedgerService.get_or_create_account(
            AccountType.PLATFORM_ESCROW,
            owner_id=None,
            currency=payment_order.currency,
        )

        revenue_account = LedgerService.get_or_create_account(
            AccountType.PLATFORM_REVENUE,
            owner_id=None,
            currency=payment_order.currency,
        )

        # Recipient's USER_BALANCE account
        recipient_balance_account = LedgerService.get_or_create_account(
            AccountType.USER_BALANCE,
            owner_id=subscription.recipient_profile_id,
            currency=payment_order.currency,
        )

        # Calculate platform fee and recipient's net
        platform_fee = self.calculate_platform_fee(payment_order.amount_cents)
        recipient_net = payment_order.amount_cents - platform_fee

        # Use invoice ID for idempotency (unique per payment)
        invoice_id = payment_order.stripe_invoice_id or str(payment_order.id)

        entries = []

        # Entry 1: Payment received from Stripe
        entries.append(
            RecordEntryParams(
                debit_account_id=external_account.id,
                credit_account_id=escrow_account.id,
                amount_cents=payment_order.amount_cents,
                entry_type=EntryType.PAYMENT_RECEIVED,
                idempotency_key=f"subscription:{invoice_id}:received",
                reference_type="payment_order",
                reference_id=payment_order.id,
                description=f"Subscription payment received for order {payment_order.id}",
                created_by="subscription_payment_strategy",
            )
        )

        # Entry 2: Platform fee collection
        if platform_fee > 0:
            entries.append(
                RecordEntryParams(
                    debit_account_id=escrow_account.id,
                    credit_account_id=revenue_account.id,
                    amount_cents=platform_fee,
                    entry_type=EntryType.FEE_COLLECTED,
                    idempotency_key=f"subscription:{invoice_id}:fee",
                    reference_type="payment_order",
                    reference_id=payment_order.id,
                    description=f"Platform fee for subscription order {payment_order.id}",
                    created_by="subscription_payment_strategy",
                )
            )

        # Entry 3: Recipient credit (release to USER_BALANCE)
        if recipient_net > 0:
            entries.append(
                RecordEntryParams(
                    debit_account_id=escrow_account.id,
                    credit_account_id=recipient_balance_account.id,
                    amount_cents=recipient_net,
                    entry_type=EntryType.PAYMENT_RELEASED,
                    idempotency_key=f"subscription:{invoice_id}:release",
                    reference_type="payment_order",
                    reference_id=payment_order.id,
                    description=f"Recipient credit for subscription order {payment_order.id}",
                    created_by="subscription_payment_strategy",
                )
            )

        LedgerService.record_entries(entries)

        logger.info(
            "Ledger entries recorded for subscription payment",
            extra={
                "payment_order_id": str(payment_order.id),
                "subscription_id": str(subscription.id),
                "amount_cents": payment_order.amount_cents,
                "platform_fee": platform_fee,
                "recipient_net": recipient_net,
                "entries_count": len(entries),
            },
        )

    def handle_payment_failed(
        self,
        payment_order: PaymentOrder,
        event_data: dict[str, Any],
        reason: str,
    ) -> ServiceResult[PaymentOrder]:
        """
        Process a failed subscription payment.

        For subscription payments, this is primarily handled by the subscription
        state machine (mark_past_due). Individual PaymentOrders for failed invoices
        may not exist if the payment failed before the invoice was finalized.

        If a PaymentOrder exists, it is marked as FAILED.

        Args:
            payment_order: The PaymentOrder to update
            event_data: Full webhook event data from Stripe
            reason: Human-readable failure reason

        Returns:
            ServiceResult containing updated PaymentOrder on success
        """
        logger.info(
            "Handling payment failure for subscription payment",
            extra={
                "payment_order_id": str(payment_order.id),
                "current_state": payment_order.state,
                "reason": reason,
            },
        )

        # If already failed, return success (idempotent)
        if payment_order.state == PaymentOrderState.FAILED:
            return ServiceResult.success(payment_order)

        if payment_order.state == PaymentOrderState.PENDING:
            try:
                payment_order.process()
                payment_order.fail(reason=reason)
                payment_order.save()

                # Mark subscription as past_due if active
                subscription = payment_order.subscription
                if subscription and subscription.state == SubscriptionState.ACTIVE:
                    subscription.mark_past_due()
                    subscription.save()

                logger.info(
                    "Subscription payment marked as failed",
                    extra={
                        "payment_order_id": str(payment_order.id),
                        "final_state": payment_order.state,
                        "reason": reason,
                    },
                )

                return ServiceResult.success(payment_order)

            except Exception as e:
                logger.error(
                    f"Failed to process payment failure: {type(e).__name__}",
                    extra={"payment_order_id": str(payment_order.id)},
                    exc_info=True,
                )
                return ServiceResult.failure(
                    str(e),
                    error_code="PAYMENT_FAILURE_PROCESSING_ERROR",
                )

        return ServiceResult.failure(
            f"Cannot fail payment from state: {payment_order.state}",
            error_code="INVALID_STATE",
        )

    def handle_invoice_payment_failed(
        self,
        subscription: Subscription,
        event_data: dict[str, Any],
    ) -> ServiceResult[Subscription]:
        """
        Handle an invoice.payment_failed webhook for a subscription.

        Marks the subscription as PAST_DUE. Stripe Smart Retries will handle
        the retry logic, so we just update the local state.

        Args:
            subscription: The Subscription to update
            event_data: Full webhook event data from Stripe

        Returns:
            ServiceResult containing updated Subscription on success
        """
        logger.info(
            "Handling invoice payment failed for subscription",
            extra={
                "subscription_id": str(subscription.id),
                "stripe_subscription_id": subscription.stripe_subscription_id,
                "current_state": subscription.state,
            },
        )

        # If already past_due or cancelled, return success (idempotent)
        if subscription.state in (
            SubscriptionState.PAST_DUE,
            SubscriptionState.CANCELLED,
        ):
            logger.info(
                "Subscription already in appropriate state, skipping",
                extra={
                    "subscription_id": str(subscription.id),
                    "current_state": subscription.state,
                },
            )
            return ServiceResult.success(subscription)

        try:
            subscription.mark_past_due()
            subscription.save()

            logger.info(
                "Subscription marked as past_due",
                extra={
                    "subscription_id": str(subscription.id),
                    "stripe_subscription_id": subscription.stripe_subscription_id,
                },
            )

            return ServiceResult.success(subscription)

        except Exception as e:
            logger.error(
                f"Failed to mark subscription as past_due: {type(e).__name__}",
                extra={"subscription_id": str(subscription.id)},
                exc_info=True,
            )
            return ServiceResult.failure(
                str(e),
                error_code="SUBSCRIPTION_UPDATE_ERROR",
            )

    def calculate_platform_fee(self, amount_cents: int) -> int:
        """
        Calculate the platform fee for a subscription payment.

        Uses the same PLATFORM_FEE_PERCENT setting as other strategies (default: 15%).

        Args:
            amount_cents: Total payment amount in cents

        Returns:
            Platform fee amount in cents
        """
        fee_percent = getattr(settings, "PLATFORM_FEE_PERCENT", 15)
        return amount_cents * fee_percent // 100
