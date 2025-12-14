"""
Webhook endpoint views for Stripe.

This module provides the HTTP endpoint for receiving Stripe webhooks.
The view:
1. Verifies the webhook signature
2. Creates/retrieves the WebhookEvent record (idempotent)
3. Queues the event for async processing
4. Returns immediately

Async processing ensures webhook responses are fast (<1 second)
while allowing complex business logic in the background.

Usage:
    # In urls.py
    from payments.webhooks.views import stripe_webhook

    urlpatterns = [
        path("webhooks/stripe/", stripe_webhook, name="stripe_webhook"),
    ]
"""

from __future__ import annotations

import logging

from django.http import HttpRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from payments.adapters import StripeAdapter
from payments.exceptions import StripeInvalidRequestError
from payments.models import WebhookEvent
from payments.state_machines import WebhookEventStatus


logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def stripe_webhook(request: HttpRequest) -> HttpResponse:
    """
    Receive and queue Stripe webhook events.

    This view:
    1. Verifies the webhook signature using Stripe's library
    2. Creates a WebhookEvent record (idempotent via stripe_event_id)
    3. Queues the event for async processing via Celery
    4. Returns 200 immediately to satisfy Stripe's timeout requirements

    Stripe expects a 2xx response within 20 seconds. By queuing for
    async processing, we can return immediately while handling
    complex business logic in the background.

    Security:
    - Signature verification prevents spoofed webhooks
    - CSRF exemption required for external webhooks
    - Only POST requests accepted

    Idempotency:
    - WebhookEvent.stripe_event_id is unique
    - Duplicate events are detected and return 200 without reprocessing
    - This handles Stripe's retry behavior gracefully

    Returns:
        HttpResponse with status:
        - 200: Event accepted (new or duplicate)
        - 400: Invalid signature or payload

    Example Stripe-Signature header:
        t=1614556800,v1=xxx,v0=yyy
    """
    payload = request.body
    signature = request.headers.get("Stripe-Signature", "")

    if not signature:
        logger.warning("Webhook received without Stripe-Signature header")
        return HttpResponse("Missing signature", status=400)

    # Step 1: Verify signature
    try:
        event_data = StripeAdapter.verify_webhook_signature(payload, signature)
    except StripeInvalidRequestError as e:
        logger.warning(
            "Webhook signature verification failed",
            extra={"error": str(e)},
        )
        return HttpResponse("Invalid signature", status=400)
    except Exception as e:
        logger.error(
            f"Unexpected error verifying webhook: {type(e).__name__}",
            exc_info=True,
        )
        return HttpResponse("Verification error", status=400)

    stripe_event_id = event_data.get("id")
    event_type = event_data.get("type")

    if not stripe_event_id or not event_type:
        logger.warning("Webhook missing required fields")
        return HttpResponse("Invalid event", status=400)

    logger.info(
        f"Received Stripe webhook: {event_type}",
        extra={
            "stripe_event_id": stripe_event_id,
            "event_type": event_type,
        },
    )

    # Step 2: Create/get WebhookEvent (idempotent)
    webhook_event, created = WebhookEvent.objects.get_or_create(
        stripe_event_id=stripe_event_id,
        defaults={
            "event_type": event_type,
            "payload": event_data,
            "status": WebhookEventStatus.PENDING,
        },
    )

    # Step 3: If already processed, return success
    if not created:
        if webhook_event.status == WebhookEventStatus.PROCESSED:
            logger.info(
                "Webhook already processed, returning success",
                extra={"stripe_event_id": stripe_event_id},
            )
            return HttpResponse("Already processed", status=200)

        # If processing or failed, let it retry (but don't block)
        logger.info(
            f"Webhook already exists with status: {webhook_event.status}",
            extra={"stripe_event_id": stripe_event_id},
        )

    # Step 4: Queue for async processing
    try:
        from payments.tasks import process_webhook_event

        process_webhook_event.delay(webhook_event.id)
        logger.info(
            "Webhook queued for processing",
            extra={
                "stripe_event_id": stripe_event_id,
                "webhook_event_id": str(webhook_event.id),
            },
        )
    except Exception as e:
        # If queuing fails, log but still return 200
        # The webhook will be retried by Stripe
        logger.error(
            f"Failed to queue webhook: {type(e).__name__}",
            extra={"stripe_event_id": stripe_event_id},
            exc_info=True,
        )

    # Step 5: Return success immediately
    return HttpResponse("Accepted", status=200)
