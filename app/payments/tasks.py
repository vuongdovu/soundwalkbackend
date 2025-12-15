"""
Celery tasks for payment processing.

This module provides async tasks for:
- Processing Stripe webhook events
- Retrying failed webhook events
- Periodic cleanup of old/stuck events
- Processing pending payouts
- Retrying failed payouts
- Processing expired escrow holds

Usage:
    from payments.tasks import process_webhook_event

    # Queue a webhook for async processing
    process_webhook_event.delay(webhook_event_id)

    # Queue a payout for execution
    from payments.tasks import execute_single_payout
    execute_single_payout.delay(str(payout_id))

    # Process all pending payouts (typically via celery-beat)
    from payments.tasks import process_pending_payouts
    process_pending_payouts.delay()
"""

from __future__ import annotations

import logging
from datetime import timedelta
from uuid import UUID

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from payments.models import WebhookEvent
from payments.state_machines import WebhookEventStatus

logger = logging.getLogger(__name__)


# =============================================================================
# Constants
# =============================================================================

MAX_WEBHOOK_RETRIES = 5
STUCK_PROCESSING_THRESHOLD_MINUTES = 30


# =============================================================================
# Webhook Processing Tasks
# =============================================================================


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_kwargs={"max_retries": MAX_WEBHOOK_RETRIES},
    acks_late=True,
)
def process_webhook_event(self, webhook_event_id: str) -> dict:
    """
    Process a Stripe webhook event asynchronously.

    This task:
    1. Loads the WebhookEvent by ID
    2. Checks if already processed (idempotency)
    3. Marks as processing
    4. Dispatches to appropriate handler
    5. Marks as processed or failed

    Args:
        webhook_event_id: UUID of the WebhookEvent to process

    Returns:
        Dict with processing result status

    Raises:
        Exception: Re-raised to trigger Celery retry mechanism
    """
    # Import here to avoid circular imports
    from payments.webhooks.handlers import dispatch_webhook

    # Convert string ID to UUID if needed
    if isinstance(webhook_event_id, str):
        webhook_event_id = UUID(webhook_event_id)

    logger.info(
        "Processing webhook event",
        extra={"webhook_event_id": str(webhook_event_id)},
    )

    try:
        webhook_event = WebhookEvent.objects.get(id=webhook_event_id)
    except WebhookEvent.DoesNotExist:
        logger.error(
            "WebhookEvent not found",
            extra={"webhook_event_id": str(webhook_event_id)},
        )
        return {"status": "not_found", "webhook_event_id": str(webhook_event_id)}

    # Check if already processed (idempotency)
    if webhook_event.status == WebhookEventStatus.PROCESSED:
        logger.info(
            "WebhookEvent already processed, skipping",
            extra={
                "webhook_event_id": str(webhook_event_id),
                "stripe_event_id": webhook_event.stripe_event_id,
            },
        )
        return {
            "status": "already_processed",
            "webhook_event_id": str(webhook_event_id),
        }

    # Mark as processing
    webhook_event.mark_processing()
    webhook_event.save()

    logger.info(
        f"Dispatching webhook: {webhook_event.event_type}",
        extra={
            "webhook_event_id": str(webhook_event_id),
            "stripe_event_id": webhook_event.stripe_event_id,
            "event_type": webhook_event.event_type,
            "retry_count": webhook_event.retry_count,
        },
    )

    try:
        with transaction.atomic():
            result = dispatch_webhook(webhook_event)

        if result.success:
            webhook_event.mark_processed()
            webhook_event.save()
            logger.info(
                "Webhook processed successfully",
                extra={
                    "webhook_event_id": str(webhook_event_id),
                    "stripe_event_id": webhook_event.stripe_event_id,
                },
            )
            return {
                "status": "processed",
                "webhook_event_id": str(webhook_event_id),
                "stripe_event_id": webhook_event.stripe_event_id,
            }
        else:
            # Handler returned failure - mark as failed
            error_msg = result.error or "Handler returned failure"
            webhook_event.mark_failed(error_msg)
            webhook_event.save()
            logger.warning(
                f"Webhook handler failed: {error_msg}",
                extra={
                    "webhook_event_id": str(webhook_event_id),
                    "stripe_event_id": webhook_event.stripe_event_id,
                    "error": error_msg,
                    "error_code": result.error_code,
                },
            )
            return {
                "status": "handler_failed",
                "webhook_event_id": str(webhook_event_id),
                "error": error_msg,
            }

    except Exception as e:
        # Unexpected exception - mark as failed and let Celery retry
        error_msg = f"{type(e).__name__}: {str(e)}"
        webhook_event.mark_failed(error_msg)
        webhook_event.save()

        logger.exception(
            "Webhook processing failed with exception",
            extra={
                "webhook_event_id": str(webhook_event_id),
                "stripe_event_id": webhook_event.stripe_event_id,
                "error": error_msg,
            },
        )

        # Re-raise to trigger Celery retry
        raise


@shared_task
def retry_failed_webhooks() -> dict:
    """
    Periodic task to retry failed webhook events.

    Finds failed webhooks that haven't exceeded max retries and
    re-queues them for processing.

    This task should be scheduled via celery-beat, e.g., every 5 minutes.

    Returns:
        Dict with count of webhooks queued for retry
    """
    # Find failed webhooks that can be retried
    failed_webhooks = WebhookEvent.objects.filter(
        status=WebhookEventStatus.FAILED,
        retry_count__lt=MAX_WEBHOOK_RETRIES,
    ).order_by("created_at")[:100]  # Process in batches

    queued_count = 0
    for webhook in failed_webhooks:
        try:
            process_webhook_event.delay(str(webhook.id))
            queued_count += 1
            logger.info(
                "Queued failed webhook for retry",
                extra={
                    "webhook_event_id": str(webhook.id),
                    "stripe_event_id": webhook.stripe_event_id,
                    "retry_count": webhook.retry_count,
                },
            )
        except Exception as e:
            logger.error(
                f"Failed to queue webhook for retry: {e}",
                extra={"webhook_event_id": str(webhook.id)},
            )

    logger.info(
        f"Queued {queued_count} failed webhooks for retry",
        extra={"queued_count": queued_count},
    )

    return {"queued_count": queued_count}


@shared_task
def cleanup_stuck_webhooks() -> dict:
    """
    Periodic task to reset stuck webhooks.

    Finds webhooks that have been in PROCESSING status for too long
    and resets them to FAILED so they can be retried.

    This handles cases where the worker crashed during processing.

    Returns:
        Dict with count of webhooks reset
    """
    threshold = timezone.now() - timedelta(minutes=STUCK_PROCESSING_THRESHOLD_MINUTES)

    # Find webhooks stuck in PROCESSING state
    stuck_webhooks = WebhookEvent.objects.filter(
        status=WebhookEventStatus.PROCESSING,
        updated_at__lt=threshold,
    )

    reset_count = 0
    for webhook in stuck_webhooks:
        webhook.mark_failed("Processing timed out - reset for retry")
        webhook.save()
        reset_count += 1
        logger.warning(
            "Reset stuck webhook",
            extra={
                "webhook_event_id": str(webhook.id),
                "stripe_event_id": webhook.stripe_event_id,
                "stuck_since": webhook.updated_at.isoformat(),
            },
        )

    if reset_count > 0:
        logger.info(
            f"Reset {reset_count} stuck webhooks",
            extra={"reset_count": reset_count},
        )

    return {"reset_count": reset_count}


@shared_task
def cleanup_old_webhooks(days: int = 90) -> dict:
    """
    Periodic task to clean up old processed webhook events.

    Removes webhooks older than the specified number of days that
    have been successfully processed. Keeps failed webhooks for
    longer for debugging purposes.

    Args:
        days: Delete processed webhooks older than this many days

    Returns:
        Dict with count of webhooks deleted
    """
    cutoff = timezone.now() - timedelta(days=days)

    # Only delete successfully processed webhooks
    deleted_count, _ = WebhookEvent.objects.filter(
        status=WebhookEventStatus.PROCESSED,
        processed_at__lt=cutoff,
    ).delete()

    if deleted_count > 0:
        logger.info(
            f"Deleted {deleted_count} old webhook events",
            extra={
                "deleted_count": deleted_count,
                "cutoff_date": cutoff.isoformat(),
            },
        )

    return {"deleted_count": deleted_count}


# =============================================================================
# Re-exported Worker Tasks
# =============================================================================
# These tasks are defined in payments.workers but re-exported here for
# convenience and to ensure Celery autodiscover finds them.

from payments.workers import (  # noqa: E402, F401
    execute_single_payout,
    process_expired_holds,
    process_pending_payouts,
    release_single_hold,
    retry_failed_payouts,
)


# =============================================================================
# Monthly Subscription Payout Task
# =============================================================================

# Default minimum balance required to trigger a payout (in cents)
DEFAULT_MINIMUM_PAYOUT_AMOUNT = 1000  # $10.00


@shared_task
def create_monthly_subscription_payouts(
    minimum_payout_amount: int = DEFAULT_MINIMUM_PAYOUT_AMOUNT,
) -> dict:
    """
    Monthly task to create payouts for subscription revenue.

    Aggregates USER_BALANCE for all recipients with connected accounts and
    creates Payout records for balances exceeding the minimum threshold.

    This task should be scheduled via celery-beat to run on the 1st of
    each month at 00:00 UTC:

        'create-monthly-subscription-payouts': {
            'task': 'payments.tasks.create_monthly_subscription_payouts',
            'schedule': crontab(day_of_month='1', hour='0', minute='0'),
        }

    Flow:
    1. Query all ConnectedAccounts with payouts_enabled=True
    2. For each, get USER_BALANCE via LedgerService
    3. If balance >= minimum_payout_amount, create Payout
    4. PayoutService.execute_payout handles Stripe transfer

    Args:
        minimum_payout_amount: Minimum balance to trigger payout (cents)

    Returns:
        Dict with stats about payouts created
    """
    from django.conf import settings as django_settings

    from payments.ledger import LedgerService
    from payments.ledger.models import AccountType
    from payments.models import ConnectedAccount, Payout
    from payments.state_machines import OnboardingStatus

    # Get threshold from settings or use default
    threshold = getattr(
        django_settings,
        "MINIMUM_PAYOUT_AMOUNT_CENTS",
        minimum_payout_amount,
    )

    logger.info(
        "Starting monthly subscription payout creation",
        extra={"minimum_payout_amount": threshold},
    )

    # Find all connected accounts eligible for payouts
    eligible_accounts = ConnectedAccount.objects.filter(
        onboarding_status=OnboardingStatus.COMPLETE,
        payouts_enabled=True,
    )

    stats = {
        "accounts_checked": 0,
        "payouts_created": 0,
        "total_payout_amount": 0,
        "accounts_below_threshold": 0,
        "errors": 0,
    }

    now = timezone.now()
    period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # Period end is the last day of the previous month
    period_end = period_start - timedelta(days=1)

    for account in eligible_accounts:
        stats["accounts_checked"] += 1

        try:
            # Get USER_BALANCE account if it exists
            balance_account = LedgerService.get_account_by_owner(
                AccountType.USER_BALANCE,
                owner_id=account.profile_id,
                currency="usd",
            )

            if not balance_account:
                # No balance account = no subscription revenue
                logger.debug(
                    "No balance account for connected account",
                    extra={"connected_account_id": str(account.id)},
                )
                continue

            # Get current balance
            balance = LedgerService.get_balance(balance_account.id)

            if balance.cents < threshold:
                stats["accounts_below_threshold"] += 1
                logger.debug(
                    "Balance below threshold",
                    extra={
                        "connected_account_id": str(account.id),
                        "balance_cents": balance.cents,
                        "threshold": threshold,
                    },
                )
                continue

            # Create payout for the full balance
            payout = Payout.objects.create(
                connected_account=account,
                amount_cents=balance.cents,
                currency="usd",
                payment_order=None,  # Aggregated payout, not linked to single order
                metadata={
                    "aggregation_type": "monthly_subscription",
                    "period_start": period_start.isoformat(),
                    "period_end": period_end.isoformat(),
                    "source": "subscription_renewals",
                },
            )

            stats["payouts_created"] += 1
            stats["total_payout_amount"] += balance.cents

            logger.info(
                "Created monthly subscription payout",
                extra={
                    "payout_id": str(payout.id),
                    "connected_account_id": str(account.id),
                    "amount_cents": balance.cents,
                },
            )

            # Queue the payout for execution
            execute_single_payout.delay(str(payout.id))

        except Exception as e:
            stats["errors"] += 1
            logger.error(
                f"Error processing monthly payout for account: {e}",
                extra={
                    "connected_account_id": str(account.id),
                    "error": str(e),
                },
                exc_info=True,
            )

    logger.info(
        "Monthly subscription payout creation completed",
        extra=stats,
    )

    return stats
