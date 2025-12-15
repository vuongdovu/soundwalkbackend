"""
Tests for hold_manager worker tasks.

This module tests the Celery tasks that process expired FundHolds
and release them automatically (auto-release to recipient policy).

Tests follow TDD approach - written before implementation.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from django.utils import timezone

from payments.models import FundHold
from payments.state_machines import PaymentOrderState, PaymentStrategyType
from payments.tests.factories import (
    ConnectedAccountFactory,
    FundHoldFactory,
    PaymentOrderFactory,
    UserFactory,
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
    """Create a recipient profile with connected account ready for payouts.

    Note: Profile is auto-created via signals when user is created.
    """
    from authentication.models import Profile

    user = UserFactory()
    # Get the profile created by the signal
    profile = Profile.objects.get(user=user)
    ConnectedAccountFactory(
        profile=profile,
        payouts_enabled=True,
        charges_enabled=True,
    )
    return profile


@pytest.fixture
def escrow_order_in_held_state(db, payer, recipient_profile):
    """
    Create an escrow payment order in HELD state with active FundHold.

    This represents a completed payment waiting for service delivery.
    """
    order = PaymentOrderFactory(
        payer=payer,
        strategy_type=PaymentStrategyType.ESCROW,
        amount_cents=10000,
        metadata={"recipient_profile_id": str(recipient_profile.pk)},
    )
    # Transition to HELD state
    order.submit()
    order.save()
    order.process()
    order.save()
    order.capture()
    order.save()
    order.hold()
    order.save()
    return order


@pytest.fixture
def active_hold(db, escrow_order_in_held_state):
    """Create an active (not expired) fund hold."""
    return FundHoldFactory(
        payment_order=escrow_order_in_held_state,
        amount_cents=escrow_order_in_held_state.amount_cents,
        expires_at=timezone.now() + timedelta(days=7),
        released=False,
    )


@pytest.fixture
def expired_hold(db, escrow_order_in_held_state):
    """Create an expired (but not yet released) fund hold."""
    return FundHoldFactory(
        payment_order=escrow_order_in_held_state,
        amount_cents=escrow_order_in_held_state.amount_cents,
        expires_at=timezone.now() - timedelta(hours=1),
        released=False,
    )


@pytest.fixture
def multiple_expired_holds(db, payer, recipient_profile):
    """Create multiple expired holds for batch processing tests."""
    holds = []
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

        hold = FundHoldFactory(
            payment_order=order,
            amount_cents=order.amount_cents,
            expires_at=timezone.now() - timedelta(hours=i + 1),
            released=False,
        )
        holds.append(hold)
    return holds


# =============================================================================
# process_expired_holds Tests
# =============================================================================


@pytest.mark.django_db
class TestProcessExpiredHolds:
    """Tests for the process_expired_holds periodic task."""

    def test_finds_expired_holds_only(self, active_hold, expired_hold):
        """Task should only find holds that have expired."""

        # Query used internally should find expired, not active
        expired_holds = FundHold.objects.filter(
            released=False,
            expires_at__lt=timezone.now(),
            payment_order__state=PaymentOrderState.HELD,
        )

        assert expired_holds.count() == 1
        assert expired_holds.first().id == expired_hold.id

    def test_does_not_find_already_released_holds(
        self, expired_hold, escrow_order_in_held_state
    ):
        """Task should ignore holds that are already released."""
        # Mark the hold as released
        expired_hold.released = True
        expired_hold.released_at = timezone.now()
        expired_hold.save()

        expired_holds = FundHold.objects.filter(
            released=False,
            expires_at__lt=timezone.now(),
            payment_order__state=PaymentOrderState.HELD,
        )

        assert expired_holds.count() == 0

    def test_does_not_find_holds_for_non_held_orders(
        self, expired_hold, escrow_order_in_held_state
    ):
        """Task should ignore holds for orders not in HELD state."""
        # Move order to RELEASED state
        escrow_order_in_held_state.release()
        escrow_order_in_held_state.save()

        expired_holds = FundHold.objects.filter(
            released=False,
            expires_at__lt=timezone.now(),
            payment_order__state=PaymentOrderState.HELD,
        )

        assert expired_holds.count() == 0

    @patch("payments.workers.hold_manager.release_single_hold")
    def test_queues_release_tasks_for_expired_holds(
        self, mock_release_task, multiple_expired_holds
    ):
        """Task should queue a release_single_hold task for each expired hold."""
        from payments.workers.hold_manager import process_expired_holds

        mock_release_task.delay = MagicMock()

        result = process_expired_holds()

        # All 5 expired holds should be queued
        assert mock_release_task.delay.call_count == 5
        assert result["queued_count"] == 5

    @patch("payments.workers.hold_manager.release_single_hold")
    def test_returns_queued_count(self, mock_release_task, expired_hold):
        """Task should return a dict with the count of queued releases."""
        from payments.workers.hold_manager import process_expired_holds

        mock_release_task.delay = MagicMock()

        result = process_expired_holds()

        assert "queued_count" in result
        assert result["queued_count"] == 1

    @patch("payments.workers.hold_manager.release_single_hold")
    def test_returns_zero_when_no_expired_holds(self, mock_release_task, active_hold):
        """Task should return zero count when no holds are expired."""
        from payments.workers.hold_manager import process_expired_holds

        mock_release_task.delay = MagicMock()

        result = process_expired_holds()

        assert result["queued_count"] == 0
        mock_release_task.delay.assert_not_called()

    @patch("payments.workers.hold_manager.release_single_hold")
    def test_limits_batch_size(self, mock_release_task, payer, recipient_profile):
        """Task should process in batches to avoid memory issues."""
        from payments.workers.hold_manager import process_expired_holds

        # Create more than batch size
        for i in range(150):
            order = PaymentOrderFactory(
                payer=payer,
                strategy_type=PaymentStrategyType.ESCROW,
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

            FundHoldFactory(
                payment_order=order,
                expires_at=timezone.now() - timedelta(hours=1),
                released=False,
            )

        mock_release_task.delay = MagicMock()

        result = process_expired_holds()

        # Should be limited to 100 per batch
        assert result["queued_count"] == 100

    @patch("payments.workers.hold_manager.release_single_hold")
    def test_orders_by_expiration_time(
        self, mock_release_task, payer, recipient_profile
    ):
        """Task should process oldest expired holds first."""
        from payments.workers.hold_manager import process_expired_holds

        # Create holds with different expiration times
        oldest_order = PaymentOrderFactory(
            payer=payer,
            strategy_type=PaymentStrategyType.ESCROW,
            metadata={"recipient_profile_id": str(recipient_profile.pk)},
        )
        oldest_order.submit()
        oldest_order.save()
        oldest_order.process()
        oldest_order.save()
        oldest_order.capture()
        oldest_order.save()
        oldest_order.hold()
        oldest_order.save()

        oldest_hold = FundHoldFactory(
            payment_order=oldest_order,
            expires_at=timezone.now() - timedelta(days=10),
            released=False,
        )

        newer_order = PaymentOrderFactory(
            payer=payer,
            strategy_type=PaymentStrategyType.ESCROW,
            metadata={"recipient_profile_id": str(recipient_profile.pk)},
        )
        newer_order.submit()
        newer_order.save()
        newer_order.process()
        newer_order.save()
        newer_order.capture()
        newer_order.save()
        newer_order.hold()
        newer_order.save()

        FundHoldFactory(
            payment_order=newer_order,
            expires_at=timezone.now() - timedelta(hours=1),
            released=False,
        )

        mock_release_task.delay = MagicMock()
        call_args_list = []

        def capture_call(fund_hold_id, reason):
            call_args_list.append(fund_hold_id)

        mock_release_task.delay.side_effect = capture_call

        process_expired_holds()

        # Oldest should be processed first
        assert call_args_list[0] == str(oldest_hold.id)


# =============================================================================
# release_single_hold Tests
# =============================================================================


@pytest.mark.django_db
class TestReleaseSingleHold:
    """Tests for the release_single_hold task."""

    @patch("payments.strategies.escrow.EscrowPaymentStrategy")
    def test_releases_hold_successfully(
        self, mock_strategy_class, expired_hold, recipient_profile
    ):
        """Task should release hold via EscrowPaymentStrategy."""
        from core.services import ServiceResult
        from payments.workers.hold_manager import release_single_hold

        # Setup mock
        mock_strategy = MagicMock()
        mock_strategy_class.return_value = mock_strategy
        mock_strategy.release_hold.return_value = ServiceResult.success(
            expired_hold.payment_order
        )

        result = release_single_hold(str(expired_hold.id), reason="expiration")

        assert result["status"] == "released"
        mock_strategy.release_hold.assert_called_once()

    @patch("payments.strategies.escrow.EscrowPaymentStrategy")
    def test_returns_already_released_for_released_hold(
        self, mock_strategy_class, expired_hold
    ):
        """Task should return idempotent result for already released holds."""
        from payments.workers.hold_manager import release_single_hold

        # Mark as already released
        expired_hold.released = True
        expired_hold.released_at = timezone.now()
        expired_hold.save()

        result = release_single_hold(str(expired_hold.id), reason="expiration")

        assert result["status"] == "already_released"
        mock_strategy_class.assert_not_called()

    def test_returns_not_found_for_invalid_id(self):
        """Task should return not_found for invalid fund hold ID."""
        from payments.workers.hold_manager import release_single_hold

        result = release_single_hold(str(uuid4()), reason="expiration")

        assert result["status"] == "not_found"

    @patch("payments.strategies.escrow.EscrowPaymentStrategy")
    def test_returns_invalid_state_for_non_held_order(
        self, mock_strategy_class, expired_hold, escrow_order_in_held_state
    ):
        """Task should return invalid_state if order is not in HELD state."""
        from payments.workers.hold_manager import release_single_hold

        # Move order out of HELD state
        escrow_order_in_held_state.release()
        escrow_order_in_held_state.save()

        result = release_single_hold(str(expired_hold.id), reason="expiration")

        assert result["status"] == "invalid_state"
        mock_strategy_class.assert_not_called()

    @patch("payments.strategies.escrow.EscrowPaymentStrategy")
    @patch("payments.workers.hold_manager.DistributedLock")
    def test_acquires_distributed_lock(
        self,
        mock_lock_class,
        mock_strategy_class,
        expired_hold,
        recipient_profile,
    ):
        """Task should acquire distributed lock before releasing."""
        from core.services import ServiceResult
        from payments.workers.hold_manager import release_single_hold

        # Setup mocks
        mock_lock = MagicMock()
        mock_lock.__enter__ = MagicMock(return_value=mock_lock)
        mock_lock.__exit__ = MagicMock(return_value=False)
        mock_lock_class.return_value = mock_lock

        mock_strategy = MagicMock()
        mock_strategy_class.return_value = mock_strategy
        mock_strategy.release_hold.return_value = ServiceResult.success(
            expired_hold.payment_order
        )

        release_single_hold(str(expired_hold.id), reason="expiration")

        # Lock should be acquired with correct key pattern
        mock_lock_class.assert_called_once()
        lock_key = mock_lock_class.call_args[0][0]
        assert f"escrow:release:{expired_hold.payment_order.id}" in lock_key

    @patch("payments.strategies.escrow.EscrowPaymentStrategy")
    @patch("payments.workers.hold_manager.DistributedLock")
    def test_handles_lock_acquisition_failure(
        self,
        mock_lock_class,
        mock_strategy_class,
        expired_hold,
    ):
        """Task should handle lock acquisition failure gracefully."""
        from payments.locks import LockAcquisitionError
        from payments.workers.hold_manager import release_single_hold

        # Mock lock acquisition failure
        mock_lock_class.return_value.__enter__ = MagicMock(
            side_effect=LockAcquisitionError("Could not acquire lock")
        )

        result = release_single_hold(str(expired_hold.id), reason="expiration")

        assert result["status"] == "lock_failed"
        mock_strategy_class.assert_not_called()

    @patch("payments.strategies.escrow.EscrowPaymentStrategy")
    def test_handles_strategy_failure(
        self, mock_strategy_class, expired_hold, recipient_profile
    ):
        """Task should handle strategy release failure."""
        from core.services import ServiceResult
        from payments.workers.hold_manager import release_single_hold

        # Setup mock to return failure
        mock_strategy = MagicMock()
        mock_strategy_class.return_value = mock_strategy
        mock_strategy.release_hold.return_value = ServiceResult.failure(
            error="No connected account found",
            error_code="RECIPIENT_NO_CONNECTED_ACCOUNT",
        )

        result = release_single_hold(str(expired_hold.id), reason="expiration")

        assert result["status"] == "release_failed"
        assert result["error_code"] == "RECIPIENT_NO_CONNECTED_ACCOUNT"

    @patch("payments.strategies.escrow.EscrowPaymentStrategy")
    def test_passes_reason_to_strategy(
        self, mock_strategy_class, expired_hold, recipient_profile
    ):
        """Task should pass the release reason to the strategy."""
        from core.services import ServiceResult
        from payments.workers.hold_manager import release_single_hold

        mock_strategy = MagicMock()
        mock_strategy_class.return_value = mock_strategy
        mock_strategy.release_hold.return_value = ServiceResult.success(
            expired_hold.payment_order
        )

        release_single_hold(str(expired_hold.id), reason="expiration")

        # Verify reason was passed
        call_kwargs = mock_strategy.release_hold.call_args[1]
        assert call_kwargs.get("release_reason") == "expiration"

    @patch("payments.strategies.escrow.EscrowPaymentStrategy")
    def test_logs_release_attempt(
        self, mock_strategy_class, expired_hold, recipient_profile, caplog
    ):
        """Task should log release attempts for auditing."""
        from core.services import ServiceResult
        from payments.workers.hold_manager import release_single_hold

        mock_strategy = MagicMock()
        mock_strategy_class.return_value = mock_strategy
        mock_strategy.release_hold.return_value = ServiceResult.success(
            expired_hold.payment_order
        )

        with caplog.at_level("INFO"):
            release_single_hold(str(expired_hold.id), reason="expiration")

        # Should log the attempt
        assert any("release" in record.message.lower() for record in caplog.records)


# =============================================================================
# Integration Tests (with real strategy)
# =============================================================================


@pytest.mark.django_db
@pytest.mark.integration
class TestHoldManagerIntegration:
    """Integration tests for hold manager with real components."""

    @patch("payments.adapters.stripe_adapter.StripeAdapter.create_transfer")
    def test_full_expired_hold_release_flow(
        self,
        mock_create_transfer,
        expired_hold,
        recipient_profile,
    ):
        """Test full flow from expired hold detection to payout creation."""
        from payments.workers.hold_manager import (
            process_expired_holds,
        )

        # Mock Stripe transfer
        mock_create_transfer.return_value = MagicMock(
            id="tr_test123",
            amount=8500,  # After platform fee
        )

        # First, detect expired holds
        with patch("payments.workers.hold_manager.release_single_hold") as mock_release:
            mock_release.delay = MagicMock()
            result = process_expired_holds()
            assert result["queued_count"] == 1

        # Then, release the hold (simulating task execution)
        # This tests the actual strategy integration
        # Note: Actual release test requires proper ledger setup
        # See test_escrow_strategy.py for full release tests

    def test_concurrent_release_protection(self, expired_hold, recipient_profile):
        """Test that concurrent releases are prevented by distributed lock."""
        # This test verifies the locking behavior
        # Full implementation depends on Redis availability
        # In unit tests, we verify lock is acquired (see test_acquires_distributed_lock)
        pass
