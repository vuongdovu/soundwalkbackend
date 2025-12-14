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

from payments.models import PaymentOrder, WebhookEvent
from payments.services import PaymentOrchestrator
from payments.state_machines import PaymentOrderState

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
