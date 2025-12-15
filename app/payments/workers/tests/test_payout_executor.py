"""
Tests for payout_executor worker tasks.

This module tests the Celery tasks that process pending payouts
and execute transfers to connected Stripe accounts.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from django.utils import timezone

from core.services import ServiceResult
from payments.exceptions import (
    LockAcquisitionError,
    StripeAPIUnavailableError,
    StripeRateLimitError,
    StripeTimeoutError,
)
from payments.models import Payout
from payments.services.payout_service import PayoutExecutionResult
from payments.state_machines import (
    OnboardingStatus,
    PaymentStrategyType,
    PayoutState,
)
from payments.tests.factories import (
    ConnectedAccountFactory,
    PaymentOrderFactory,
    PayoutFactory,
    UserFactory,
)
from payments.workers.payout_executor import (
    execute_single_payout,
    process_pending_payouts,
    retry_failed_payouts,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def payer(db):
    """Create a payer user for payment orders."""
    return UserFactory()


@pytest.fixture
def recipient_profile(db):
    """Create a recipient profile with connected account ready for payouts."""
    from authentication.models import Profile

    user = UserFactory()
    profile = Profile.objects.get(user=user)
    ConnectedAccountFactory(
        profile=profile,
        onboarding_status=OnboardingStatus.COMPLETE,
        payouts_enabled=True,
        charges_enabled=True,
    )
    return profile


@pytest.fixture
def payment_order_released(db, payer, recipient_profile):
    """Create a payment order in RELEASED state."""
    order = PaymentOrderFactory(
        payer=payer,
        strategy_type=PaymentStrategyType.ESCROW,
        amount_cents=10000,
        metadata={"recipient_profile_id": str(recipient_profile.pk)},
    )
    order.submit()
    order.save()
    order.process()
    order.save()
    order.capture()
    order.save()
    order.hold()
    order.save()
    order.release()
    order.save()
    return order


@pytest.fixture
def pending_payout(db, payment_order_released, recipient_profile):
    """Create a payout in PENDING state."""
    return PayoutFactory(
        payment_order=payment_order_released,
        connected_account=recipient_profile.connected_account,
        amount_cents=9000,
        state=PayoutState.PENDING,
    )


@pytest.fixture
def scheduled_payout(db, payment_order_released, recipient_profile):
    """Create a payout in PENDING state with a schedule time in the past."""
    return PayoutFactory(
        payment_order=payment_order_released,
        connected_account=recipient_profile.connected_account,
        amount_cents=9000,
        state=PayoutState.PENDING,
        scheduled_for=timezone.now() - timedelta(hours=1),
    )


@pytest.fixture
def future_scheduled_payout(db, payment_order_released, recipient_profile):
    """Create a payout in PENDING state scheduled for the future."""
    return PayoutFactory(
        payment_order=payment_order_released,
        connected_account=recipient_profile.connected_account,
        amount_cents=9000,
        state=PayoutState.PENDING,
        scheduled_for=timezone.now() + timedelta(hours=24),
    )


@pytest.fixture
def processing_payout(db, payment_order_released, recipient_profile):
    """Create a payout in PROCESSING state."""
    payout = PayoutFactory(
        payment_order=payment_order_released,
        connected_account=recipient_profile.connected_account,
        amount_cents=9000,
        stripe_transfer_id="tr_test_processing_123",
    )
    payout.process()
    payout.save()
    return payout


@pytest.fixture
def paid_payout(db, payment_order_released, recipient_profile):
    """Create a payout in PAID state."""
    payout = PayoutFactory(
        payment_order=payment_order_released,
        connected_account=recipient_profile.connected_account,
        amount_cents=9000,
        stripe_transfer_id="tr_test_paid_123",
    )
    payout.process()
    payout.save()
    payout.complete()
    payout.save()
    return payout


@pytest.fixture
def failed_payout_retryable(db, payment_order_released, recipient_profile):
    """Create a failed payout with retryable reason."""
    payout = PayoutFactory(
        payment_order=payment_order_released,
        connected_account=recipient_profile.connected_account,
        amount_cents=9000,
        stripe_transfer_id="tr_test_failed_retry_123",
    )
    payout.process()
    payout.save()
    payout.fail(reason="rate_limit: Too many requests")
    payout.save()
    return payout


@pytest.fixture
def failed_payout_permanent(db, payment_order_released, recipient_profile):
    """Create a failed payout with non-retryable reason."""
    payout = PayoutFactory(
        payment_order=payment_order_released,
        connected_account=recipient_profile.connected_account,
        amount_cents=9000,
        stripe_transfer_id="tr_test_failed_perm_123",
    )
    payout.process()
    payout.save()
    payout.fail(reason="invalid_account: Account is closed")
    payout.save()
    return payout


@pytest.fixture
def multiple_pending_payouts(db, payer, recipient_profile):
    """Create multiple pending payouts for batch processing tests."""
    payouts = []
    for i in range(5):
        order = PaymentOrderFactory(
            payer=payer,
            strategy_type=PaymentStrategyType.ESCROW,
            amount_cents=10000 + (i * 1000),
            metadata={"recipient_profile_id": str(recipient_profile.pk)},
        )
        order.submit()
        order.save()
        order.process()
        order.save()
        order.capture()
        order.save()
        order.hold()
        order.save()
        order.release()
        order.save()

        payout = PayoutFactory(
            payment_order=order,
            connected_account=recipient_profile.connected_account,
            amount_cents=order.amount_cents - 1000,  # After platform fee
            state=PayoutState.PENDING,
        )
        payouts.append(payout)
    return payouts


@pytest.fixture
def mock_celery_task():
    """Mock the Celery task request."""
    mock_request = MagicMock()
    mock_request.retries = 0
    return mock_request


# =============================================================================
# process_pending_payouts Tests
# =============================================================================


@pytest.mark.django_db
class TestProcessPendingPayouts:
    """Tests for the process_pending_payouts periodic task."""

    def test_finds_pending_payouts_only(self, pending_payout, processing_payout):
        """Task should only find payouts in PENDING state."""
        from django.db import models

        pending_payouts = Payout.objects.filter(state=PayoutState.PENDING).filter(
            models.Q(scheduled_for__isnull=True)
            | models.Q(scheduled_for__lte=timezone.now())
        )

        assert pending_payouts.count() == 1
        assert pending_payouts.first().id == pending_payout.id

    def test_finds_scheduled_payouts_in_past(
        self, scheduled_payout, future_scheduled_payout
    ):
        """Task should find payouts scheduled for the past but not future."""
        from django.db import models

        ready_payouts = Payout.objects.filter(state=PayoutState.PENDING).filter(
            models.Q(scheduled_for__isnull=True)
            | models.Q(scheduled_for__lte=timezone.now())
        )

        assert ready_payouts.count() == 1
        assert ready_payouts.first().id == scheduled_payout.id

    def test_queues_tasks_for_pending_payouts(self, pending_payout):
        """Task should queue execute_single_payout for each pending payout."""
        with patch(
            "payments.workers.payout_executor.execute_single_payout"
        ) as mock_task:
            mock_task.delay = MagicMock()

            result = process_pending_payouts()

            assert result["queued_count"] == 1
            mock_task.delay.assert_called_once()
            call_args = mock_task.delay.call_args
            assert call_args[0][0] == str(pending_payout.id)

    def test_queues_multiple_payouts(self, multiple_pending_payouts):
        """Task should queue all pending payouts."""
        with patch(
            "payments.workers.payout_executor.execute_single_payout"
        ) as mock_task:
            mock_task.delay = MagicMock()

            result = process_pending_payouts()

            assert result["queued_count"] == 5
            assert mock_task.delay.call_count == 5

    def test_handles_empty_queue(self, db):
        """Task should handle case with no pending payouts."""
        with patch(
            "payments.workers.payout_executor.execute_single_payout"
        ) as mock_task:
            mock_task.delay = MagicMock()

            result = process_pending_payouts()

            assert result["queued_count"] == 0
            mock_task.delay.assert_not_called()

    def test_continues_on_task_queue_failure(self, multiple_pending_payouts):
        """Task should continue processing if queueing one task fails."""
        with patch(
            "payments.workers.payout_executor.execute_single_payout"
        ) as mock_task:
            # First call fails, others succeed
            mock_task.delay = MagicMock(
                side_effect=[Exception("Queue error")] + [None] * 4
            )

            result = process_pending_payouts()

            # 4 should be queued (first failed)
            assert result["queued_count"] == 4


# =============================================================================
# execute_single_payout Tests
# =============================================================================


@pytest.mark.django_db
class TestExecuteSinglePayout:
    """Tests for the execute_single_payout task."""

    def test_executes_pending_payout_successfully(self, pending_payout):
        """Task should execute pending payout via PayoutService."""
        # Set up the mock payout with a stripe_transfer_id
        pending_payout.stripe_transfer_id = "tr_new_123"
        pending_payout.save()

        # Patch at payment_service module where PayoutService.execute_payout is defined
        with patch(
            "payments.services.payout_service.PayoutService.execute_payout"
        ) as mock_execute:
            mock_execute.return_value = ServiceResult.success(
                PayoutExecutionResult(
                    payout=pending_payout,
                    stripe_transfer_id="tr_new_123",
                )
            )

            # Call the task function directly with apply()
            result = execute_single_payout.apply(
                args=[str(pending_payout.id)], kwargs={"attempt": 1}
            ).get()

            assert result["status"] == "executed"
            assert result["stripe_transfer_id"] == "tr_new_123"
            mock_execute.assert_called_once_with(pending_payout.id, attempt=1)

    def test_handles_invalid_uuid(self, db):
        """Task should handle invalid UUID gracefully."""
        result = execute_single_payout.apply(args=["not-a-valid-uuid"]).get()

        assert result["status"] == "not_found"
        assert "Invalid UUID format" in result["error"]

    def test_handles_payout_not_found(self, db):
        """Task should handle non-existent payout gracefully."""
        result = execute_single_payout.apply(args=[str(uuid4())]).get()

        assert result["status"] == "not_found"

    def test_skips_already_processing_payout(self, processing_payout):
        """Task should skip payouts already in PROCESSING state."""
        result = execute_single_payout.apply(args=[str(processing_payout.id)]).get()

        assert result["status"] == "already_processed"
        assert result["current_state"] == PayoutState.PROCESSING

    def test_skips_already_paid_payout(self, paid_payout):
        """Task should skip payouts already in PAID state."""
        result = execute_single_payout.apply(args=[str(paid_payout.id)]).get()

        assert result["status"] == "already_processed"
        assert result["current_state"] == PayoutState.PAID

    def test_handles_failed_state(self, failed_payout_retryable):
        """Task should report invalid state for FAILED payouts."""
        result = execute_single_payout.apply(
            args=[str(failed_payout_retryable.id)]
        ).get()

        assert result["status"] == "invalid_state"
        assert result["current_state"] == PayoutState.FAILED

    def test_handles_service_failure(self, pending_payout):
        """Task should report failure when service fails."""
        with patch("payments.services.PayoutService") as mock_service:
            mock_service.execute_payout.return_value = ServiceResult.failure(
                "Transfer failed",
                error_code="STRIPE_ERROR",
            )

            result = execute_single_payout.apply(args=[str(pending_payout.id)]).get()

            assert result["status"] == "failed"
            assert result["error_code"] == "STRIPE_ERROR"

    def test_handles_lock_acquisition_failure(self, pending_payout):
        """Task should report lock failure when lock cannot be acquired."""
        with patch("payments.services.PayoutService") as mock_service:
            mock_service.execute_payout.side_effect = LockAcquisitionError(
                "Could not acquire lock"
            )

            result = execute_single_payout.apply(args=[str(pending_payout.id)]).get()

            assert result["status"] == "lock_failed"

    def test_transient_stripe_errors_trigger_autoretry(self, pending_payout):
        """Task should trigger autoretry for transient Stripe errors.

        Note: Celery's autoretry behavior is tested implicitly through
        the task decorator configuration. In synchronous test mode,
        we just verify the exceptions are handled appropriately.
        """
        # Verify task is configured with autoretry_for
        assert StripeRateLimitError in execute_single_payout.autoretry_for
        assert StripeAPIUnavailableError in execute_single_payout.autoretry_for
        assert StripeTimeoutError in execute_single_payout.autoretry_for

        # Verify retry configuration
        assert execute_single_payout.retry_kwargs.get("max_retries") == 5

    def test_handles_unexpected_exception(self, pending_payout):
        """Task should handle unexpected exceptions gracefully."""
        with patch("payments.services.PayoutService") as mock_service:
            mock_service.execute_payout.side_effect = ValueError("Unexpected error")

            result = execute_single_payout.apply(args=[str(pending_payout.id)]).get()

            assert result["status"] == "failed"
            assert result["error_code"] == "UNEXPECTED_ERROR"


# =============================================================================
# retry_failed_payouts Tests
# =============================================================================


@pytest.mark.django_db
class TestRetryFailedPayouts:
    """Tests for the retry_failed_payouts periodic task."""

    def test_finds_failed_payouts(self, failed_payout_retryable, pending_payout):
        """Task should only find payouts in FAILED state."""
        failed_payouts = Payout.objects.filter(state=PayoutState.FAILED)

        assert failed_payouts.count() == 1
        assert failed_payouts.first().id == failed_payout_retryable.id

    def test_retries_payout_with_retryable_reason(self, failed_payout_retryable):
        """Task should retry payouts with retryable failure reasons."""
        with patch(
            "payments.workers.payout_executor.execute_single_payout"
        ) as mock_task:
            mock_task.delay = MagicMock()

            result = retry_failed_payouts()

            assert result["queued_count"] == 1
            assert result["skipped_count"] == 0

            # Verify payout state changed to PENDING
            payout = Payout.objects.get(id=failed_payout_retryable.id)
            assert payout.state == PayoutState.PENDING
            assert payout.metadata.get("retry_count") == 1

    def test_skips_payout_with_permanent_failure(self, failed_payout_permanent):
        """Task should skip payouts with non-retryable failure reasons."""
        with patch(
            "payments.workers.payout_executor.execute_single_payout"
        ) as mock_task:
            mock_task.delay = MagicMock()

            result = retry_failed_payouts()

            assert result["queued_count"] == 0
            assert result["skipped_count"] == 1

            # Payout should remain FAILED
            payout = Payout.objects.get(id=failed_payout_permanent.id)
            assert payout.state == PayoutState.FAILED

    def test_skips_payout_exceeding_max_retries(self, failed_payout_retryable):
        """Task should skip payouts that have exceeded max retry attempts."""
        # Set retry count to max
        failed_payout_retryable.metadata["retry_count"] = 5
        failed_payout_retryable.save()

        with patch(
            "payments.workers.payout_executor.execute_single_payout"
        ) as mock_task:
            mock_task.delay = MagicMock()

            result = retry_failed_payouts()

            assert result["queued_count"] == 0
            assert result["skipped_count"] == 1
            mock_task.delay.assert_not_called()

    def test_increments_retry_count(self, failed_payout_retryable):
        """Task should increment retry count in metadata."""
        # Set initial retry count
        failed_payout_retryable.metadata["retry_count"] = 2
        failed_payout_retryable.save()

        with patch(
            "payments.workers.payout_executor.execute_single_payout"
        ) as mock_task:
            mock_task.delay = MagicMock()

            retry_failed_payouts()

            payout = Payout.objects.get(id=failed_payout_retryable.id)
            assert payout.metadata.get("retry_count") == 3

    def test_handles_empty_queue(self, db):
        """Task should handle case with no failed payouts."""
        with patch(
            "payments.workers.payout_executor.execute_single_payout"
        ) as mock_task:
            mock_task.delay = MagicMock()

            result = retry_failed_payouts()

            assert result["queued_count"] == 0
            assert result["skipped_count"] == 0
            mock_task.delay.assert_not_called()

    def test_handles_concurrent_state_change(self, failed_payout_retryable):
        """Task should handle case where payout state changes during processing."""
        with patch(
            "payments.workers.payout_executor.execute_single_payout"
        ) as mock_task:
            mock_task.delay = MagicMock()

            # First run - will transition to PENDING
            retry_failed_payouts()

            # Now payout is PENDING, run again - should not find it
            result2 = retry_failed_payouts()

            assert result2["queued_count"] == 0
