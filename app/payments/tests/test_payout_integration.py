"""
Integration tests for the full payout flow.

Tests the complete payout lifecycle:
1. FundHold release creates Payout in PENDING state
2. PayoutService executes payout (PENDING → PROCESSING)
3. Webhook handlers complete the flow (PROCESSING → PAID)
4. Ledger entries are recorded
5. PaymentOrder is settled

These tests verify that all components work together correctly.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from payments.adapters.stripe_adapter import TransferResult
from payments.ledger import LedgerService
from payments.ledger.models import AccountType, EntryType, LedgerEntry
from payments.ledger.types import RecordEntryParams
from payments.models import PaymentOrder, Payout
from payments.services import PayoutService
from payments.state_machines import (
    OnboardingStatus,
    PaymentOrderState,
    PaymentStrategyType,
    PayoutState,
    WebhookEventStatus,
)
from payments.tests.factories import (
    ConnectedAccountFactory,
    PaymentOrderFactory,
    PayoutFactory,
    UserFactory,
)
from payments.webhooks.handlers import handle_transfer_paid


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def payer(db):
    """Create a payer user."""
    return UserFactory()


@pytest.fixture
def recipient_profile(db):
    """Create a recipient profile with connected account ready for payouts."""
    from authentication.models import Profile

    user = UserFactory()
    profile = Profile.objects.get(user=user)
    return profile


@pytest.fixture
def connected_account(db, recipient_profile):
    """Create a connected account ready for payouts."""
    return ConnectedAccountFactory(
        profile=recipient_profile,
        onboarding_status=OnboardingStatus.COMPLETE,
        payouts_enabled=True,
        charges_enabled=True,
    )


@pytest.fixture
def released_escrow_order(db, payer, recipient_profile):
    """Create a PaymentOrder in RELEASED state (escrow flow completed)."""
    order = PaymentOrderFactory(
        payer=payer,
        strategy_type=PaymentStrategyType.ESCROW,
        amount_cents=10000,
        currency="usd",
        metadata={"recipient_profile_id": str(recipient_profile.pk)},
    )
    # Transition through escrow states: DRAFT → PENDING → PROCESSING → CAPTURED → HELD → RELEASED
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
def mock_redis_lock():
    """Mock Redis for distributed locking."""
    mock_redis = MagicMock()
    mock_redis.set.return_value = True
    mock_redis.get.return_value = None
    mock_redis.delete.return_value = 1
    mock_redis.eval.return_value = 1

    with patch("payments.locks.get_redis_connection", return_value=mock_redis):
        yield mock_redis


@pytest.fixture
def seed_user_balance(recipient_profile, connected_account):
    """Seed the user's balance account with funds."""

    def _seed(amount_cents: int, currency: str = "usd"):
        user_balance = LedgerService.get_or_create_account(
            AccountType.USER_BALANCE,
            owner_id=recipient_profile.pk,
            currency=currency,
        )
        external_account = LedgerService.get_or_create_account(
            AccountType.EXTERNAL_STRIPE,
            owner_id=None,
            currency=currency,
            allow_negative=True,
        )
        # Seed balance by recording money coming in
        LedgerService.record_entry(
            RecordEntryParams(
                debit_account_id=external_account.id,
                credit_account_id=user_balance.id,
                amount_cents=amount_cents,
                entry_type=EntryType.PAYMENT_RELEASED,
                idempotency_key=f"test_seed:{uuid4()}",
                description="Test seed for integration test",
                created_by="test",
            )
        )
        return user_balance

    return _seed


# =============================================================================
# Integration Tests: Full Payout Flow
# =============================================================================


@pytest.mark.django_db
class TestPayoutIntegrationFlow:
    """Integration tests for the complete payout lifecycle."""

    def test_full_payout_flow_with_mocked_stripe(
        self,
        released_escrow_order,
        connected_account,
        seed_user_balance,
        mock_redis_lock,
    ):
        """
        Test the complete payout flow from PENDING to PAID.

        Flow:
        1. Create Payout in PENDING state
        2. Execute payout via PayoutService (mocked Stripe)
        3. Verify state is PROCESSING with stripe_transfer_id
        4. Simulate transfer.paid webhook
        5. Verify payout is PAID
        6. Verify ledger entry was created
        7. Verify PaymentOrder is SETTLED
        """
        # Setup: Create payout and seed balance
        payout = PayoutFactory(
            payment_order=released_escrow_order,
            connected_account=connected_account,
            amount_cents=9000,  # After platform fee
            state=PayoutState.PENDING,
        )
        seed_user_balance(9000, "usd")

        # Mock Stripe adapter
        mock_adapter = MagicMock()
        mock_adapter.create_transfer.return_value = TransferResult(
            id="tr_integration_test_123",
            amount_cents=9000,
            currency="usd",
            destination_account=connected_account.stripe_account_id,
        )
        PayoutService.set_stripe_adapter(mock_adapter)

        try:
            # Step 1: Execute payout
            result = PayoutService.execute_payout(payout.id)

            assert result.success is True
            assert result.data.stripe_transfer_id == "tr_integration_test_123"

            # Verify payout state
            payout = Payout.objects.get(id=payout.id)
            assert payout.state == PayoutState.PROCESSING
            assert payout.stripe_transfer_id == "tr_integration_test_123"

            # Step 2: Simulate transfer.paid webhook
            from payments.models import WebhookEvent

            webhook_event = WebhookEvent.objects.create(
                stripe_event_id="evt_integration_test_123",
                event_type="transfer.paid",
                payload={
                    "id": "evt_integration_test_123",
                    "type": "transfer.paid",
                    "data": {
                        "object": {
                            "id": "tr_integration_test_123",
                            "object": "transfer",
                            "amount": 9000,
                            "currency": "usd",
                        }
                    },
                },
                status=WebhookEventStatus.PENDING,
            )

            webhook_result = handle_transfer_paid(webhook_event)
            assert webhook_result.success is True

            # Step 3: Verify final states
            payout = Payout.objects.get(id=payout.id)
            assert payout.state == PayoutState.PAID
            assert payout.paid_at is not None

            # Verify PaymentOrder is SETTLED
            order = PaymentOrder.objects.get(id=released_escrow_order.id)
            assert order.state == PaymentOrderState.SETTLED
            assert order.settled_at is not None

            # Step 4: Verify ledger entry
            payout_entries = LedgerEntry.objects.filter(
                reference_type="payout",
                reference_id=payout.id,
            )
            assert payout_entries.count() == 1
            entry = payout_entries.first()
            assert entry.entry_type == EntryType.PAYOUT
            assert entry.amount_cents == 9000

        finally:
            PayoutService.set_stripe_adapter(None)

    def test_payout_idempotency_returns_success_for_already_processing(
        self,
        released_escrow_order,
        connected_account,
        mock_redis_lock,
    ):
        """
        Test that re-executing an already-processing payout is idempotent.

        Idempotent operations should return success when the operation
        has already been performed (or is in progress). This prevents
        unnecessary failures when retrying or handling duplicate requests.
        """
        # Create payout already in PROCESSING state
        payout = PayoutFactory(
            payment_order=released_escrow_order,
            connected_account=connected_account,
            amount_cents=9000,
            stripe_transfer_id="tr_already_processing_123",
        )
        payout.process()
        payout.save()

        assert payout.state == PayoutState.PROCESSING

        # Try to execute again - should succeed (idempotent)
        result = PayoutService.execute_payout(payout.id)

        # Should return success with existing transfer ID
        assert result.success is True
        assert result.data.stripe_transfer_id == "tr_already_processing_123"

        # Payout should remain unchanged
        payout = Payout.objects.get(id=payout.id)
        assert payout.state == PayoutState.PROCESSING
        assert payout.stripe_transfer_id == "tr_already_processing_123"

    def test_concurrent_webhook_delivery_is_idempotent(
        self,
        released_escrow_order,
        connected_account,
        seed_user_balance,
    ):
        """
        Test that duplicate webhook delivery doesn't create duplicate entries.
        """
        # Create payout in PROCESSING state
        payout = PayoutFactory(
            payment_order=released_escrow_order,
            connected_account=connected_account,
            amount_cents=9000,
            stripe_transfer_id="tr_duplicate_webhook_123",
        )
        payout.process()
        payout.save()

        # Seed balance for ledger entry
        seed_user_balance(9000, "usd")

        # Process first webhook
        from payments.models import WebhookEvent

        webhook1 = WebhookEvent.objects.create(
            stripe_event_id="evt_duplicate_1",
            event_type="transfer.paid",
            payload={
                "id": "evt_duplicate_1",
                "type": "transfer.paid",
                "data": {
                    "object": {
                        "id": "tr_duplicate_webhook_123",
                        "object": "transfer",
                    }
                },
            },
            status=WebhookEventStatus.PENDING,
        )

        result1 = handle_transfer_paid(webhook1)
        assert result1.success is True

        # Verify payout is PAID
        payout = Payout.objects.get(id=payout.id)
        assert payout.state == PayoutState.PAID

        # Count ledger entries
        initial_entry_count = LedgerEntry.objects.filter(
            reference_type="payout",
            reference_id=payout.id,
        ).count()
        assert initial_entry_count == 1

        # Process duplicate webhook
        webhook2 = WebhookEvent.objects.create(
            stripe_event_id="evt_duplicate_2",
            event_type="transfer.paid",
            payload={
                "id": "evt_duplicate_2",
                "type": "transfer.paid",
                "data": {
                    "object": {
                        "id": "tr_duplicate_webhook_123",
                        "object": "transfer",
                    }
                },
            },
            status=WebhookEventStatus.PENDING,
        )

        result2 = handle_transfer_paid(webhook2)
        # Should still succeed (idempotent)
        assert result2.success is True

        # No new ledger entries
        final_entry_count = LedgerEntry.objects.filter(
            reference_type="payout",
            reference_id=payout.id,
        ).count()
        assert final_entry_count == initial_entry_count


@pytest.mark.django_db
class TestPayoutWorkerIntegration:
    """Integration tests for payout worker tasks."""

    def test_process_pending_payouts_queues_ready_payouts(
        self,
        released_escrow_order,
        connected_account,
    ):
        """Test that process_pending_payouts finds and queues pending payouts."""
        from payments.workers.payout_executor import process_pending_payouts

        # Create pending payout
        payout = PayoutFactory(
            payment_order=released_escrow_order,
            connected_account=connected_account,
            amount_cents=9000,
            state=PayoutState.PENDING,
        )

        with patch(
            "payments.workers.payout_executor.execute_single_payout"
        ) as mock_task:
            mock_task.delay = MagicMock()

            result = process_pending_payouts()

            assert result["queued_count"] >= 1
            # Verify our payout was queued
            call_ids = [call[0][0] for call in mock_task.delay.call_args_list]
            assert str(payout.id) in call_ids

    def test_retry_failed_payouts_retries_transient_failures(
        self,
        released_escrow_order,
        connected_account,
    ):
        """Test that retry_failed_payouts retries payouts with transient errors."""
        from payments.workers.payout_executor import retry_failed_payouts

        # Create failed payout with retryable reason
        payout = PayoutFactory(
            payment_order=released_escrow_order,
            connected_account=connected_account,
            amount_cents=9000,
            stripe_transfer_id="tr_failed_retry_123",
        )
        payout.process()
        payout.save()
        payout.fail(reason="rate_limit: Too many requests")
        payout.save()

        assert payout.state == PayoutState.FAILED

        with patch(
            "payments.workers.payout_executor.execute_single_payout"
        ) as mock_task:
            mock_task.delay = MagicMock()

            result = retry_failed_payouts()

            assert result["queued_count"] == 1
            assert result["skipped_count"] == 0

            # Verify payout was reset to PENDING
            payout = Payout.objects.get(id=payout.id)
            assert payout.state == PayoutState.PENDING
            assert payout.metadata.get("retry_count") == 1
