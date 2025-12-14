"""
Tests for payment Celery tasks.

Tests cover:
- process_webhook_event task
- retry_failed_webhooks task
- cleanup_stuck_webhooks task
- cleanup_old_webhooks task
"""

from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.utils import timezone

from core.services import ServiceResult
from payments.models import WebhookEvent
from payments.state_machines import WebhookEventStatus
from payments.tasks import (
    MAX_WEBHOOK_RETRIES,
    STUCK_PROCESSING_THRESHOLD_MINUTES,
    cleanup_old_webhooks,
    cleanup_stuck_webhooks,
    process_webhook_event,
    retry_failed_webhooks,
)


# =============================================================================
# process_webhook_event Tests
# =============================================================================


class TestProcessWebhookEvent:
    """Tests for the process_webhook_event task."""

    def test_process_pending_event_success(self, db, pending_webhook_event):
        """Should process pending event successfully."""
        with patch("payments.webhooks.handlers.dispatch_webhook") as mock_dispatch:
            mock_dispatch.return_value = ServiceResult.success(None)

            result = process_webhook_event(str(pending_webhook_event.id))

        assert result["status"] == "processed"
        assert result["stripe_event_id"] == pending_webhook_event.stripe_event_id

        pending_webhook_event.refresh_from_db()
        assert pending_webhook_event.status == WebhookEventStatus.PROCESSED
        assert pending_webhook_event.processed_at is not None
        assert pending_webhook_event.retry_count == 1  # Incremented during processing

    def test_skip_already_processed_event(self, db, processed_webhook_event):
        """Should skip already processed events."""
        with patch("payments.webhooks.handlers.dispatch_webhook") as mock_dispatch:
            result = process_webhook_event(str(processed_webhook_event.id))

        assert result["status"] == "already_processed"
        mock_dispatch.assert_not_called()

    def test_event_not_found(self, db):
        """Should handle missing webhook event."""
        fake_id = uuid4()

        result = process_webhook_event(str(fake_id))

        assert result["status"] == "not_found"

    def test_handler_failure_marks_event_failed(self, db, pending_webhook_event):
        """Should mark event as failed if handler returns failure."""
        with patch("payments.webhooks.handlers.dispatch_webhook") as mock_dispatch:
            mock_dispatch.return_value = ServiceResult.failure(
                "Handler error", error_code="HANDLER_ERROR"
            )

            result = process_webhook_event(str(pending_webhook_event.id))

        assert result["status"] == "handler_failed"
        assert "Handler error" in result["error"]

        pending_webhook_event.refresh_from_db()
        assert pending_webhook_event.status == WebhookEventStatus.FAILED
        assert "Handler error" in pending_webhook_event.error_message

    def test_exception_marks_event_failed_and_raises(self, db, pending_webhook_event):
        """Should mark event failed and re-raise for Celery retry."""
        with patch("payments.webhooks.handlers.dispatch_webhook") as mock_dispatch:
            mock_dispatch.side_effect = Exception("Database connection lost")

            with pytest.raises(Exception, match="Database connection lost"):
                process_webhook_event(str(pending_webhook_event.id))

        pending_webhook_event.refresh_from_db()
        assert pending_webhook_event.status == WebhookEventStatus.FAILED
        assert "Database connection lost" in pending_webhook_event.error_message

    def test_increments_retry_count(self, db, failed_webhook_event):
        """Should increment retry count when processing."""
        initial_retry_count = failed_webhook_event.retry_count

        # Reset to pending for processing
        failed_webhook_event.status = WebhookEventStatus.PENDING
        failed_webhook_event.save()

        with patch("payments.webhooks.handlers.dispatch_webhook") as mock_dispatch:
            mock_dispatch.return_value = ServiceResult.success(None)

            process_webhook_event(str(failed_webhook_event.id))

        failed_webhook_event.refresh_from_db()
        assert failed_webhook_event.retry_count == initial_retry_count + 1

    def test_accepts_string_uuid(self, db, pending_webhook_event):
        """Should accept string UUID as parameter."""
        with patch("payments.webhooks.handlers.dispatch_webhook") as mock_dispatch:
            mock_dispatch.return_value = ServiceResult.success(None)

            # Pass as string (how Celery would serialize it)
            result = process_webhook_event(str(pending_webhook_event.id))

        assert result["status"] == "processed"


# =============================================================================
# retry_failed_webhooks Tests
# =============================================================================


class TestRetryFailedWebhooks:
    """Tests for the retry_failed_webhooks task."""

    def test_queues_failed_webhooks_for_retry(self, db, failed_webhook_event):
        """Should queue failed webhooks for retry."""
        with patch("payments.tasks.process_webhook_event.delay") as mock_task:
            result = retry_failed_webhooks()

        assert result["queued_count"] == 1
        mock_task.assert_called_once_with(str(failed_webhook_event.id))

    def test_skips_webhooks_at_max_retries(self, db, failed_webhook_event):
        """Should skip webhooks that have exceeded max retries."""
        failed_webhook_event.retry_count = MAX_WEBHOOK_RETRIES
        failed_webhook_event.save()

        with patch("payments.tasks.process_webhook_event.delay") as mock_task:
            result = retry_failed_webhooks()

        assert result["queued_count"] == 0
        mock_task.assert_not_called()

    def test_only_processes_failed_status(
        self, db, pending_webhook_event, processed_webhook_event
    ):
        """Should only queue webhooks with FAILED status."""
        with patch("payments.tasks.process_webhook_event.delay") as mock_task:
            result = retry_failed_webhooks()

        assert result["queued_count"] == 0
        mock_task.assert_not_called()

    def test_handles_queueing_error(self, db, failed_webhook_event):
        """Should handle errors when queuing individual webhooks."""
        with patch("payments.tasks.process_webhook_event.delay") as mock_task:
            mock_task.side_effect = Exception("Celery error")

            result = retry_failed_webhooks()

        # Should report 0 queued due to error
        assert result["queued_count"] == 0

    def test_processes_multiple_failed_webhooks(self, db):
        """Should queue multiple failed webhooks."""
        # Create multiple failed events
        for i in range(5):
            WebhookEvent.objects.create(
                stripe_event_id=f"evt_batch_failed_{i}",
                event_type="payment_intent.succeeded",
                payload={
                    "id": f"evt_batch_failed_{i}",
                    "type": "payment_intent.succeeded",
                },
                status=WebhookEventStatus.FAILED,
                error_message="Previous failure",
                retry_count=1,
            )

        with patch("payments.tasks.process_webhook_event.delay") as mock_task:
            result = retry_failed_webhooks()

        assert result["queued_count"] == 5
        assert mock_task.call_count == 5


# =============================================================================
# cleanup_stuck_webhooks Tests
# =============================================================================


class TestCleanupStuckWebhooks:
    """Tests for the cleanup_stuck_webhooks task."""

    def test_resets_stuck_processing_webhooks(self, db):
        """Should reset webhooks stuck in PROCESSING."""
        # Create a stuck webhook
        stuck_event = WebhookEvent.objects.create(
            stripe_event_id="evt_stuck_123",
            event_type="payment_intent.succeeded",
            payload={"id": "evt_stuck_123", "type": "payment_intent.succeeded"},
            status=WebhookEventStatus.PROCESSING,
        )

        # Manually backdate the updated_at
        threshold = timezone.now() - timedelta(
            minutes=STUCK_PROCESSING_THRESHOLD_MINUTES + 5
        )
        WebhookEvent.objects.filter(id=stuck_event.id).update(updated_at=threshold)

        result = cleanup_stuck_webhooks()

        assert result["reset_count"] == 1

        stuck_event.refresh_from_db()
        assert stuck_event.status == WebhookEventStatus.FAILED
        assert "timed out" in stuck_event.error_message.lower()

    def test_leaves_recent_processing_webhooks(self, db):
        """Should not reset webhooks still being processed."""
        recent_event = WebhookEvent.objects.create(
            stripe_event_id="evt_recent_processing_123",
            event_type="payment_intent.succeeded",
            payload={
                "id": "evt_recent_processing_123",
                "type": "payment_intent.succeeded",
            },
            status=WebhookEventStatus.PROCESSING,
        )
        # updated_at is now, so it's recent

        result = cleanup_stuck_webhooks()

        assert result["reset_count"] == 0

        recent_event.refresh_from_db()
        assert recent_event.status == WebhookEventStatus.PROCESSING

    def test_only_affects_processing_status(
        self, db, pending_webhook_event, failed_webhook_event
    ):
        """Should only affect webhooks in PROCESSING status."""
        # Backdate these events
        threshold = timezone.now() - timedelta(
            minutes=STUCK_PROCESSING_THRESHOLD_MINUTES + 5
        )
        WebhookEvent.objects.filter(
            id__in=[pending_webhook_event.id, failed_webhook_event.id]
        ).update(updated_at=threshold)

        result = cleanup_stuck_webhooks()

        assert result["reset_count"] == 0


# =============================================================================
# cleanup_old_webhooks Tests
# =============================================================================


class TestCleanupOldWebhooks:
    """Tests for the cleanup_old_webhooks task."""

    def test_deletes_old_processed_webhooks(self, db):
        """Should delete old processed webhooks."""
        old_date = timezone.now() - timedelta(days=100)

        old_event = WebhookEvent.objects.create(
            stripe_event_id="evt_old_processed_123",
            event_type="payment_intent.succeeded",
            payload={"id": "evt_old_processed_123", "type": "payment_intent.succeeded"},
            status=WebhookEventStatus.PROCESSED,
            processed_at=old_date,
        )

        result = cleanup_old_webhooks(days=90)

        assert result["deleted_count"] == 1
        assert not WebhookEvent.objects.filter(id=old_event.id).exists()

    def test_keeps_recent_processed_webhooks(self, db, processed_webhook_event):
        """Should keep recently processed webhooks."""
        result = cleanup_old_webhooks(days=90)

        assert result["deleted_count"] == 0
        assert WebhookEvent.objects.filter(id=processed_webhook_event.id).exists()

    def test_keeps_failed_webhooks(self, db, failed_webhook_event):
        """Should keep failed webhooks for debugging."""
        # Backdate the failed webhook
        old_date = timezone.now() - timedelta(days=100)
        WebhookEvent.objects.filter(id=failed_webhook_event.id).update(
            created_at=old_date,
            updated_at=old_date,
        )

        result = cleanup_old_webhooks(days=90)

        # Should not delete failed webhooks
        assert result["deleted_count"] == 0
        assert WebhookEvent.objects.filter(id=failed_webhook_event.id).exists()

    def test_respects_days_parameter(self, db):
        """Should respect the days parameter."""
        # Create webhook processed 60 days ago
        date_60_days_ago = timezone.now() - timedelta(days=60)

        WebhookEvent.objects.create(
            stripe_event_id="evt_60_days_123",
            event_type="payment_intent.succeeded",
            payload={"id": "evt_60_days_123", "type": "payment_intent.succeeded"},
            status=WebhookEventStatus.PROCESSED,
            processed_at=date_60_days_ago,
        )

        # With 90 day threshold, should keep
        result = cleanup_old_webhooks(days=90)
        assert result["deleted_count"] == 0

        # With 30 day threshold, should delete
        result = cleanup_old_webhooks(days=30)
        assert result["deleted_count"] == 1
