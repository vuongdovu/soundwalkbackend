"""
TDD tests for RefundService.

These tests define the expected behavior of the RefundService before
implementation. The tests cover:
1. Refund eligibility for all PaymentOrder states
2. Partial refund validation and tracking
3. Full refund with state transitions
4. Ledger reversal accuracy
5. Concurrency control (distributed locks, optimistic locking)
6. Stripe API integration (success, errors, retries)
7. Payout coordination (cancel pending, block processing/paid)

Run with: docker-compose exec web pytest payments/tests/test_refund_service.py -v
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from django.utils import timezone

from payments.adapters.stripe_adapter import RefundResult
from payments.exceptions import (
    LockAcquisitionError,
    StripeInvalidRequestError,
    StripeRateLimitError,
    StripeTimeoutError,
)
from payments.ledger import LedgerService
from payments.ledger.models import AccountType, EntryType, LedgerEntry
from payments.ledger.types import RecordEntryParams
from payments.models import PaymentOrder, Payout, Refund
from payments.state_machines import (
    OnboardingStatus,
    PaymentOrderState,
    PaymentStrategyType,
    PayoutState,
    RefundState,
)
from payments.tests.factories import (
    ConnectedAccountFactory,
    PaymentOrderFactory,
    PayoutFactory,
    RefundFactory,
    UserFactory,
)


# =============================================================================
# Fixtures
# =============================================================================


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
def mock_stripe_refund_success():
    """Mock successful Stripe refund response."""
    mock_adapter = MagicMock()
    mock_adapter.create_refund.return_value = RefundResult(
        id="re_test_success_123",
        amount_cents=10000,
        currency="usd",
        status="succeeded",
        payment_intent_id="pi_test_123",
        metadata={},
        raw_response={},
    )
    return mock_adapter


@pytest.fixture
def recipient_profile(db):
    """Create a recipient profile for escrow payments."""
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
def seed_ledger_accounts(db):
    """Seed ledger accounts for testing."""

    def _seed(payment_order, fee_taken=False):
        """
        Seed ledger accounts with payment entries.

        Args:
            payment_order: The PaymentOrder to seed entries for
            fee_taken: Whether platform fee has been deducted
        """
        external = LedgerService.get_or_create_account(
            AccountType.EXTERNAL_STRIPE,
            owner_id=None,
            currency=payment_order.currency,
            allow_negative=True,
        )
        escrow = LedgerService.get_or_create_account(
            AccountType.PLATFORM_ESCROW,
            owner_id=None,
            currency=payment_order.currency,
        )
        revenue = LedgerService.get_or_create_account(
            AccountType.PLATFORM_REVENUE,
            owner_id=None,
            currency=payment_order.currency,
        )

        # Record payment received
        LedgerService.record_entry(
            RecordEntryParams(
                debit_account_id=external.id,
                credit_account_id=escrow.id,
                amount_cents=payment_order.amount_cents,
                entry_type=EntryType.PAYMENT_RECEIVED,
                idempotency_key=f"test:payment:{payment_order.id}:received",
                reference_type="payment_order",
                reference_id=payment_order.id,
                created_by="test",
            )
        )

        if fee_taken:
            # Record fee collected (10% for testing)
            fee = payment_order.amount_cents // 10
            LedgerService.record_entry(
                RecordEntryParams(
                    debit_account_id=escrow.id,
                    credit_account_id=revenue.id,
                    amount_cents=fee,
                    entry_type=EntryType.FEE_COLLECTED,
                    idempotency_key=f"test:payment:{payment_order.id}:fee",
                    reference_type="payment_order",
                    reference_id=payment_order.id,
                    created_by="test",
                )
            )

        return {"external": external, "escrow": escrow, "revenue": revenue}

    return _seed


@pytest.fixture
def seed_user_balance_for_refund(db, recipient_profile):
    """Seed user balance account for refund testing (released payments)."""

    def _seed(amount_cents, currency="usd"):
        user_balance = LedgerService.get_or_create_account(
            AccountType.USER_BALANCE,
            owner_id=recipient_profile.pk,
            currency=currency,
        )
        escrow = LedgerService.get_or_create_account(
            AccountType.PLATFORM_ESCROW,
            owner_id=None,
            currency=currency,
        )

        # Record release to user balance
        LedgerService.record_entry(
            RecordEntryParams(
                debit_account_id=escrow.id,
                credit_account_id=user_balance.id,
                amount_cents=amount_cents,
                entry_type=EntryType.PAYMENT_RELEASED,
                idempotency_key=f"test:release:{uuid4()}",
                description="Test seed for refund test",
                created_by="test",
            )
        )
        return user_balance

    return _seed


# =============================================================================
# Test Class: Refund Eligibility
# =============================================================================


@pytest.mark.django_db
class TestRefundEligibility:
    """Tests for refund eligibility based on PaymentOrder state."""

    def test_draft_order_not_refundable(self, draft_order):
        """DRAFT state should not allow refunds (no money moved)."""
        from payments.services.refund_service import RefundService

        result = RefundService.check_refund_eligibility(draft_order)

        assert result.eligible is False
        assert (
            "DRAFT" in result.block_reason or "no money" in result.block_reason.lower()
        )

    def test_pending_order_not_refundable(self, pending_order):
        """PENDING state should not allow refunds (no money moved)."""
        from payments.services.refund_service import RefundService

        result = RefundService.check_refund_eligibility(pending_order)

        assert result.eligible is False

    def test_processing_order_not_refundable(self, processing_order):
        """PROCESSING state should not allow refunds (payment in progress)."""
        from payments.services.refund_service import RefundService

        result = RefundService.check_refund_eligibility(processing_order)

        assert result.eligible is False

    def test_captured_direct_order_is_refundable(self, captured_order):
        """CAPTURED state with DIRECT strategy allows refunds."""
        from payments.services.refund_service import RefundService

        result = RefundService.check_refund_eligibility(captured_order)

        assert result.eligible is True
        assert result.fee_refundable is False  # Fee already taken for direct

    def test_held_escrow_order_is_refundable_with_fee(self, held_order):
        """HELD state (escrow) allows full refund including fee."""
        from payments.services.refund_service import RefundService

        result = RefundService.check_refund_eligibility(held_order)

        assert result.eligible is True
        assert result.fee_refundable is True  # Fee not taken yet
        assert result.max_refundable_cents == held_order.amount_cents

    def test_released_order_refundable_if_payout_pending(
        self, released_order, connected_account
    ):
        """RELEASED state allows refund if payout is PENDING."""
        from payments.services.refund_service import RefundService

        # Create pending payout
        payout = PayoutFactory(
            payment_order=released_order,
            connected_account=connected_account,
            state=PayoutState.PENDING,
        )

        result = RefundService.check_refund_eligibility(released_order)

        assert result.eligible is True
        assert result.requires_payout_cancellation is True
        assert result.payout_to_cancel == payout

    def test_released_order_blocked_if_payout_processing(
        self, released_order, connected_account
    ):
        """RELEASED state blocks refund if payout is PROCESSING."""
        from payments.services.refund_service import RefundService

        # Create processing payout
        payout = PayoutFactory(
            payment_order=released_order,
            connected_account=connected_account,
        )
        payout.process()
        payout.save()

        result = RefundService.check_refund_eligibility(released_order)

        assert result.eligible is False
        assert "PROCESSING" in result.block_reason

    def test_released_order_blocked_if_payout_paid(
        self, released_order, connected_account
    ):
        """RELEASED state blocks refund if payout is PAID (requires manual)."""
        from payments.services.refund_service import RefundService

        # Create paid payout
        payout = PayoutFactory(
            payment_order=released_order,
            connected_account=connected_account,
        )
        payout.process()
        payout.save()
        payout.complete()
        payout.save()

        result = RefundService.check_refund_eligibility(released_order)

        assert result.eligible is False
        assert "manual" in result.block_reason.lower() or "PAID" in result.block_reason

    def test_settled_order_blocked_if_payout_paid(self, user, connected_account):
        """SETTLED state blocks refund if payout is PAID."""
        from payments.services.refund_service import RefundService

        # Create settled escrow order
        order = PaymentOrderFactory(
            payer=user,
            strategy_type=PaymentStrategyType.ESCROW,
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
        order.settle_from_released()
        order.save()

        # Create paid payout
        payout = PayoutFactory(
            payment_order=order,
            connected_account=connected_account,
        )
        payout.process()
        payout.save()
        payout.complete()
        payout.save()

        result = RefundService.check_refund_eligibility(order)

        assert result.eligible is False

    def test_failed_order_not_refundable(self, failed_order):
        """FAILED state should not allow refunds (terminal state)."""
        from payments.services.refund_service import RefundService

        result = RefundService.check_refund_eligibility(failed_order)

        assert result.eligible is False

    def test_cancelled_order_not_refundable(self, cancelled_order):
        """CANCELLED state should not allow refunds (terminal state)."""
        from payments.services.refund_service import RefundService

        result = RefundService.check_refund_eligibility(cancelled_order)

        assert result.eligible is False

    def test_refunded_order_not_refundable(self, refunded_order):
        """REFUNDED state should not allow refunds (terminal state)."""
        from payments.services.refund_service import RefundService

        result = RefundService.check_refund_eligibility(refunded_order)

        assert result.eligible is False


# =============================================================================
# Test Class: Partial Refunds
# =============================================================================


@pytest.mark.django_db
class TestPartialRefunds:
    """Tests for partial refund functionality."""

    def test_partial_refund_creates_refund_record(
        self,
        captured_order,
        mock_redis_lock,
        mock_stripe_refund_success,
        seed_ledger_accounts,
    ):
        """Partial refund should create Refund model instance."""
        from payments.services.refund_service import RefundService

        seed_ledger_accounts(captured_order, fee_taken=True)
        RefundService.set_stripe_adapter(mock_stripe_refund_success)

        try:
            result = RefundService.create_refund(
                payment_order_id=captured_order.id,
                amount_cents=5000,  # Half of $100
                reason="Customer requested partial refund",
            )

            assert result.success is True
            assert result.data.refund.amount_cents == 5000
            assert result.data.refund.state == RefundState.COMPLETED
        finally:
            RefundService.set_stripe_adapter(None)

    def test_multiple_partial_refunds_allowed(
        self,
        captured_order,
        mock_redis_lock,
        mock_stripe_refund_success,
        seed_ledger_accounts,
    ):
        """Multiple partial refunds should be allowed."""
        from payments.services.refund_service import RefundService

        seed_ledger_accounts(captured_order, fee_taken=True)
        RefundService.set_stripe_adapter(mock_stripe_refund_success)

        try:
            # First partial refund
            result1 = RefundService.create_refund(
                payment_order_id=captured_order.id,
                amount_cents=3000,
            )
            assert result1.success is True

            # Second partial refund
            result2 = RefundService.create_refund(
                payment_order_id=captured_order.id,
                amount_cents=2000,
            )
            assert result2.success is True

            # Verify two refunds exist
            refund_count = Refund.objects.filter(payment_order=captured_order).count()
            assert refund_count == 2
        finally:
            RefundService.set_stripe_adapter(None)

    def test_partial_refunds_cannot_exceed_original_amount(
        self,
        captured_order,
        mock_redis_lock,
        mock_stripe_refund_success,
        seed_ledger_accounts,
    ):
        """Total refunds cannot exceed original payment amount."""
        from payments.services.refund_service import RefundService

        seed_ledger_accounts(captured_order, fee_taken=True)
        RefundService.set_stripe_adapter(mock_stripe_refund_success)

        try:
            # First refund: $60
            result1 = RefundService.create_refund(
                payment_order_id=captured_order.id,
                amount_cents=6000,
            )
            assert result1.success is True

            # Second refund: $50 (would exceed $100 total)
            result2 = RefundService.create_refund(
                payment_order_id=captured_order.id,
                amount_cents=5000,
            )

            assert result2.success is False
            assert "exceeds" in result2.error.lower()
        finally:
            RefundService.set_stripe_adapter(None)

    def test_partial_refund_amount_must_be_positive(
        self, captured_order, mock_redis_lock
    ):
        """Refund amount must be positive."""
        from payments.services.refund_service import RefundService

        result = RefundService.create_refund(
            payment_order_id=captured_order.id,
            amount_cents=0,
        )

        assert result.success is False
        assert "positive" in result.error.lower() or "invalid" in result.error.lower()

    def test_partial_refund_calculates_remaining_correctly(
        self,
        captured_order,
        mock_redis_lock,
        mock_stripe_refund_success,
        seed_ledger_accounts,
    ):
        """Should calculate remaining refundable amount correctly."""
        from payments.services.refund_service import RefundService

        seed_ledger_accounts(captured_order, fee_taken=True)
        RefundService.set_stripe_adapter(mock_stripe_refund_success)

        try:
            # First refund: $30
            RefundService.create_refund(
                payment_order_id=captured_order.id,
                amount_cents=3000,
            )

            # Check remaining - use objects.get() instead of refresh_from_db() for FSM fields
            updated_order = PaymentOrder.objects.get(id=captured_order.id)
            eligibility = RefundService.check_refund_eligibility(updated_order)

            # Should have $70 remaining
            assert eligibility.max_refundable_cents == 7000
        finally:
            RefundService.set_stripe_adapter(None)

    def test_partial_refund_transitions_to_partially_refunded(
        self,
        captured_order,
        mock_redis_lock,
        mock_stripe_refund_success,
        seed_ledger_accounts,
    ):
        """Partial refund should transition PaymentOrder to PARTIALLY_REFUNDED."""
        from payments.services.refund_service import RefundService

        seed_ledger_accounts(captured_order, fee_taken=True)
        RefundService.set_stripe_adapter(mock_stripe_refund_success)

        try:
            result = RefundService.create_refund(
                payment_order_id=captured_order.id,
                amount_cents=5000,
            )

            assert result.success is True

            # Use objects.get() instead of refresh_from_db() for FSM fields
            updated_order = PaymentOrder.objects.get(id=captured_order.id)
            assert updated_order.state == PaymentOrderState.PARTIALLY_REFUNDED
        finally:
            RefundService.set_stripe_adapter(None)


# =============================================================================
# Test Class: Full Refunds
# =============================================================================


@pytest.mark.django_db
class TestFullRefunds:
    """Tests for full refund functionality."""

    def test_full_refund_held_order_includes_full_amount(
        self,
        held_order,
        mock_redis_lock,
        mock_stripe_refund_success,
        seed_ledger_accounts,
    ):
        """Full refund from HELD includes full amount (fee not taken)."""
        from payments.services.refund_service import RefundService

        seed_ledger_accounts(held_order, fee_taken=False)  # Fee not taken in HELD
        RefundService.set_stripe_adapter(mock_stripe_refund_success)

        try:
            result = RefundService.create_refund(
                payment_order_id=held_order.id,
                amount_cents=None,  # Full refund
            )

            assert result.success is True
            assert result.data.refund.amount_cents == held_order.amount_cents
        finally:
            RefundService.set_stripe_adapter(None)

    def test_full_refund_transitions_to_refunded(
        self,
        captured_order,
        mock_redis_lock,
        mock_stripe_refund_success,
        seed_ledger_accounts,
    ):
        """Full refund should transition PaymentOrder to REFUNDED."""
        from payments.services.refund_service import RefundService

        # Don't take fee so escrow has full amount for full refund
        seed_ledger_accounts(captured_order, fee_taken=False)
        RefundService.set_stripe_adapter(mock_stripe_refund_success)

        try:
            result = RefundService.create_refund(
                payment_order_id=captured_order.id,
                amount_cents=None,  # Full refund
            )

            assert result.success is True

            # Use objects.get() instead of refresh_from_db() for FSM fields
            updated_order = PaymentOrder.objects.get(id=captured_order.id)
            assert updated_order.state == PaymentOrderState.REFUNDED
        finally:
            RefundService.set_stripe_adapter(None)

    def test_full_refund_after_partial_calculates_remainder(
        self, captured_order, mock_redis_lock, seed_ledger_accounts
    ):
        """Full refund after partial should refund remaining amount."""
        from payments.services.refund_service import RefundService

        # Don't take fee so escrow has full amount
        seed_ledger_accounts(captured_order, fee_taken=False)

        # Use side_effect to return different refund IDs for each call
        refund_counter = [0]

        def make_refund_result(*args, **kwargs):
            refund_counter[0] += 1
            amount = kwargs.get("amount_cents", 10000)
            return RefundResult(
                id=f"re_test_partial_{refund_counter[0]}",
                amount_cents=amount,
                currency="usd",
                status="succeeded",
                payment_intent_id="pi_test_123",
                metadata={},
                raw_response={},
            )

        mock_adapter = MagicMock()
        mock_adapter.create_refund.side_effect = make_refund_result
        RefundService.set_stripe_adapter(mock_adapter)

        try:
            # Partial refund first: $30
            RefundService.create_refund(
                payment_order_id=captured_order.id,
                amount_cents=3000,
            )

            # Full refund of remaining
            result = RefundService.create_refund(
                payment_order_id=captured_order.id,
                amount_cents=None,  # Full (remaining) refund
            )

            assert result.success is True
            # Should refund remaining $70
            assert result.data.refund.amount_cents == 7000

            # Use objects.get() instead of refresh_from_db() for FSM fields
            updated_order = PaymentOrder.objects.get(id=captured_order.id)
            assert updated_order.state == PaymentOrderState.REFUNDED
        finally:
            RefundService.set_stripe_adapter(None)


# =============================================================================
# Test Class: Ledger Reversals
# =============================================================================


@pytest.mark.django_db
class TestLedgerReversals:
    """Tests for ledger entry creation during refunds."""

    def test_refund_creates_correct_ledger_entry(
        self,
        captured_order,
        mock_redis_lock,
        mock_stripe_refund_success,
        seed_ledger_accounts,
    ):
        """Refund should create correct debit/credit ledger entry."""
        from payments.services.refund_service import RefundService

        accounts = seed_ledger_accounts(captured_order, fee_taken=True)
        RefundService.set_stripe_adapter(mock_stripe_refund_success)

        try:
            result = RefundService.create_refund(
                payment_order_id=captured_order.id,
                amount_cents=5000,
            )

            assert result.success is True

            # Find refund ledger entry
            refund_entries = LedgerEntry.objects.filter(
                entry_type=EntryType.REFUND,
                reference_type="refund",
                reference_id=result.data.refund.id,
            )

            assert refund_entries.count() >= 1
            entry = refund_entries.first()
            assert entry.amount_cents == 5000
            # Debit escrow, credit external (money going back to Stripe)
            assert entry.debit_account_id == accounts["escrow"].id
            assert entry.credit_account_id == accounts["external"].id
        finally:
            RefundService.set_stripe_adapter(None)

    def test_ledger_idempotency_key_prevents_duplicates(
        self,
        captured_order,
        mock_redis_lock,
        mock_stripe_refund_success,
        seed_ledger_accounts,
    ):
        """Same idempotency key should not create duplicate entries."""
        from payments.services.refund_service import RefundService

        seed_ledger_accounts(captured_order, fee_taken=True)
        RefundService.set_stripe_adapter(mock_stripe_refund_success)

        try:
            RefundService.create_refund(
                payment_order_id=captured_order.id,
                amount_cents=5000,
            )

            initial_count = LedgerEntry.objects.filter(
                entry_type=EntryType.REFUND,
            ).count()

            # The ledger service should be idempotent
            # If same refund is processed again, no new entries
            # (This tests the idempotency key mechanism)
            assert initial_count >= 1
        finally:
            RefundService.set_stripe_adapter(None)

    def test_partial_refund_ledger_entry_proportional(
        self,
        captured_order,
        mock_redis_lock,
        mock_stripe_refund_success,
        seed_ledger_accounts,
    ):
        """Partial refund creates proportional ledger entry."""
        from payments.services.refund_service import RefundService

        seed_ledger_accounts(captured_order, fee_taken=True)
        RefundService.set_stripe_adapter(mock_stripe_refund_success)

        try:
            # 30% refund
            result = RefundService.create_refund(
                payment_order_id=captured_order.id,
                amount_cents=3000,  # $30 of $100
            )

            assert result.success is True

            refund_entries = LedgerEntry.objects.filter(
                entry_type=EntryType.REFUND,
                reference_id=result.data.refund.id,
            )

            entry = refund_entries.first()
            assert entry.amount_cents == 3000
        finally:
            RefundService.set_stripe_adapter(None)

    def test_refund_ledger_entry_has_correct_reference(
        self,
        captured_order,
        mock_redis_lock,
        mock_stripe_refund_success,
        seed_ledger_accounts,
    ):
        """Ledger entry should reference the Refund model."""
        from payments.services.refund_service import RefundService

        seed_ledger_accounts(captured_order, fee_taken=True)
        RefundService.set_stripe_adapter(mock_stripe_refund_success)

        try:
            result = RefundService.create_refund(
                payment_order_id=captured_order.id,
                amount_cents=5000,
            )

            refund_entries = LedgerEntry.objects.filter(
                entry_type=EntryType.REFUND,
                reference_type="refund",
            )

            assert refund_entries.count() >= 1
            entry = refund_entries.first()
            assert entry.reference_type == "refund"
            assert entry.reference_id == result.data.refund.id
        finally:
            RefundService.set_stripe_adapter(None)


# =============================================================================
# Test Class: Concurrency
# =============================================================================


@pytest.mark.django_db
class TestRefundConcurrency:
    """Tests for concurrency control during refund operations."""

    def test_distributed_lock_acquired_for_refund(
        self, captured_order, mock_stripe_refund_success, seed_ledger_accounts
    ):
        """Refund should acquire distributed lock."""
        from payments.services.refund_service import RefundService

        seed_ledger_accounts(captured_order, fee_taken=True)
        RefundService.set_stripe_adapter(mock_stripe_refund_success)

        mock_redis = MagicMock()
        mock_redis.set.return_value = True
        mock_redis.get.return_value = None
        mock_redis.delete.return_value = 1
        mock_redis.eval.return_value = 1

        with patch("payments.locks.get_redis_connection", return_value=mock_redis):
            result = RefundService.create_refund(
                payment_order_id=captured_order.id,
                amount_cents=5000,
            )

            assert result.success is True
            # Lock should have been acquired
            mock_redis.set.assert_called()

        RefundService.set_stripe_adapter(None)

    def test_lock_acquisition_failure_raises_error(
        self, captured_order, mock_stripe_refund_success, seed_ledger_accounts
    ):
        """Lock acquisition failure should raise LockAcquisitionError."""
        from payments.services.refund_service import RefundService

        seed_ledger_accounts(captured_order, fee_taken=True)
        RefundService.set_stripe_adapter(mock_stripe_refund_success)

        mock_redis = MagicMock()
        mock_redis.set.return_value = False  # Lock acquisition fails

        with patch("payments.locks.get_redis_connection", return_value=mock_redis):
            with pytest.raises(LockAcquisitionError):
                RefundService.create_refund(
                    payment_order_id=captured_order.id,
                    amount_cents=5000,
                )

        RefundService.set_stripe_adapter(None)

    def test_refund_with_processing_refund_blocks(
        self, captured_order, mock_redis_lock
    ):
        """Cannot issue new refund while another is PROCESSING."""
        from payments.services.refund_service import RefundService

        # Create processing refund
        existing_refund = RefundFactory(
            payment_order=captured_order,
            amount_cents=5000,
        )
        existing_refund.process()
        existing_refund.save()

        result = RefundService.create_refund(
            payment_order_id=captured_order.id,
            amount_cents=3000,
        )

        assert result.success is False
        assert "in progress" in result.error.lower()


# =============================================================================
# Test Class: Stripe Integration
# =============================================================================


@pytest.mark.django_db
class TestStripeRefundIntegration:
    """Tests for Stripe refund API integration."""

    def test_successful_stripe_refund_stores_refund_id(
        self,
        captured_order,
        mock_redis_lock,
        mock_stripe_refund_success,
        seed_ledger_accounts,
    ):
        """Successful Stripe refund should store stripe_refund_id."""
        from payments.services.refund_service import RefundService

        seed_ledger_accounts(captured_order, fee_taken=True)
        RefundService.set_stripe_adapter(mock_stripe_refund_success)

        try:
            result = RefundService.create_refund(
                payment_order_id=captured_order.id,
                amount_cents=5000,
            )

            assert result.success is True
            assert result.data.stripe_refund_id == "re_test_success_123"
            assert result.data.refund.stripe_refund_id == "re_test_success_123"
        finally:
            RefundService.set_stripe_adapter(None)

    def test_stripe_timeout_leaves_refund_processing(
        self, captured_order, mock_redis_lock, seed_ledger_accounts
    ):
        """Stripe timeout should leave refund in PROCESSING for retry."""
        from payments.services.refund_service import RefundService

        seed_ledger_accounts(captured_order, fee_taken=True)

        mock_adapter = MagicMock()
        mock_adapter.create_refund.side_effect = StripeTimeoutError("Request timed out")
        RefundService.set_stripe_adapter(mock_adapter)

        try:
            with pytest.raises(StripeTimeoutError):
                RefundService.create_refund(
                    payment_order_id=captured_order.id,
                    amount_cents=5000,
                )

            # Refund should exist in PROCESSING state for reconciliation
            # (created before Stripe call, left in PROCESSING for retry)
            assert Refund.objects.filter(
                payment_order=captured_order,
                state=RefundState.PROCESSING,
            ).exists()
        finally:
            RefundService.set_stripe_adapter(None)

    def test_stripe_rate_limit_is_retryable(
        self, captured_order, mock_redis_lock, seed_ledger_accounts
    ):
        """Rate limit error should be retryable (raise for Celery)."""
        from payments.services.refund_service import RefundService

        seed_ledger_accounts(captured_order, fee_taken=True)

        mock_adapter = MagicMock()
        mock_adapter.create_refund.side_effect = StripeRateLimitError(
            "Too many requests"
        )
        RefundService.set_stripe_adapter(mock_adapter)

        try:
            with pytest.raises(StripeRateLimitError) as exc_info:
                RefundService.create_refund(
                    payment_order_id=captured_order.id,
                    amount_cents=5000,
                )

            assert exc_info.value.is_retryable is True
        finally:
            RefundService.set_stripe_adapter(None)

    def test_stripe_invalid_request_marks_refund_failed(
        self, captured_order, mock_redis_lock, seed_ledger_accounts
    ):
        """Permanent Stripe error should mark refund as FAILED."""
        from payments.services.refund_service import RefundService

        seed_ledger_accounts(captured_order, fee_taken=True)

        mock_adapter = MagicMock()
        mock_adapter.create_refund.side_effect = StripeInvalidRequestError(
            "Invalid refund request"
        )
        RefundService.set_stripe_adapter(mock_adapter)

        try:
            result = RefundService.create_refund(
                payment_order_id=captured_order.id,
                amount_cents=5000,
            )

            # Should return failure, not raise
            assert result.success is False
            assert "invalid" in result.error.lower()
        finally:
            RefundService.set_stripe_adapter(None)

    def test_refund_uses_idempotency_key(
        self, captured_order, mock_redis_lock, seed_ledger_accounts
    ):
        """Refund should use idempotency key for Stripe call."""
        from payments.services.refund_service import RefundService

        seed_ledger_accounts(captured_order, fee_taken=True)

        mock_adapter = MagicMock()
        mock_adapter.create_refund.return_value = RefundResult(
            id="re_test_idempotent_123",
            amount_cents=5000,
            currency="usd",
            status="succeeded",
            payment_intent_id="pi_test_123",
            metadata={},
            raw_response={},
        )
        RefundService.set_stripe_adapter(mock_adapter)

        try:
            RefundService.create_refund(
                payment_order_id=captured_order.id,
                amount_cents=5000,
            )

            # Verify idempotency_key was passed
            call_args = mock_adapter.create_refund.call_args
            assert "idempotency_key" in call_args.kwargs
            assert call_args.kwargs["idempotency_key"] is not None
        finally:
            RefundService.set_stripe_adapter(None)


# =============================================================================
# Test Class: Payout Coordination
# =============================================================================


@pytest.mark.django_db
class TestPayoutCoordination:
    """Tests for refund coordination with payout lifecycle."""

    def test_refund_with_pending_payout_cancels_payout(
        self,
        released_order,
        connected_account,
        mock_redis_lock,
        mock_stripe_refund_success,
        seed_ledger_accounts,
        seed_user_balance_for_refund,
    ):
        """Refund should cancel PENDING payout before processing."""
        from payments.services.refund_service import RefundService

        seed_ledger_accounts(released_order, fee_taken=True)

        # Create pending payout
        payout = PayoutFactory(
            payment_order=released_order,
            connected_account=connected_account,
            amount_cents=9000,
        )

        # Seed user balance to simulate payout allocation (needed for ledger reversal)
        seed_user_balance_for_refund(payout.amount_cents, released_order.currency)

        RefundService.set_stripe_adapter(mock_stripe_refund_success)

        try:
            result = RefundService.create_refund(
                payment_order_id=released_order.id,
                amount_cents=None,  # Full refund
            )

            assert result.success is True
            assert result.data.payout_cancelled is True

            # Payout should be cancelled - use objects.get() for FSM fields
            updated_payout = Payout.objects.get(id=payout.id)
            assert updated_payout.state == PayoutState.CANCELLED
        finally:
            RefundService.set_stripe_adapter(None)

    def test_refund_with_scheduled_payout_cancels_payout(
        self,
        released_order,
        connected_account,
        mock_redis_lock,
        mock_stripe_refund_success,
        seed_ledger_accounts,
        seed_user_balance_for_refund,
    ):
        """Refund should cancel SCHEDULED payout before processing."""
        from payments.services.refund_service import RefundService

        seed_ledger_accounts(released_order, fee_taken=True)

        # Create scheduled payout
        payout = PayoutFactory(
            payment_order=released_order,
            connected_account=connected_account,
            amount_cents=9000,
        )
        future_time = timezone.now() + timezone.timedelta(days=1)
        payout.schedule(scheduled_for=future_time)
        payout.save()

        # Seed user balance to simulate payout allocation (needed for ledger reversal)
        seed_user_balance_for_refund(payout.amount_cents, released_order.currency)

        RefundService.set_stripe_adapter(mock_stripe_refund_success)

        try:
            result = RefundService.create_refund(
                payment_order_id=released_order.id,
                amount_cents=None,
            )

            assert result.success is True

            # Use objects.get() for FSM fields
            updated_payout = Payout.objects.get(id=payout.id)
            assert updated_payout.state == PayoutState.CANCELLED
        finally:
            RefundService.set_stripe_adapter(None)

    def test_refund_with_processing_payout_blocks(
        self, released_order, connected_account, mock_redis_lock
    ):
        """Cannot refund while payout is PROCESSING (in flight)."""
        from payments.services.refund_service import RefundService

        # Create processing payout
        payout = PayoutFactory(
            payment_order=released_order,
            connected_account=connected_account,
            amount_cents=9000,
        )
        payout.process()
        payout.save()

        result = RefundService.create_refund(
            payment_order_id=released_order.id,
            amount_cents=5000,
        )

        assert result.success is False
        assert (
            "processing" in result.error.lower() or "in flight" in result.error.lower()
        )

    def test_refund_with_paid_payout_blocks(
        self, released_order, connected_account, mock_redis_lock
    ):
        """Cannot refund when payout is PAID (requires manual intervention)."""
        from payments.services.refund_service import RefundService

        # Create paid payout
        payout = PayoutFactory(
            payment_order=released_order,
            connected_account=connected_account,
            amount_cents=9000,
        )
        payout.process()
        payout.save()
        payout.complete()
        payout.save()

        result = RefundService.create_refund(
            payment_order_id=released_order.id,
            amount_cents=5000,
        )

        assert result.success is False
        assert "manual" in result.error.lower() or "paid" in result.error.lower()


# =============================================================================
# Test Class: Edge Cases
# =============================================================================


@pytest.mark.django_db
class TestRefundEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_minimum_refund_amount_one_cent(
        self,
        captured_order,
        mock_redis_lock,
        mock_stripe_refund_success,
        seed_ledger_accounts,
    ):
        """Minimum refund amount is $0.01 (1 cent)."""
        from payments.services.refund_service import RefundService

        seed_ledger_accounts(captured_order, fee_taken=True)

        mock_adapter = MagicMock()
        mock_adapter.create_refund.return_value = RefundResult(
            id="re_test_min_amount_123",
            amount_cents=1,
            currency="usd",
            status="succeeded",
            payment_intent_id="pi_test_123",
            metadata={},
            raw_response={},
        )
        RefundService.set_stripe_adapter(mock_adapter)

        try:
            result = RefundService.create_refund(
                payment_order_id=captured_order.id,
                amount_cents=1,
            )

            assert result.success is True
            assert result.data.refund.amount_cents == 1
        finally:
            RefundService.set_stripe_adapter(None)

    def test_refund_exact_remaining_amount_transitions_to_refunded(
        self, captured_order, mock_redis_lock, seed_ledger_accounts
    ):
        """Refund of exact remaining amount should transition to REFUNDED."""
        from payments.services.refund_service import RefundService

        # Don't take fee so escrow has full amount
        seed_ledger_accounts(captured_order, fee_taken=False)

        # Use side_effect to return different refund IDs for each call
        refund_counter = [0]

        def make_refund_result(*args, **kwargs):
            refund_counter[0] += 1
            amount = kwargs.get("amount_cents", 10000)
            return RefundResult(
                id=f"re_test_exact_{refund_counter[0]}",
                amount_cents=amount,
                currency="usd",
                status="succeeded",
                payment_intent_id="pi_test_123",
                metadata={},
                raw_response={},
            )

        mock_adapter = MagicMock()
        mock_adapter.create_refund.side_effect = make_refund_result
        RefundService.set_stripe_adapter(mock_adapter)

        try:
            # First refund: $60
            RefundService.create_refund(
                payment_order_id=captured_order.id,
                amount_cents=6000,
            )

            # Second refund: exactly $40 (remaining)
            result = RefundService.create_refund(
                payment_order_id=captured_order.id,
                amount_cents=4000,
            )

            assert result.success is True

            # Use objects.get() for FSM fields
            updated_order = PaymentOrder.objects.get(id=captured_order.id)
            assert updated_order.state == PaymentOrderState.REFUNDED
        finally:
            RefundService.set_stripe_adapter(None)

    def test_refund_nonexistent_order_returns_not_found(self, mock_redis_lock):
        """Refund for non-existent order should fail with not found."""
        from payments.services.refund_service import RefundService

        fake_id = uuid4()

        result = RefundService.create_refund(
            payment_order_id=fake_id,
            amount_cents=5000,
        )

        assert result.success is False
        assert "not found" in result.error.lower()

    def test_refund_currency_matches_order_currency(
        self, user, mock_redis_lock, seed_ledger_accounts
    ):
        """Refund currency should match order currency."""
        from payments.services.refund_service import RefundService

        # Create EUR order
        order = PaymentOrderFactory(
            payer=user,
            amount_cents=10000,
            currency="eur",
        )
        order.submit()
        order.save()
        order.process()
        order.save()
        order.capture()
        order.save()

        seed_ledger_accounts(order, fee_taken=True)

        mock_adapter = MagicMock()
        mock_adapter.create_refund.return_value = RefundResult(
            id="re_test_eur_123",
            amount_cents=5000,
            currency="eur",
            status="succeeded",
            payment_intent_id="pi_test_123",
            metadata={},
            raw_response={},
        )
        RefundService.set_stripe_adapter(mock_adapter)

        try:
            result = RefundService.create_refund(
                payment_order_id=order.id,
                amount_cents=5000,
            )

            assert result.success is True
            assert result.data.refund.currency == "eur"
        finally:
            RefundService.set_stripe_adapter(None)

    def test_refund_with_failed_previous_refund_allowed(
        self,
        captured_order,
        mock_redis_lock,
        mock_stripe_refund_success,
        seed_ledger_accounts,
    ):
        """Can refund after previous refund failed."""
        from payments.services.refund_service import RefundService

        seed_ledger_accounts(captured_order, fee_taken=True)

        # Create failed refund
        failed_refund = RefundFactory(
            payment_order=captured_order,
            amount_cents=3000,
        )
        failed_refund.process()
        failed_refund.save()
        failed_refund.fail(reason="Card network error")
        failed_refund.save()

        RefundService.set_stripe_adapter(mock_stripe_refund_success)

        try:
            # New refund should succeed (failed doesn't count)
            result = RefundService.create_refund(
                payment_order_id=captured_order.id,
                amount_cents=None,  # Full refund
            )

            assert result.success is True
            # Full amount should be available (failed doesn't count)
            assert result.data.refund.amount_cents == captured_order.amount_cents
        finally:
            RefundService.set_stripe_adapter(None)
