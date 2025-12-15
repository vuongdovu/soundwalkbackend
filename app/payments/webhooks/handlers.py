"""
Webhook event handlers for Stripe events.

This module provides a handler registry and implementations for
processing different Stripe webhook events.

The handler registry allows:
- Clean separation between event routing and handling
- Easy extension for new event types
- Centralized error handling

Usage:
    from payments.webhooks.handlers import dispatch_webhook, register_handler

    # Register a custom handler
    @register_handler("custom.event")
    def handle_custom_event(webhook_event: WebhookEvent) -> ServiceResult:
        ...

    # Dispatch an event to its handler
    result = dispatch_webhook(webhook_event)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

from django.db import transaction

from core.services import ServiceResult

from payments.ledger import LedgerService
from payments.ledger.models import AccountType, EntryType
from payments.ledger.types import RecordEntryParams
from payments.models import (
    ConnectedAccount,
    PaymentOrder,
    Payout,
    Refund,
    Subscription,
    WebhookEvent,
)
from payments.services import PaymentOrchestrator
from payments.state_machines import (
    OnboardingStatus,
    PaymentOrderState,
    PaymentStrategyType,
    PayoutState,
    RefundState,
    SubscriptionState,
)

if TYPE_CHECKING:
    pass


logger = logging.getLogger(__name__)


# =============================================================================
# Handler Registry
# =============================================================================


# Maps event type strings to handler functions
WEBHOOK_HANDLERS: dict[str, Callable[[WebhookEvent], ServiceResult]] = {}


def register_handler(event_type: str) -> Callable:
    """
    Decorator to register a webhook event handler.

    Usage:
        @register_handler("payment_intent.succeeded")
        def handle_payment_succeeded(webhook_event: WebhookEvent) -> ServiceResult:
            ...

    Args:
        event_type: The Stripe event type (e.g., "payment_intent.succeeded")

    Returns:
        Decorator function that registers the handler
    """

    def decorator(func: Callable[[WebhookEvent], ServiceResult]) -> Callable:
        WEBHOOK_HANDLERS[event_type] = func
        logger.debug(f"Registered webhook handler for {event_type}")
        return func

    return decorator


def dispatch_webhook(webhook_event: WebhookEvent) -> ServiceResult:
    """
    Dispatch a webhook event to the appropriate handler.

    Looks up the handler by event type and calls it. If no handler
    is registered, logs a warning and returns success (to avoid
    failing on unknown events).

    Args:
        webhook_event: The WebhookEvent to process

    Returns:
        ServiceResult from the handler, or success if no handler
    """
    handler = WEBHOOK_HANDLERS.get(webhook_event.event_type)

    if not handler:
        logger.info(
            f"No handler registered for event type: {webhook_event.event_type}",
            extra={"stripe_event_id": webhook_event.stripe_event_id},
        )
        return ServiceResult.success(None)

    logger.info(
        f"Dispatching {webhook_event.event_type} to handler",
        extra={"stripe_event_id": webhook_event.stripe_event_id},
    )

    return handler(webhook_event)


# =============================================================================
# Payment Intent Handlers
# =============================================================================


@register_handler("payment_intent.succeeded")
def handle_payment_intent_succeeded(webhook_event: WebhookEvent) -> ServiceResult:
    """
    Handle successful payment confirmation.

    Called when Stripe sends payment_intent.succeeded webhook.
    Looks up the PaymentOrder and delegates to the appropriate strategy.

    Args:
        webhook_event: The WebhookEvent containing the event data

    Returns:
        ServiceResult with success/failure status
    """
    payment_intent_id = webhook_event.get_object_id()

    if not payment_intent_id:
        logger.error(
            "payment_intent.succeeded: Could not extract payment_intent_id",
            extra={"stripe_event_id": webhook_event.stripe_event_id},
        )
        return ServiceResult.failure(
            "Could not extract payment_intent_id from webhook",
            error_code="INVALID_WEBHOOK_PAYLOAD",
        )

    logger.info(
        "Processing payment_intent.succeeded",
        extra={
            "stripe_event_id": webhook_event.stripe_event_id,
            "payment_intent_id": payment_intent_id,
        },
    )

    with transaction.atomic():
        # Lock the PaymentOrder for update
        payment_order = (
            PaymentOrder.objects.select_for_update()
            .filter(stripe_payment_intent_id=payment_intent_id)
            .first()
        )

        if not payment_order:
            logger.warning(
                "PaymentOrder not found for payment_intent_id",
                extra={
                    "payment_intent_id": payment_intent_id,
                    "stripe_event_id": webhook_event.stripe_event_id,
                },
            )
            return ServiceResult.failure(
                f"PaymentOrder not found for intent: {payment_intent_id}",
                error_code="PAYMENT_ORDER_NOT_FOUND",
            )

        # Get the appropriate strategy and handle success
        strategy = PaymentOrchestrator.get_strategy_for_order(payment_order)
        result = strategy.handle_payment_succeeded(payment_order, webhook_event.payload)

        return result


@register_handler("payment_intent.payment_failed")
def handle_payment_intent_failed(webhook_event: WebhookEvent) -> ServiceResult:
    """
    Handle payment failure notification.

    Called when Stripe sends payment_intent.payment_failed webhook.
    Looks up the PaymentOrder and delegates to the appropriate strategy.

    Args:
        webhook_event: The WebhookEvent containing the event data

    Returns:
        ServiceResult with success/failure status
    """
    payment_intent_id = webhook_event.get_object_id()

    if not payment_intent_id:
        logger.error(
            "payment_intent.payment_failed: Could not extract payment_intent_id",
            extra={"stripe_event_id": webhook_event.stripe_event_id},
        )
        return ServiceResult.failure(
            "Could not extract payment_intent_id from webhook",
            error_code="INVALID_WEBHOOK_PAYLOAD",
        )

    # Extract failure reason from payload
    payload = webhook_event.payload
    data_object = payload.get("data", {}).get("object", {})
    last_error = data_object.get("last_payment_error", {})
    reason = last_error.get("message", "Payment failed")

    logger.info(
        "Processing payment_intent.payment_failed",
        extra={
            "stripe_event_id": webhook_event.stripe_event_id,
            "payment_intent_id": payment_intent_id,
            "reason": reason,
        },
    )

    with transaction.atomic():
        # Lock the PaymentOrder for update
        payment_order = (
            PaymentOrder.objects.select_for_update()
            .filter(stripe_payment_intent_id=payment_intent_id)
            .first()
        )

        if not payment_order:
            logger.warning(
                "PaymentOrder not found for payment_intent_id",
                extra={
                    "payment_intent_id": payment_intent_id,
                    "stripe_event_id": webhook_event.stripe_event_id,
                },
            )
            return ServiceResult.failure(
                f"PaymentOrder not found for intent: {payment_intent_id}",
                error_code="PAYMENT_ORDER_NOT_FOUND",
            )

        # Get the appropriate strategy and handle failure
        strategy = PaymentOrchestrator.get_strategy_for_order(payment_order)
        result = strategy.handle_payment_failed(
            payment_order, webhook_event.payload, reason
        )

        return result


@register_handler("payment_intent.canceled")
def handle_payment_intent_canceled(webhook_event: WebhookEvent) -> ServiceResult:
    """
    Handle payment cancellation notification.

    Called when Stripe sends payment_intent.canceled webhook.
    This typically happens when the PaymentIntent expires or is
    explicitly canceled.

    Args:
        webhook_event: The WebhookEvent containing the event data

    Returns:
        ServiceResult with success/failure status
    """
    payment_intent_id = webhook_event.get_object_id()

    if not payment_intent_id:
        logger.error(
            "payment_intent.canceled: Could not extract payment_intent_id",
            extra={"stripe_event_id": webhook_event.stripe_event_id},
        )
        return ServiceResult.failure(
            "Could not extract payment_intent_id from webhook",
            error_code="INVALID_WEBHOOK_PAYLOAD",
        )

    logger.info(
        "Processing payment_intent.canceled",
        extra={
            "stripe_event_id": webhook_event.stripe_event_id,
            "payment_intent_id": payment_intent_id,
        },
    )

    with transaction.atomic():
        payment_order = (
            PaymentOrder.objects.select_for_update()
            .filter(stripe_payment_intent_id=payment_intent_id)
            .first()
        )

        if not payment_order:
            # This is fine - the order might not exist yet or already cleaned up
            logger.info(
                "PaymentOrder not found for canceled intent (OK)",
                extra={"payment_intent_id": payment_intent_id},
            )
            return ServiceResult.success(None)

        # Only cancel if in a cancellable state
        if payment_order.state in [
            PaymentOrderState.DRAFT,
            PaymentOrderState.PENDING,
        ]:
            payment_order.cancel()
            payment_order.save()
            logger.info(
                "PaymentOrder canceled",
                extra={"payment_order_id": str(payment_order.id)},
            )

        return ServiceResult.success(payment_order)


# =============================================================================
# Transfer Handlers (Payout lifecycle)
# =============================================================================


@register_handler("transfer.created")
def handle_transfer_created(webhook_event: WebhookEvent) -> ServiceResult:
    """
    Handle transfer creation confirmation from Stripe.

    Called when Stripe sends transfer.created webhook. This confirms
    that Stripe has accepted the transfer request and queued it for
    processing. Transitions the Payout from PROCESSING to SCHEDULED.

    Args:
        webhook_event: The WebhookEvent containing the event data

    Returns:
        ServiceResult with success/failure status
    """
    transfer_id = webhook_event.get_object_id()

    if not transfer_id:
        logger.error(
            "transfer.created: Could not extract transfer_id",
            extra={"stripe_event_id": webhook_event.stripe_event_id},
        )
        return ServiceResult.failure(
            "Could not extract transfer_id from webhook",
            error_code="INVALID_WEBHOOK_PAYLOAD",
        )

    logger.info(
        "Processing transfer.created",
        extra={
            "stripe_event_id": webhook_event.stripe_event_id,
            "transfer_id": transfer_id,
        },
    )

    with transaction.atomic():
        # Lock the Payout for update
        payout = (
            Payout.objects.select_for_update()
            .filter(stripe_transfer_id=transfer_id)
            .first()
        )

        if not payout:
            # This could be an external transfer or a race condition
            logger.warning(
                "Payout not found for transfer_id (may be external)",
                extra={
                    "transfer_id": transfer_id,
                    "stripe_event_id": webhook_event.stripe_event_id,
                },
            )
            return ServiceResult.success(None)

        # Check current state and transition if appropriate
        if payout.state == PayoutState.PROCESSING:
            payout.mark_scheduled()
            payout.save()
            logger.info(
                "Payout marked as scheduled",
                extra={
                    "payout_id": str(payout.id),
                    "transfer_id": transfer_id,
                },
            )
        elif payout.state in [PayoutState.SCHEDULED, PayoutState.PAID]:
            # Already in a later state - idempotent
            logger.info(
                "Payout already in later state, ignoring transfer.created",
                extra={
                    "payout_id": str(payout.id),
                    "current_state": payout.state,
                },
            )
        else:
            # Wrong state - log warning but don't fail
            logger.warning(
                "Unexpected payout state for transfer.created",
                extra={
                    "payout_id": str(payout.id),
                    "current_state": payout.state,
                    "transfer_id": transfer_id,
                },
            )

        return ServiceResult.success(payout)


@register_handler("transfer.paid")
def handle_transfer_paid(webhook_event: WebhookEvent) -> ServiceResult:
    """
    Handle transfer completion from Stripe.

    Called when Stripe sends transfer.paid webhook. This confirms
    that money has been transferred to the connected account.

    This handler:
    1. Transitions the Payout to PAID state
    2. Records ledger entry: USER_BALANCE[recipient] → EXTERNAL_STRIPE
    3. Transitions PaymentOrder: RELEASED → SETTLED (for escrow payments)

    Args:
        webhook_event: The WebhookEvent containing the event data

    Returns:
        ServiceResult with success/failure status
    """
    transfer_id = webhook_event.get_object_id()

    if not transfer_id:
        logger.error(
            "transfer.paid: Could not extract transfer_id",
            extra={"stripe_event_id": webhook_event.stripe_event_id},
        )
        return ServiceResult.failure(
            "Could not extract transfer_id from webhook",
            error_code="INVALID_WEBHOOK_PAYLOAD",
        )

    logger.info(
        "Processing transfer.paid",
        extra={
            "stripe_event_id": webhook_event.stripe_event_id,
            "transfer_id": transfer_id,
        },
    )

    with transaction.atomic():
        # Lock the Payout for update
        payout = (
            Payout.objects.select_for_update()
            .filter(stripe_transfer_id=transfer_id)
            .first()
        )

        if not payout:
            logger.warning(
                "Payout not found for transfer_id",
                extra={
                    "transfer_id": transfer_id,
                    "stripe_event_id": webhook_event.stripe_event_id,
                },
            )
            return ServiceResult.failure(
                f"Payout not found for transfer: {transfer_id}",
                error_code="PAYOUT_NOT_FOUND",
            )

        # Check current state and transition if appropriate
        if payout.state == PayoutState.PAID:
            # Already paid - idempotent
            logger.info(
                "Payout already paid, ignoring duplicate webhook",
                extra={"payout_id": str(payout.id)},
            )
            return ServiceResult.success(payout)

        if payout.state in [PayoutState.PROCESSING, PayoutState.SCHEDULED]:
            payout.complete()
            payout.save()
            logger.info(
                "Payout completed successfully",
                extra={
                    "payout_id": str(payout.id),
                    "transfer_id": transfer_id,
                    "amount_cents": payout.amount_cents,
                },
            )

            # Record ledger entry for money leaving platform
            _record_payout_completion_ledger_entry(payout)

            # Settle the PaymentOrder if in RELEASED state (escrow path)
            _settle_payment_order_if_released(payout)
        else:
            # Wrong state - log warning but return success for graceful degradation
            logger.warning(
                "Unexpected payout state for transfer.paid",
                extra={
                    "payout_id": str(payout.id),
                    "current_state": payout.state,
                    "transfer_id": transfer_id,
                },
            )

        return ServiceResult.success(payout)


def _record_payout_completion_ledger_entry(payout: Payout) -> None:
    """
    Record ledger entry when payout completes.

    Debits USER_BALANCE[recipient] and credits EXTERNAL_STRIPE to record
    money leaving the platform and going to the recipient's bank account.

    This entry is idempotent - duplicate calls with the same payout ID
    will return the existing entry rather than creating a duplicate.

    Args:
        payout: The completed Payout record
    """
    # Get the recipient's profile ID from the connected account
    recipient_profile_id = payout.connected_account.profile_id

    # Get or create the user's balance account
    user_balance = LedgerService.get_or_create_account(
        AccountType.USER_BALANCE,
        owner_id=recipient_profile_id,
        currency=payout.currency,
    )

    # Get or create the external Stripe account (money leaving platform)
    external_account = LedgerService.get_or_create_account(
        AccountType.EXTERNAL_STRIPE,
        owner_id=None,
        currency=payout.currency,
        allow_negative=True,
    )

    # Record the payout entry
    try:
        LedgerService.record_entry(
            RecordEntryParams(
                debit_account_id=user_balance.id,
                credit_account_id=external_account.id,
                amount_cents=payout.amount_cents,
                entry_type=EntryType.PAYOUT,
                idempotency_key=f"payout:{payout.id}:completion",
                reference_type="payout",
                reference_id=payout.id,
                description=f"Payout to connected account {payout.connected_account.stripe_account_id}",
                created_by="transfer_paid_handler",
            )
        )
        logger.info(
            "Recorded payout completion ledger entry",
            extra={
                "payout_id": str(payout.id),
                "amount_cents": payout.amount_cents,
                "recipient_profile_id": str(recipient_profile_id),
            },
        )
    except Exception as e:
        # Log but don't fail - the payout is already complete
        # Reconciliation will catch any missing ledger entries
        logger.error(
            "Failed to record payout completion ledger entry",
            extra={
                "payout_id": str(payout.id),
                "error": str(e),
            },
            exc_info=True,
        )


def _settle_payment_order_if_released(payout: Payout) -> None:
    """
    Settle the PaymentOrder if it's in RELEASED state.

    For escrow payments, the PaymentOrder goes through:
    HELD → RELEASED (when hold is released) → SETTLED (when payout completes)

    This function handles the final transition to SETTLED.

    Args:
        payout: The completed Payout record
    """
    payment_order = payout.payment_order

    if payment_order.state == PaymentOrderState.RELEASED:
        # Lock and transition to SETTLED
        payment_order = PaymentOrder.objects.select_for_update().get(
            id=payment_order.id
        )

        # Double-check state after locking (could have changed)
        if payment_order.state == PaymentOrderState.RELEASED:
            payment_order.settle_from_released()
            payment_order.save()
            logger.info(
                "PaymentOrder settled after payout completion",
                extra={
                    "payment_order_id": str(payment_order.id),
                    "payout_id": str(payout.id),
                },
            )
    elif payment_order.state == PaymentOrderState.SETTLED:
        # Already settled - this is fine (idempotent)
        logger.info(
            "PaymentOrder already settled",
            extra={"payment_order_id": str(payment_order.id)},
        )


@register_handler("transfer.failed")
def handle_transfer_failed(webhook_event: WebhookEvent) -> ServiceResult:
    """
    Handle transfer failure from Stripe.

    Called when Stripe sends transfer.failed webhook. This indicates
    that the transfer could not be completed. Transitions the Payout
    to FAILED state with the failure reason.

    Args:
        webhook_event: The WebhookEvent containing the event data

    Returns:
        ServiceResult with success/failure status
    """
    transfer_id = webhook_event.get_object_id()

    if not transfer_id:
        logger.error(
            "transfer.failed: Could not extract transfer_id",
            extra={"stripe_event_id": webhook_event.stripe_event_id},
        )
        return ServiceResult.failure(
            "Could not extract transfer_id from webhook",
            error_code="INVALID_WEBHOOK_PAYLOAD",
        )

    # Extract failure reason from payload
    payload = webhook_event.payload
    data_object = payload.get("data", {}).get("object", {})
    failure_code = data_object.get("failure_code", "unknown")
    failure_message = data_object.get("failure_message", "Transfer failed")
    reason = f"{failure_code}: {failure_message}"

    logger.info(
        "Processing transfer.failed",
        extra={
            "stripe_event_id": webhook_event.stripe_event_id,
            "transfer_id": transfer_id,
            "reason": reason,
        },
    )

    with transaction.atomic():
        # Lock the Payout for update
        payout = (
            Payout.objects.select_for_update()
            .filter(stripe_transfer_id=transfer_id)
            .first()
        )

        if not payout:
            logger.warning(
                "Payout not found for transfer_id",
                extra={
                    "transfer_id": transfer_id,
                    "stripe_event_id": webhook_event.stripe_event_id,
                },
            )
            return ServiceResult.failure(
                f"Payout not found for transfer: {transfer_id}",
                error_code="PAYOUT_NOT_FOUND",
            )

        # Check current state and transition if appropriate
        if payout.state == PayoutState.FAILED:
            # Already failed - idempotent
            logger.info(
                "Payout already failed, ignoring duplicate webhook",
                extra={"payout_id": str(payout.id)},
            )
            return ServiceResult.success(payout)

        if payout.state in [PayoutState.PROCESSING, PayoutState.SCHEDULED]:
            payout.fail(reason=reason)
            payout.save()
            logger.info(
                "Payout marked as failed",
                extra={
                    "payout_id": str(payout.id),
                    "transfer_id": transfer_id,
                    "reason": reason,
                },
            )
        else:
            # Wrong state - log warning but return success for graceful degradation
            logger.warning(
                "Unexpected payout state for transfer.failed",
                extra={
                    "payout_id": str(payout.id),
                    "current_state": payout.state,
                    "transfer_id": transfer_id,
                },
            )

        return ServiceResult.success(payout)


# =============================================================================
# Refund Handler
# =============================================================================


@register_handler("charge.refunded")
def handle_charge_refunded(webhook_event: WebhookEvent) -> ServiceResult:
    """
    Handle refund notification from Stripe.

    Called when Stripe sends charge.refunded webhook. This is fired
    when a refund is processed, either initiated through our system
    or directly via the Stripe dashboard.

    This handler:
    1. Finds the PaymentOrder via payment_intent
    2. Creates or updates Refund records for each refund
    3. Updates PaymentOrder state (REFUNDED or PARTIALLY_REFUNDED)

    Args:
        webhook_event: The WebhookEvent containing the event data

    Returns:
        ServiceResult with success/failure status
    """
    payload = webhook_event.payload
    data_object = payload.get("data", {}).get("object", {})

    # Extract charge and payment intent info
    charge_id = data_object.get("id")
    payment_intent_id = data_object.get("payment_intent")
    amount_refunded = data_object.get("amount_refunded", 0)
    amount = data_object.get("amount", 0)
    currency = data_object.get("currency", "usd")
    refunds_data = data_object.get("refunds", {}).get("data", [])

    if not payment_intent_id:
        logger.error(
            "charge.refunded: Could not extract payment_intent",
            extra={
                "stripe_event_id": webhook_event.stripe_event_id,
                "charge_id": charge_id,
            },
        )
        return ServiceResult.failure(
            "Could not extract payment_intent from charge.refunded",
            error_code="INVALID_WEBHOOK_PAYLOAD",
        )

    logger.info(
        "Processing charge.refunded",
        extra={
            "stripe_event_id": webhook_event.stripe_event_id,
            "charge_id": charge_id,
            "payment_intent_id": payment_intent_id,
            "amount_refunded": amount_refunded,
            "total_amount": amount,
        },
    )

    with transaction.atomic():
        # Lock the PaymentOrder for update
        payment_order = (
            PaymentOrder.objects.select_for_update()
            .filter(stripe_payment_intent_id=payment_intent_id)
            .first()
        )

        if not payment_order:
            logger.warning(
                "PaymentOrder not found for charge.refunded",
                extra={
                    "payment_intent_id": payment_intent_id,
                    "stripe_event_id": webhook_event.stripe_event_id,
                },
            )
            return ServiceResult.failure(
                f"PaymentOrder not found for intent: {payment_intent_id}",
                error_code="PAYMENT_ORDER_NOT_FOUND",
            )

        # Process each refund in the list
        for refund_data in refunds_data:
            stripe_refund_id = refund_data.get("id")
            refund_amount = refund_data.get("amount", 0)
            refund_reason = refund_data.get("reason") or "No reason provided"

            if not stripe_refund_id:
                continue

            # Check if Refund record already exists
            existing_refund = Refund.objects.filter(
                stripe_refund_id=stripe_refund_id
            ).first()

            if existing_refund:
                # Update existing refund if not completed
                if existing_refund.state == RefundState.COMPLETED:
                    logger.info(
                        "Refund already completed, skipping",
                        extra={
                            "refund_id": str(existing_refund.id),
                            "stripe_refund_id": stripe_refund_id,
                        },
                    )
                    continue

                # Transition to completed if not already
                if existing_refund.state in [
                    RefundState.REQUESTED,
                    RefundState.PROCESSING,
                ]:
                    if existing_refund.state == RefundState.REQUESTED:
                        existing_refund.process()
                    existing_refund.complete()
                    existing_refund.save()
                    logger.info(
                        "Existing refund marked as completed",
                        extra={
                            "refund_id": str(existing_refund.id),
                            "stripe_refund_id": stripe_refund_id,
                        },
                    )
            else:
                # Create new Refund record for externally-initiated refunds
                # This ensures a complete audit trail
                new_refund = Refund.objects.create(
                    payment_order=payment_order,
                    amount_cents=refund_amount,
                    currency=currency,
                    stripe_refund_id=stripe_refund_id,
                    reason=refund_reason,
                    # Start in REQUESTED, then transition through states
                )
                new_refund.process()
                new_refund.complete()
                new_refund.save()
                logger.info(
                    "Created new refund record from webhook",
                    extra={
                        "refund_id": str(new_refund.id),
                        "stripe_refund_id": stripe_refund_id,
                        "amount_cents": refund_amount,
                    },
                )

        # Update PaymentOrder state based on refund totals
        is_full_refund = amount_refunded >= amount

        if payment_order.state == PaymentOrderState.REFUNDED:
            # Already fully refunded - idempotent
            logger.info(
                "PaymentOrder already refunded, no state change",
                extra={"payment_order_id": str(payment_order.id)},
            )
        elif is_full_refund:
            # Full refund
            if payment_order.state not in [
                PaymentOrderState.REFUNDED,
                PaymentOrderState.CANCELLED,
            ]:
                try:
                    payment_order.refund_full()
                    payment_order.save()
                    logger.info(
                        "PaymentOrder marked as fully refunded",
                        extra={"payment_order_id": str(payment_order.id)},
                    )
                except Exception as e:
                    logger.warning(
                        f"Could not transition to REFUNDED: {e}",
                        extra={
                            "payment_order_id": str(payment_order.id),
                            "current_state": payment_order.state,
                        },
                    )
        else:
            # Partial refund
            if payment_order.state not in [
                PaymentOrderState.PARTIALLY_REFUNDED,
                PaymentOrderState.REFUNDED,
            ]:
                try:
                    payment_order.refund_partial()
                    payment_order.save()
                    logger.info(
                        "PaymentOrder marked as partially refunded",
                        extra={"payment_order_id": str(payment_order.id)},
                    )
                except Exception as e:
                    logger.warning(
                        f"Could not transition to PARTIALLY_REFUNDED: {e}",
                        extra={
                            "payment_order_id": str(payment_order.id),
                            "current_state": payment_order.state,
                        },
                    )

        return ServiceResult.success(payment_order)


# =============================================================================
# Connected Account Handler
# =============================================================================


@register_handler("account.updated")
def handle_account_updated(webhook_event: WebhookEvent) -> ServiceResult:
    """
    Handle connected account updates from Stripe.

    Called when Stripe sends account.updated webhook. This is fired
    when a Connected Account's status changes, such as:
    - Onboarding completion
    - Capability changes (payouts_enabled, charges_enabled)
    - Verification issues

    Args:
        webhook_event: The WebhookEvent containing the event data

    Returns:
        ServiceResult with success/failure status
    """
    payload = webhook_event.payload
    data_object = payload.get("data", {}).get("object", {})

    account_id = data_object.get("id")

    if not account_id:
        logger.error(
            "account.updated: Could not extract account_id",
            extra={"stripe_event_id": webhook_event.stripe_event_id},
        )
        return ServiceResult.failure(
            "Could not extract account_id from webhook",
            error_code="INVALID_WEBHOOK_PAYLOAD",
        )

    # Extract account status fields
    payouts_enabled = data_object.get("payouts_enabled", False)
    charges_enabled = data_object.get("charges_enabled", False)
    requirements = data_object.get("requirements", {})
    currently_due = requirements.get("currently_due", [])
    past_due = requirements.get("past_due", [])
    disabled_reason = requirements.get("disabled_reason")

    logger.info(
        "Processing account.updated",
        extra={
            "stripe_event_id": webhook_event.stripe_event_id,
            "account_id": account_id,
            "payouts_enabled": payouts_enabled,
            "charges_enabled": charges_enabled,
            "requirements_due": len(currently_due) + len(past_due),
        },
    )

    with transaction.atomic():
        # Look up ConnectedAccount (no select_for_update since we use optimistic locking)
        connected_account = ConnectedAccount.objects.filter(
            stripe_account_id=account_id
        ).first()

        if not connected_account:
            # This account is not in our system - could be legitimate
            logger.info(
                "ConnectedAccount not found, may be external account",
                extra={
                    "account_id": account_id,
                    "stripe_event_id": webhook_event.stripe_event_id,
                },
            )
            return ServiceResult.success(None)

        # Update account status
        connected_account.payouts_enabled = payouts_enabled
        connected_account.charges_enabled = charges_enabled

        # Determine onboarding status based on requirements
        if disabled_reason:
            # Account has issues - mark as rejected
            connected_account.onboarding_status = OnboardingStatus.REJECTED
        elif not currently_due and not past_due:
            # No requirements pending - onboarding complete
            connected_account.onboarding_status = OnboardingStatus.COMPLETE
        else:
            # Still has requirements - in progress
            connected_account.onboarding_status = OnboardingStatus.IN_PROGRESS

        connected_account.save()

        logger.info(
            "ConnectedAccount updated",
            extra={
                "connected_account_id": str(connected_account.id),
                "onboarding_status": connected_account.onboarding_status,
                "payouts_enabled": connected_account.payouts_enabled,
                "charges_enabled": connected_account.charges_enabled,
            },
        )

        return ServiceResult.success(connected_account)


# =============================================================================
# Subscription Invoice Handlers
# =============================================================================


@register_handler("invoice.paid")
def handle_invoice_paid(webhook_event: WebhookEvent) -> ServiceResult:
    """
    Handle successful invoice payment for subscriptions.

    Called when Stripe sends invoice.paid webhook for subscription billing.
    This handler:
    1. Skips non-subscription invoices
    2. Finds or creates the PaymentOrder for this invoice
    3. Transitions PaymentOrder through states to SETTLED
    4. Records ledger entries (payment, fee, recipient credit)
    5. Updates subscription state (activates on first payment, reactivates if past_due)

    Args:
        webhook_event: The WebhookEvent containing the event data

    Returns:
        ServiceResult with success/failure status
    """
    from datetime import datetime, timezone as dt_tz

    payload = webhook_event.payload
    data_object = payload.get("data", {}).get("object", {})

    invoice_id = data_object.get("id")
    subscription_id = data_object.get("subscription")
    amount_paid = data_object.get("amount_paid", 0)
    currency = data_object.get("currency", "usd")
    period_start = data_object.get("period_start")
    period_end = data_object.get("period_end")

    # Skip non-subscription invoices
    if not subscription_id:
        logger.info(
            "invoice.paid: Not a subscription invoice, skipping",
            extra={
                "stripe_event_id": webhook_event.stripe_event_id,
                "invoice_id": invoice_id,
            },
        )
        return ServiceResult.success(None)

    logger.info(
        "Processing invoice.paid for subscription",
        extra={
            "stripe_event_id": webhook_event.stripe_event_id,
            "invoice_id": invoice_id,
            "subscription_id": subscription_id,
            "amount_paid": amount_paid,
        },
    )

    with transaction.atomic():
        # Check for idempotency - PaymentOrder with this invoice_id already exists
        existing_order = PaymentOrder.objects.filter(
            stripe_invoice_id=invoice_id
        ).first()

        if existing_order:
            logger.info(
                "PaymentOrder already exists for this invoice (idempotent)",
                extra={
                    "payment_order_id": str(existing_order.id),
                    "invoice_id": invoice_id,
                },
            )
            return ServiceResult.success(existing_order)

        # Find the subscription
        subscription = (
            Subscription.objects.select_for_update()
            .filter(stripe_subscription_id=subscription_id)
            .first()
        )

        if not subscription:
            logger.error(
                "Subscription not found for invoice.paid",
                extra={
                    "stripe_event_id": webhook_event.stripe_event_id,
                    "subscription_id": subscription_id,
                    "invoice_id": invoice_id,
                },
            )
            return ServiceResult.failure(
                f"Subscription not found: {subscription_id}",
                error_code="SUBSCRIPTION_NOT_FOUND",
            )

        # Create PaymentOrder for this invoice
        order = PaymentOrder.objects.create(
            payer=subscription.payer,
            amount_cents=amount_paid,
            currency=currency,
            strategy_type=PaymentStrategyType.SUBSCRIPTION,
            subscription=subscription,
            stripe_invoice_id=invoice_id,
            reference_id=subscription.recipient_profile_id,
            reference_type="subscription",
            metadata={
                "subscription_id": str(subscription.id),
                "billing_reason": data_object.get("billing_reason"),
            },
        )

        # Transition to PENDING
        order.submit()
        order.save()

        # Get strategy and handle success
        strategy = PaymentOrchestrator.get_strategy_for_order(order)
        result = strategy.handle_payment_succeeded(order, payload)

        if not result.success:
            logger.error(
                "Failed to process invoice.paid",
                extra={
                    "payment_order_id": str(order.id),
                    "error": result.error,
                },
            )
            return result

        # Update subscription period dates
        if period_start and period_end:
            subscription.current_period_start = datetime.fromtimestamp(
                period_start, tz=dt_tz.utc
            )
            subscription.current_period_end = datetime.fromtimestamp(
                period_end, tz=dt_tz.utc
            )
            subscription.save(
                update_fields=["current_period_start", "current_period_end"]
            )

        logger.info(
            "invoice.paid processed successfully",
            extra={
                "payment_order_id": str(order.id),
                "subscription_id": str(subscription.id),
                "subscription_state": subscription.state,
            },
        )

        return ServiceResult.success(order)


@register_handler("invoice.payment_failed")
def handle_invoice_payment_failed(webhook_event: WebhookEvent) -> ServiceResult:
    """
    Handle failed invoice payment for subscriptions.

    Called when Stripe sends invoice.payment_failed webhook. This marks
    the subscription as past_due. Stripe Smart Retries will attempt to
    collect payment - we don't implement custom retry logic.

    Args:
        webhook_event: The WebhookEvent containing the event data

    Returns:
        ServiceResult with success/failure status
    """
    payload = webhook_event.payload
    data_object = payload.get("data", {}).get("object", {})

    invoice_id = data_object.get("id")
    subscription_id = data_object.get("subscription")
    attempt_count = data_object.get("attempt_count", 1)

    # Skip non-subscription invoices
    if not subscription_id:
        logger.info(
            "invoice.payment_failed: Not a subscription invoice, skipping",
            extra={
                "stripe_event_id": webhook_event.stripe_event_id,
                "invoice_id": invoice_id,
            },
        )
        return ServiceResult.success(None)

    # Extract failure reason
    last_error = data_object.get("last_finalization_error", {})
    reason = last_error.get("message", "Payment failed")

    logger.info(
        "Processing invoice.payment_failed",
        extra={
            "stripe_event_id": webhook_event.stripe_event_id,
            "invoice_id": invoice_id,
            "subscription_id": subscription_id,
            "attempt_count": attempt_count,
            "reason": reason,
        },
    )

    with transaction.atomic():
        # Find the subscription
        subscription = (
            Subscription.objects.select_for_update()
            .filter(stripe_subscription_id=subscription_id)
            .first()
        )

        if not subscription:
            logger.error(
                "Subscription not found for invoice.payment_failed",
                extra={
                    "stripe_event_id": webhook_event.stripe_event_id,
                    "subscription_id": subscription_id,
                },
            )
            return ServiceResult.failure(
                f"Subscription not found: {subscription_id}",
                error_code="SUBSCRIPTION_NOT_FOUND",
            )

        # Mark subscription as past_due if active
        if subscription.state == SubscriptionState.ACTIVE:
            subscription.mark_past_due()
            subscription.save()
            logger.info(
                "Subscription marked as past_due",
                extra={
                    "subscription_id": str(subscription.id),
                    "attempt_count": attempt_count,
                },
            )
        elif subscription.state == SubscriptionState.PAST_DUE:
            # Already past_due - idempotent
            logger.info(
                "Subscription already past_due",
                extra={"subscription_id": str(subscription.id)},
            )

        return ServiceResult.success(subscription)


# =============================================================================
# Subscription Lifecycle Handlers
# =============================================================================


@register_handler("customer.subscription.created")
def handle_subscription_created(webhook_event: WebhookEvent) -> ServiceResult:
    """
    Handle subscription creation notification.

    Called when Stripe sends customer.subscription.created webhook.
    This can arrive after our create_subscription call or for externally
    created subscriptions.

    For subscriptions we created, this handler updates the local record
    if it exists. For external subscriptions, we log and succeed gracefully.

    Args:
        webhook_event: The WebhookEvent containing the event data

    Returns:
        ServiceResult with success/failure status
    """
    payload = webhook_event.payload
    data_object = payload.get("data", {}).get("object", {})

    subscription_id = data_object.get("id")
    customer_id = data_object.get("customer")
    status = data_object.get("status")

    logger.info(
        "Processing customer.subscription.created",
        extra={
            "stripe_event_id": webhook_event.stripe_event_id,
            "subscription_id": subscription_id,
            "customer_id": customer_id,
            "status": status,
        },
    )

    with transaction.atomic():
        subscription = Subscription.objects.filter(
            stripe_subscription_id=subscription_id
        ).first()

        if not subscription:
            # This could be an external subscription not created through our system
            logger.info(
                "Subscription not found, may be external",
                extra={"subscription_id": subscription_id},
            )
            return ServiceResult.success(None)

        # Update subscription if needed based on Stripe status
        # (We might receive this webhook after our create_subscription call)
        logger.info(
            "Subscription found, webhook acknowledged",
            extra={
                "subscription_id": str(subscription.id),
                "local_state": subscription.state,
                "stripe_status": status,
            },
        )

        return ServiceResult.success(subscription)


@register_handler("customer.subscription.updated")
def handle_subscription_updated(webhook_event: WebhookEvent) -> ServiceResult:
    """
    Handle subscription update notification.

    Called when Stripe sends customer.subscription.updated webhook.
    Syncs important fields from Stripe:
    - cancel_at_period_end flag
    - current_period_start/end dates
    - Status changes

    Args:
        webhook_event: The WebhookEvent containing the event data

    Returns:
        ServiceResult with success/failure status
    """
    from datetime import datetime, timezone as dt_tz

    payload = webhook_event.payload
    data_object = payload.get("data", {}).get("object", {})

    subscription_id = data_object.get("id")
    status = data_object.get("status")
    cancel_at_period_end = data_object.get("cancel_at_period_end", False)
    period_start = data_object.get("current_period_start")
    period_end = data_object.get("current_period_end")
    canceled_at = data_object.get("canceled_at")

    logger.info(
        "Processing customer.subscription.updated",
        extra={
            "stripe_event_id": webhook_event.stripe_event_id,
            "subscription_id": subscription_id,
            "status": status,
            "cancel_at_period_end": cancel_at_period_end,
        },
    )

    with transaction.atomic():
        subscription = (
            Subscription.objects.select_for_update()
            .filter(stripe_subscription_id=subscription_id)
            .first()
        )

        if not subscription:
            logger.info(
                "Subscription not found, may be external",
                extra={"subscription_id": subscription_id},
            )
            return ServiceResult.success(None)

        # Sync cancel_at_period_end
        subscription.cancel_at_period_end = cancel_at_period_end

        # Sync period dates
        if period_start:
            subscription.current_period_start = datetime.fromtimestamp(
                period_start, tz=dt_tz.utc
            )
        if period_end:
            subscription.current_period_end = datetime.fromtimestamp(
                period_end, tz=dt_tz.utc
            )

        # Sync canceled_at if present
        if canceled_at:
            subscription.cancelled_at = datetime.fromtimestamp(
                canceled_at, tz=dt_tz.utc
            )

        subscription.save()

        logger.info(
            "Subscription updated from webhook",
            extra={
                "subscription_id": str(subscription.id),
                "cancel_at_period_end": subscription.cancel_at_period_end,
            },
        )

        return ServiceResult.success(subscription)


@register_handler("customer.subscription.deleted")
def handle_subscription_deleted(webhook_event: WebhookEvent) -> ServiceResult:
    """
    Handle subscription deletion/cancellation notification.

    Called when Stripe sends customer.subscription.deleted webhook.
    This is triggered when:
    - Subscription is immediately canceled
    - Subscription cancels at period end and period has ended
    - Subscription is deleted (max retries exceeded, etc.)

    Transitions the local subscription to CANCELLED state.

    Args:
        webhook_event: The WebhookEvent containing the event data

    Returns:
        ServiceResult with success/failure status
    """
    from datetime import datetime, timezone as dt_tz

    payload = webhook_event.payload
    data_object = payload.get("data", {}).get("object", {})

    subscription_id = data_object.get("id")
    canceled_at = data_object.get("canceled_at")

    logger.info(
        "Processing customer.subscription.deleted",
        extra={
            "stripe_event_id": webhook_event.stripe_event_id,
            "subscription_id": subscription_id,
        },
    )

    with transaction.atomic():
        subscription = (
            Subscription.objects.select_for_update()
            .filter(stripe_subscription_id=subscription_id)
            .first()
        )

        if not subscription:
            logger.info(
                "Subscription not found, may be external or already deleted",
                extra={"subscription_id": subscription_id},
            )
            return ServiceResult.success(None)

        # Check if already cancelled
        if subscription.state == SubscriptionState.CANCELLED:
            logger.info(
                "Subscription already cancelled (idempotent)",
                extra={"subscription_id": str(subscription.id)},
            )
            return ServiceResult.success(subscription)

        # Cancel the subscription
        if subscription.state in [SubscriptionState.ACTIVE, SubscriptionState.PAST_DUE]:
            subscription.cancel()

            # Set cancelled_at from Stripe if provided
            if canceled_at:
                subscription.cancelled_at = datetime.fromtimestamp(
                    canceled_at, tz=dt_tz.utc
                )

            subscription.save()

            logger.info(
                "Subscription cancelled",
                extra={
                    "subscription_id": str(subscription.id),
                    "cancelled_at": str(subscription.cancelled_at),
                },
            )
        elif subscription.state == SubscriptionState.PENDING:
            # Subscription was cancelled before first payment
            subscription.cancel()
            subscription.save()
            logger.info(
                "Pending subscription cancelled",
                extra={"subscription_id": str(subscription.id)},
            )

        return ServiceResult.success(subscription)
