"""
Tests for EscrowPaymentStrategy.

Tests cover:
- Payment creation with recipient validation
- State transitions on payment success (to HELD)
- FundHold creation with expiration
- Ledger entry recording (capture and release)
- Platform fee calculation (deferred to release)
- Hold release with payout creation
- Refund handling at various stages
- Error handling and edge cases
- Concurrency and idempotency

TDD: These tests are written before implementation.
Run with: pytest payments/strategies/tests/test_escrow_strategy.py -v
"""

import uuid
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings
from django.utils import timezone
from freezegun import freeze_time

from authentication.models import Profile, User
from payments.exceptions import StripeCardDeclinedError
from payments.ledger.models import AccountType, EntryType, LedgerAccount, LedgerEntry
from payments.models import ConnectedAccount, FundHold, PaymentOrder, Payout
from payments.state_machines import (
    OnboardingStatus,
    PaymentOrderState,
    PaymentStrategyType,
    PayoutState,
)
from payments.strategies import CreatePaymentParams
from payments.strategies.escrow import EscrowPaymentStrategy


# =============================================================================
# Additional Fixtures for Escrow Tests
# =============================================================================


@pytest.fixture
def recipient_user(db):
    """Create a recipient user (mentor) for escrow payments."""
    return User.objects.create_user(
        email="mentor@example.com",
        password="testpass123",
    )


@pytest.fixture
def recipient_profile(db, recipient_user):
    """Get or create a profile for the recipient user.

    Note: Profile may be auto-created by signals when user is created.
    """
    profile, _ = Profile.objects.get_or_create(
        user=recipient_user,
        defaults={
            "first_name": "Test",
            "last_name": "Mentor",
        },
    )
    return profile


@pytest.fixture
def connected_account(db, recipient_profile):
    """Create a connected account ready for payouts."""
    return ConnectedAccount.objects.create(
        profile=recipient_profile,
        stripe_account_id="acct_test_123",
        onboarding_status=OnboardingStatus.COMPLETE,
        payouts_enabled=True,
        charges_enabled=True,
    )


@pytest.fixture
def connected_account_not_ready(db, recipient_profile):
    """Create a connected account NOT ready for payouts."""
    return ConnectedAccount.objects.create(
        profile=recipient_profile,
        stripe_account_id="acct_test_not_ready",
        onboarding_status=OnboardingStatus.IN_PROGRESS,
        payouts_enabled=False,
        charges_enabled=False,
    )


@pytest.fixture
def draft_escrow_order(db, test_user, recipient_profile):
    """Create an escrow payment order in DRAFT state."""
    return PaymentOrder.objects.create(
        payer=test_user,
        amount_cents=10000,
        currency="usd",
        strategy_type=PaymentStrategyType.ESCROW,
        metadata={"recipient_profile_id": str(recipient_profile.pk)},
    )


@pytest.fixture
def pending_escrow_order(db, test_user, recipient_profile):
    """Create an escrow payment order in PENDING state."""
    order = PaymentOrder.objects.create(
        payer=test_user,
        amount_cents=10000,
        currency="usd",
        strategy_type=PaymentStrategyType.ESCROW,
        stripe_payment_intent_id="pi_test_escrow_pending",
        metadata={"recipient_profile_id": str(recipient_profile.pk)},
    )
    order.submit()
    order.save()
    return order


@pytest.fixture
def held_escrow_order(db, test_user, recipient_profile):
    """Create an escrow payment order in HELD state with FundHold and ledger entries."""
    from payments.ledger.services import LedgerService
    from payments.ledger.types import RecordEntryParams

    order = PaymentOrder.objects.create(
        payer=test_user,
        amount_cents=10000,
        currency="usd",
        strategy_type=PaymentStrategyType.ESCROW,
        stripe_payment_intent_id="pi_test_escrow_held",
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

    # Create the FundHold
    FundHold.objects.create(
        payment_order=order,
        amount_cents=order.amount_cents,
        currency=order.currency,
        expires_at=timezone.now() + timedelta(days=42),
    )

    # Create the capture ledger entry (funds into escrow)
    # This is needed for release tests to have balance to debit
    external_account = LedgerService.get_or_create_account(
        AccountType.EXTERNAL_STRIPE,
        allow_negative=True,
    )
    escrow_account = LedgerService.get_or_create_account(
        AccountType.PLATFORM_ESCROW,
    )

    LedgerService.record_entries(
        [
            RecordEntryParams(
                debit_account_id=external_account.id,
                credit_account_id=escrow_account.id,
                amount_cents=order.amount_cents,
                entry_type=EntryType.PAYMENT_RECEIVED,
                idempotency_key=f"test:escrow:{order.id}:capture",
                reference_type="payment_order",
                reference_id=order.id,
                description=f"Test capture for order {order.id}",
                created_by="test_fixture",
            )
        ]
    )

    return order


@pytest.fixture
def held_escrow_order_with_connected_account(db, held_escrow_order, connected_account):
    """Create a held escrow order with a connected account for release testing."""
    return held_escrow_order


@pytest.fixture
def released_escrow_order(db, test_user, recipient_profile, connected_account):
    """Create an escrow payment order in RELEASED state with Payout."""
    order = PaymentOrder.objects.create(
        payer=test_user,
        amount_cents=10000,
        currency="usd",
        strategy_type=PaymentStrategyType.ESCROW,
        stripe_payment_intent_id="pi_test_escrow_released",
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

    # Create FundHold
    fund_hold = FundHold.objects.create(
        payment_order=order,
        amount_cents=order.amount_cents,
        currency=order.currency,
        expires_at=timezone.now() + timedelta(days=42),
    )

    # Create Payout
    payout = Payout.objects.create(
        payment_order=order,
        connected_account=connected_account,
        amount_cents=8500,  # After 15% fee
        currency=order.currency,
    )

    # Mark hold as released
    fund_hold.release_to(payout)
    fund_hold.save()

    # Transition to RELEASED
    order.release()
    order.save()

    return order


@pytest.fixture
def released_order_with_pending_payout(db, released_escrow_order):
    """Released order with payout still in PENDING state."""
    return released_escrow_order


@pytest.fixture
def settled_escrow_order_with_paid_payout(db, released_escrow_order, connected_account):
    """Settled escrow order with payout in PAID state."""
    order = released_escrow_order
    payout = order.payouts.first()

    # Complete the payout
    payout.process()
    payout.save()
    payout.complete()
    payout.save()

    # Settle the order
    order.settle_from_released()
    order.save()

    return order


# =============================================================================
# EscrowPaymentStrategy.create_payment Tests
# =============================================================================


class TestEscrowPaymentStrategyCreatePayment:
    """Tests for EscrowPaymentStrategy.create_payment."""

    def test_create_payment_success(
        self, test_user, mock_stripe_adapter, recipient_profile
    ):
        """Should create escrow payment order with recipient metadata."""
        strategy = EscrowPaymentStrategy(stripe_adapter=mock_stripe_adapter)

        params = CreatePaymentParams(
            payer=test_user,
            amount_cents=5000,
            currency="usd",
            metadata={"recipient_profile_id": str(recipient_profile.pk)},
        )

        result = strategy.create_payment(params)

        assert result.success is True
        assert result.data is not None
        assert result.data.payment_order is not None
        assert result.data.client_secret is not None

        order = result.data.payment_order
        assert order.payer == test_user
        assert order.amount_cents == 5000
        assert order.currency == "usd"
        assert order.state == PaymentOrderState.PENDING
        assert order.strategy_type == PaymentStrategyType.ESCROW
        assert order.stripe_payment_intent_id is not None
        assert order.metadata["recipient_profile_id"] == str(recipient_profile.pk)

    def test_create_payment_missing_recipient_fails(
        self, test_user, mock_stripe_adapter
    ):
        """Should fail if recipient_profile_id not in metadata."""
        strategy = EscrowPaymentStrategy(stripe_adapter=mock_stripe_adapter)

        params = CreatePaymentParams(
            payer=test_user,
            amount_cents=5000,
            currency="usd",
            metadata={},  # No recipient_profile_id
        )

        result = strategy.create_payment(params)

        assert result.success is False
        assert result.error_code == "MISSING_RECIPIENT"
        assert "recipient_profile_id" in result.error.lower()

    def test_create_payment_no_metadata_fails(self, test_user, mock_stripe_adapter):
        """Should fail if metadata is None."""
        strategy = EscrowPaymentStrategy(stripe_adapter=mock_stripe_adapter)

        params = CreatePaymentParams(
            payer=test_user,
            amount_cents=5000,
            currency="usd",
            # No metadata at all
        )

        result = strategy.create_payment(params)

        assert result.success is False
        assert result.error_code == "MISSING_RECIPIENT"

    def test_create_payment_invalid_recipient_format_fails(
        self, test_user, mock_stripe_adapter
    ):
        """Should fail if recipient_profile_id is invalid UUID."""
        strategy = EscrowPaymentStrategy(stripe_adapter=mock_stripe_adapter)

        params = CreatePaymentParams(
            payer=test_user,
            amount_cents=5000,
            currency="usd",
            metadata={"recipient_profile_id": "not-a-uuid"},
        )

        result = strategy.create_payment(params)

        assert result.success is False
        assert result.error_code == "INVALID_RECIPIENT"

    def test_create_payment_with_reference(
        self, test_user, mock_stripe_adapter, recipient_profile
    ):
        """Should create escrow payment with business reference."""
        strategy = EscrowPaymentStrategy(stripe_adapter=mock_stripe_adapter)

        reference_id = uuid.uuid4()
        params = CreatePaymentParams(
            payer=test_user,
            amount_cents=10000,
            reference_id=reference_id,
            reference_type="session",
            metadata={
                "recipient_profile_id": str(recipient_profile.pk),
                "session_topic": "Python mentoring",
            },
        )

        result = strategy.create_payment(params)

        assert result.success is True
        order = result.data.payment_order
        assert order.reference_id == reference_id
        assert order.reference_type == "session"
        assert order.metadata["session_topic"] == "Python mentoring"

    def test_create_payment_stripe_error(
        self, test_user, mock_stripe_adapter, recipient_profile
    ):
        """Should return failure on Stripe error."""
        mock_stripe_adapter.create_payment_intent_side_effect = StripeCardDeclinedError(
            "Your card was declined",
            decline_code="generic_decline",
        )

        strategy = EscrowPaymentStrategy(stripe_adapter=mock_stripe_adapter)

        params = CreatePaymentParams(
            payer=test_user,
            amount_cents=5000,
            metadata={"recipient_profile_id": str(recipient_profile.pk)},
        )

        result = strategy.create_payment(params)

        assert result.success is False
        assert "declined" in result.error.lower()

    def test_create_payment_warns_if_no_connected_account(
        self, test_user, mock_stripe_adapter, recipient_profile, caplog
    ):
        """Should warn but not fail if recipient has no connected account."""
        # recipient_profile has no connected account
        strategy = EscrowPaymentStrategy(stripe_adapter=mock_stripe_adapter)

        params = CreatePaymentParams(
            payer=test_user,
            amount_cents=5000,
            metadata={"recipient_profile_id": str(recipient_profile.pk)},
        )

        result = strategy.create_payment(params)

        # Should still succeed - they can complete onboarding before release
        assert result.success is True
        assert "not ready for payouts" in caplog.text.lower()


# =============================================================================
# EscrowPaymentStrategy.handle_payment_succeeded Tests
# =============================================================================


class TestEscrowPaymentStrategyHandleSuccess:
    """Tests for EscrowPaymentStrategy.handle_payment_succeeded."""

    def test_handle_success_transitions_to_held(
        self, pending_escrow_order, payment_succeeded_event_data
    ):
        """Should transition from PENDING to HELD (not SETTLED)."""
        strategy = EscrowPaymentStrategy()

        result = strategy.handle_payment_succeeded(
            pending_escrow_order, payment_succeeded_event_data
        )

        assert result.success is True
        assert result.data.state == PaymentOrderState.HELD
        assert result.data.held_at is not None
        assert result.data.captured_at is not None
        # Should NOT be settled yet
        assert result.data.settled_at is None

    def test_handle_success_creates_fund_hold(
        self, pending_escrow_order, payment_succeeded_event_data
    ):
        """Should create FundHold record with correct amount and expiration."""
        strategy = EscrowPaymentStrategy()

        result = strategy.handle_payment_succeeded(
            pending_escrow_order, payment_succeeded_event_data
        )

        assert result.success is True

        # Verify FundHold was created
        fund_holds = FundHold.objects.filter(payment_order=pending_escrow_order)
        assert fund_holds.count() == 1

        fund_hold = fund_holds.first()
        assert fund_hold.amount_cents == pending_escrow_order.amount_cents
        assert fund_hold.currency == pending_escrow_order.currency
        assert fund_hold.released is False
        assert fund_hold.released_at is None
        assert fund_hold.released_to_payout is None

    @freeze_time("2024-01-15 12:00:00")
    @override_settings(ESCROW_DEFAULT_HOLD_DURATION_DAYS=42)
    def test_fund_hold_has_correct_expiration(
        self, pending_escrow_order, payment_succeeded_event_data
    ):
        """Should set expiration to 42 days from now by default."""
        strategy = EscrowPaymentStrategy()

        result = strategy.handle_payment_succeeded(
            pending_escrow_order, payment_succeeded_event_data
        )

        assert result.success is True

        fund_hold = FundHold.objects.get(payment_order=pending_escrow_order)
        expected_expiration = timezone.now() + timedelta(days=42)

        # Check expiration is approximately correct (within 1 second)
        assert abs((fund_hold.expires_at - expected_expiration).total_seconds()) < 1

    def test_handle_success_creates_capture_ledger_entry(
        self, pending_escrow_order, payment_succeeded_event_data
    ):
        """Should create ledger entry: EXTERNAL_STRIPE -> PLATFORM_ESCROW."""
        strategy = EscrowPaymentStrategy()

        result = strategy.handle_payment_succeeded(
            pending_escrow_order, payment_succeeded_event_data
        )

        assert result.success is True

        # Verify ledger entry was created
        entries = LedgerEntry.objects.filter(
            reference_type="payment_order",
            reference_id=pending_escrow_order.id,
        )

        # Should have exactly 1 entry at capture (fee is deferred to release)
        assert entries.count() == 1

        capture_entry = entries.first()
        assert capture_entry.entry_type == EntryType.PAYMENT_RECEIVED
        assert capture_entry.amount_cents == pending_escrow_order.amount_cents

        # Verify accounts
        assert capture_entry.debit_account.type == AccountType.EXTERNAL_STRIPE
        assert capture_entry.credit_account.type == AccountType.PLATFORM_ESCROW

    def test_handle_success_no_fee_at_capture(
        self, pending_escrow_order, payment_succeeded_event_data
    ):
        """Should NOT deduct platform fee at capture (deferred to release)."""
        strategy = EscrowPaymentStrategy()

        result = strategy.handle_payment_succeeded(
            pending_escrow_order, payment_succeeded_event_data
        )

        assert result.success is True

        # Verify no fee entry was created
        fee_entries = LedgerEntry.objects.filter(
            reference_type="payment_order",
            reference_id=pending_escrow_order.id,
            entry_type=EntryType.FEE_COLLECTED,
        )
        assert fee_entries.count() == 0

    def test_handle_success_idempotent_for_held(
        self, held_escrow_order, payment_succeeded_event_data
    ):
        """Should be idempotent for already held orders."""
        strategy = EscrowPaymentStrategy()

        result = strategy.handle_payment_succeeded(
            held_escrow_order, payment_succeeded_event_data
        )

        assert result.success is True
        assert result.data.state == PaymentOrderState.HELD

    def test_handle_success_idempotent_for_released(
        self, released_escrow_order, payment_succeeded_event_data
    ):
        """Should be idempotent for already released orders."""
        strategy = EscrowPaymentStrategy()

        result = strategy.handle_payment_succeeded(
            released_escrow_order, payment_succeeded_event_data
        )

        assert result.success is True
        assert result.data.state == PaymentOrderState.RELEASED

    def test_handle_success_invalid_state(
        self, failed_payment_order, payment_succeeded_event_data
    ):
        """Should fail for invalid starting state."""
        strategy = EscrowPaymentStrategy()

        result = strategy.handle_payment_succeeded(
            failed_payment_order, payment_succeeded_event_data
        )

        assert result.success is False
        assert result.error_code == "INVALID_STATE"


# =============================================================================
# EscrowPaymentStrategy.handle_payment_failed Tests
# =============================================================================


class TestEscrowPaymentStrategyHandleFailure:
    """Tests for EscrowPaymentStrategy.handle_payment_failed."""

    def test_handle_failure_from_pending(
        self, pending_escrow_order, payment_failed_event_data
    ):
        """Should transition from PENDING to FAILED."""
        strategy = EscrowPaymentStrategy()

        result = strategy.handle_payment_failed(
            pending_escrow_order,
            payment_failed_event_data,
            reason="Card was declined",
        )

        assert result.success is True
        assert result.data.state == PaymentOrderState.FAILED
        assert result.data.failed_at is not None
        assert result.data.failure_reason == "Card was declined"

    def test_handle_failure_idempotent(
        self, failed_payment_order, payment_failed_event_data
    ):
        """Should be idempotent for already failed orders."""
        strategy = EscrowPaymentStrategy()

        result = strategy.handle_payment_failed(
            failed_payment_order,
            payment_failed_event_data,
            reason="Another failure",
        )

        assert result.success is True
        assert result.data.state == PaymentOrderState.FAILED


# =============================================================================
# EscrowPaymentStrategy.release_hold Tests
# =============================================================================


class TestEscrowPaymentStrategyReleaseHold:
    """Tests for EscrowPaymentStrategy.release_hold."""

    def test_release_hold_success(self, held_escrow_order_with_connected_account):
        """Should release funds and transition to RELEASED."""
        order = held_escrow_order_with_connected_account
        strategy = EscrowPaymentStrategy()

        result = strategy.release_hold(order, release_reason="service_completed")

        assert result.success is True
        assert result.data.state == PaymentOrderState.RELEASED
        assert result.data.released_at is not None

    def test_release_hold_creates_payout(
        self, held_escrow_order_with_connected_account
    ):
        """Should create Payout record in PENDING state."""
        order = held_escrow_order_with_connected_account
        strategy = EscrowPaymentStrategy()

        result = strategy.release_hold(order)

        assert result.success is True

        # Verify Payout was created
        payouts = Payout.objects.filter(payment_order=order)
        assert payouts.count() == 1

        payout = payouts.first()
        assert payout.state == PayoutState.PENDING
        # Amount should be original minus fee (15% of 10000 = 1500)
        assert payout.amount_cents == 8500
        assert payout.currency == order.currency

    def test_release_hold_marks_fund_hold_released(
        self, held_escrow_order_with_connected_account
    ):
        """Should mark FundHold as released with timestamp."""
        order = held_escrow_order_with_connected_account
        strategy = EscrowPaymentStrategy()

        result = strategy.release_hold(order)

        assert result.success is True

        fund_hold = FundHold.objects.get(payment_order=order)
        assert fund_hold.released is True
        assert fund_hold.released_at is not None
        assert fund_hold.released_to_payout is not None

    def test_release_hold_creates_ledger_entries(
        self, held_escrow_order_with_connected_account, recipient_profile
    ):
        """Should create ledger entries for release and fee."""
        order = held_escrow_order_with_connected_account
        strategy = EscrowPaymentStrategy()

        # Get initial entry count (should have capture entry from fixture)
        initial_count = LedgerEntry.objects.filter(
            reference_type="payment_order",
            reference_id=order.id,
        ).count()

        result = strategy.release_hold(order)

        assert result.success is True

        # Verify ledger entries
        entries = LedgerEntry.objects.filter(
            reference_type="payment_order",
            reference_id=order.id,
        ).order_by("created_at")

        # Should have initial + 2 new entries: recipient release and fee
        assert entries.count() == initial_count + 2

        # Find the release and fee entries
        release_entry = entries.filter(entry_type=EntryType.PAYMENT_RELEASED).first()
        fee_entry = entries.filter(entry_type=EntryType.FEE_COLLECTED).first()

        assert release_entry is not None
        # 10000 - 1500 fee = 8500 to recipient
        assert release_entry.amount_cents == 8500
        assert release_entry.debit_account.type == AccountType.PLATFORM_ESCROW
        assert release_entry.credit_account.type == AccountType.USER_BALANCE
        # owner_id is a UUID derived from the integer profile pk
        assert release_entry.credit_account.owner_id is not None

        assert fee_entry is not None
        assert fee_entry.amount_cents == 1500  # 15% fee
        assert fee_entry.debit_account.type == AccountType.PLATFORM_ESCROW
        assert fee_entry.credit_account.type == AccountType.PLATFORM_REVENUE

    def test_release_hold_no_connected_account_fails(
        self, held_escrow_order, recipient_profile
    ):
        """Should fail if recipient has no ConnectedAccount."""
        # held_escrow_order's recipient has no connected account
        strategy = EscrowPaymentStrategy()

        result = strategy.release_hold(held_escrow_order)

        assert result.success is False
        assert result.error_code == "RECIPIENT_NOT_READY"
        assert "connected account" in result.error.lower()

    def test_release_hold_connected_account_not_ready_fails(
        self, held_escrow_order, connected_account_not_ready
    ):
        """Should fail if ConnectedAccount is not ready for payouts."""
        strategy = EscrowPaymentStrategy()

        result = strategy.release_hold(held_escrow_order)

        assert result.success is False
        assert result.error_code == "RECIPIENT_NOT_READY"

    def test_release_hold_invalid_state_fails(self, pending_escrow_order):
        """Should fail if PaymentOrder not in HELD state."""
        strategy = EscrowPaymentStrategy()

        result = strategy.release_hold(pending_escrow_order)

        assert result.success is False
        assert result.error_code == "INVALID_STATE"

    def test_release_hold_idempotent_for_released(self, released_escrow_order):
        """Should be idempotent for already released orders."""
        strategy = EscrowPaymentStrategy()

        result = strategy.release_hold(released_escrow_order)

        assert result.success is True
        assert result.data.state == PaymentOrderState.RELEASED

    @patch("payments.strategies.escrow.DistributedLock")
    def test_release_hold_uses_distributed_lock(
        self, mock_lock_class, held_escrow_order_with_connected_account
    ):
        """Should acquire distributed lock during release."""
        mock_lock = MagicMock()
        mock_lock.__enter__ = MagicMock(return_value=None)
        mock_lock.__exit__ = MagicMock(return_value=False)
        mock_lock_class.return_value = mock_lock

        order = held_escrow_order_with_connected_account
        strategy = EscrowPaymentStrategy()

        strategy.release_hold(order)

        # Verify lock was acquired with correct key
        mock_lock_class.assert_called()
        call_args = mock_lock_class.call_args
        assert f"escrow:release:{order.id}" in str(call_args)


# =============================================================================
# EscrowPaymentStrategy.refund_held_payment Tests
# =============================================================================


class TestEscrowPaymentStrategyRefund:
    """Tests for EscrowPaymentStrategy.refund_held_payment."""

    @patch("payments.strategies.escrow.StripeAdapter")
    def test_refund_while_held_full(self, mock_stripe, held_escrow_order):
        """Should allow full refund while funds are held."""
        mock_stripe.create_refund.return_value = MagicMock(
            id="re_test_123",
            status="succeeded",
        )

        strategy = EscrowPaymentStrategy(stripe_adapter=mock_stripe)

        result = strategy.refund_held_payment(
            held_escrow_order,
            amount_cents=None,  # Full refund
            reason="Customer requested",
        )

        assert result.success is True
        # Should refund full amount (no fee was taken yet)

    @patch("payments.strategies.escrow.StripeAdapter")
    def test_refund_while_held_partial(self, mock_stripe, held_escrow_order):
        """Should allow partial refund while funds are held."""
        mock_stripe.create_refund.return_value = MagicMock(
            id="re_test_partial",
            status="succeeded",
        )

        strategy = EscrowPaymentStrategy(stripe_adapter=mock_stripe)

        result = strategy.refund_held_payment(
            held_escrow_order,
            amount_cents=5000,  # Partial refund
            reason="Partial service",
        )

        assert result.success is True

    def test_refund_after_payout_paid_blocked(
        self, settled_escrow_order_with_paid_payout
    ):
        """Should block refund if payout already PAID."""
        strategy = EscrowPaymentStrategy()

        result = strategy.refund_held_payment(
            settled_escrow_order_with_paid_payout,
            reason="Dispute",
        )

        assert result.success is False
        assert result.error_code == "PAYOUT_ALREADY_PAID"
        assert "manual resolution" in result.error.lower()

    @patch("payments.strategies.escrow.StripeAdapter")
    def test_refund_after_release_before_payout_allowed(
        self, mock_stripe, released_order_with_pending_payout
    ):
        """Should allow refund if released but payout not yet PAID."""
        mock_stripe.create_refund.return_value = MagicMock(
            id="re_test_before_payout",
            status="succeeded",
        )

        strategy = EscrowPaymentStrategy(stripe_adapter=mock_stripe)

        result = strategy.refund_held_payment(
            released_order_with_pending_payout,
            reason="Service not delivered",
        )

        # Should succeed and cancel the pending payout
        assert result.success is True

    def test_refund_invalid_state_fails(self, pending_escrow_order):
        """Should fail if order is not in a refundable state."""
        strategy = EscrowPaymentStrategy()

        result = strategy.refund_held_payment(
            pending_escrow_order,
            reason="Too early",
        )

        assert result.success is False
        assert result.error_code == "INVALID_STATE"


# =============================================================================
# EscrowPaymentStrategy.calculate_platform_fee Tests
# =============================================================================


class TestEscrowPaymentStrategyPlatformFee:
    """Tests for platform fee calculation and timing."""

    def test_calculate_fee_default(self):
        """Should calculate 15% fee by default."""
        strategy = EscrowPaymentStrategy()

        assert strategy.calculate_platform_fee(10000) == 1500
        assert strategy.calculate_platform_fee(9999) == 1499
        assert strategy.calculate_platform_fee(100) == 15

    @override_settings(PLATFORM_FEE_PERCENT=10)
    def test_calculate_fee_custom_percent(self):
        """Should use custom fee percentage from settings."""
        strategy = EscrowPaymentStrategy()

        assert strategy.calculate_platform_fee(10000) == 1000

    def test_fee_not_deducted_at_capture(
        self, pending_escrow_order, payment_succeeded_event_data
    ):
        """Fee should NOT be deducted at capture time."""
        strategy = EscrowPaymentStrategy()

        strategy.handle_payment_succeeded(
            pending_escrow_order, payment_succeeded_event_data
        )

        # Check PLATFORM_ESCROW has full amount
        escrow_account = LedgerAccount.objects.filter(
            type=AccountType.PLATFORM_ESCROW,
            currency=pending_escrow_order.currency,
        ).first()

        if escrow_account:
            balance = escrow_account.get_balance()
            assert balance == pending_escrow_order.amount_cents

    def test_fee_deducted_at_release(self, held_escrow_order_with_connected_account):
        """Fee should be deducted at release time."""
        order = held_escrow_order_with_connected_account
        strategy = EscrowPaymentStrategy()

        # First ensure we have the capture ledger entry
        from payments.ledger.services import LedgerService
        from payments.ledger.types import RecordEntryParams

        external_account = LedgerService.get_or_create_account(
            AccountType.EXTERNAL_STRIPE, allow_negative=True
        )
        escrow_account = LedgerService.get_or_create_account(
            AccountType.PLATFORM_ESCROW
        )

        LedgerService.record_entries(
            [
                RecordEntryParams(
                    debit_account_id=external_account.id,
                    credit_account_id=escrow_account.id,
                    amount_cents=order.amount_cents,
                    entry_type=EntryType.PAYMENT_RECEIVED,
                    idempotency_key=f"escrow:{order.id}:capture",
                    reference_type="payment_order",
                    reference_id=order.id,
                    description=f"Payment captured for order {order.id}",
                    created_by="test",
                )
            ]
        )

        # Now release
        result = strategy.release_hold(order)
        assert result.success is True

        # Check fee was collected
        revenue_account = LedgerAccount.objects.filter(
            type=AccountType.PLATFORM_REVENUE,
            currency=order.currency,
        ).first()

        assert revenue_account is not None
        balance = revenue_account.get_balance()
        assert balance == 1500  # 15% of 10000


# =============================================================================
# Ledger Entry Idempotency Tests
# =============================================================================


class TestEscrowLedgerIdempotency:
    """Tests for ledger entry idempotency in EscrowPaymentStrategy."""

    def test_capture_ledger_entries_idempotent(
        self, pending_escrow_order, payment_succeeded_event_data
    ):
        """Should not create duplicate ledger entries on retry."""
        strategy = EscrowPaymentStrategy()

        # First call
        result1 = strategy.handle_payment_succeeded(
            pending_escrow_order, payment_succeeded_event_data
        )
        assert result1.success is True

        entry_count_after_first = LedgerEntry.objects.filter(
            reference_id=pending_escrow_order.id
        ).count()

        # Reload order
        order = PaymentOrder.objects.get(id=pending_escrow_order.id)

        # Second call (simulating retry)
        result2 = strategy.handle_payment_succeeded(order, payment_succeeded_event_data)
        assert result2.success is True

        entry_count_after_second = LedgerEntry.objects.filter(
            reference_id=pending_escrow_order.id
        ).count()

        assert entry_count_after_second == entry_count_after_first


# =============================================================================
# Transaction Atomicity Tests
# =============================================================================


class TestEscrowTransactionAtomicity:
    """Tests for transaction atomicity in escrow operations."""

    def test_capture_rollback_on_fund_hold_failure(
        self, pending_escrow_order, payment_succeeded_event_data
    ):
        """Should rollback state if FundHold creation fails."""
        strategy = EscrowPaymentStrategy()

        with patch.object(
            FundHold.objects, "create", side_effect=Exception("DB Error")
        ):
            result = strategy.handle_payment_succeeded(
                pending_escrow_order, payment_succeeded_event_data
            )

        assert result.success is False

        # Reload and verify state hasn't changed
        order = PaymentOrder.objects.get(id=pending_escrow_order.id)
        assert order.state == PaymentOrderState.PENDING

    def test_release_rollback_on_payout_failure(
        self, held_escrow_order_with_connected_account
    ):
        """Should rollback if Payout creation fails during release."""
        order = held_escrow_order_with_connected_account
        strategy = EscrowPaymentStrategy()

        with patch.object(Payout.objects, "create", side_effect=Exception("DB Error")):
            result = strategy.release_hold(order)

        assert result.success is False

        # Reload and verify state hasn't changed
        order = PaymentOrder.objects.get(id=order.id)
        assert order.state == PaymentOrderState.HELD

        # FundHold should not be marked as released
        fund_hold = FundHold.objects.get(payment_order=order)
        assert fund_hold.released is False


# =============================================================================
# Integration Tests - Full Escrow Flow
# =============================================================================


@pytest.mark.django_db
@pytest.mark.integration
class TestEscrowFullFlowIntegration:
    """Integration tests for complete escrow payment flows."""

    def test_full_escrow_flow_create_to_release(
        self,
        test_user,
        mock_stripe_adapter,
        recipient_profile,
        connected_account,
        payment_succeeded_event_data,
    ):
        """
        Test complete escrow flow: create -> capture -> hold -> release.

        This tests the full happy path:
        1. Create payment (DRAFT -> PENDING)
        2. Handle success webhook (PENDING -> HELD)
        3. Release hold (HELD -> RELEASED)
        """
        strategy = EscrowPaymentStrategy(stripe_adapter=mock_stripe_adapter)

        # Step 1: Create payment
        params = CreatePaymentParams(
            payer=test_user,
            amount_cents=10000,
            currency="usd",
            metadata={"recipient_profile_id": str(recipient_profile.pk)},
        )

        create_result = strategy.create_payment(params)
        assert create_result.success is True
        order = create_result.data.payment_order
        assert order.state == PaymentOrderState.PENDING

        # Step 2: Handle payment success (webhook)
        success_result = strategy.handle_payment_succeeded(
            order, payment_succeeded_event_data
        )
        assert success_result.success is True
        order = success_result.data
        assert order.state == PaymentOrderState.HELD

        # Verify FundHold was created
        fund_hold = FundHold.objects.get(payment_order=order)
        assert fund_hold.released is False
        assert fund_hold.amount_cents == 10000

        # Verify capture ledger entry
        from payments.ledger.models import LedgerEntry

        capture_entries = LedgerEntry.objects.filter(
            reference_id=order.id,
            entry_type=EntryType.PAYMENT_RECEIVED,
        )
        assert capture_entries.count() == 1

        # Step 3: Release the hold
        release_result = strategy.release_hold(
            order, release_reason="service_completed"
        )
        assert release_result.success is True
        order = release_result.data
        assert order.state == PaymentOrderState.RELEASED

        # Verify Payout was created
        payout = Payout.objects.get(payment_order=order)
        assert payout.state == PayoutState.PENDING
        assert payout.amount_cents == 8500  # 10000 - 15% fee

        # Verify FundHold was released
        fund_hold.refresh_from_db()
        assert fund_hold.released is True
        assert fund_hold.released_to_payout == payout

        # Verify release ledger entries
        release_entries = LedgerEntry.objects.filter(
            reference_id=order.id,
            entry_type=EntryType.PAYMENT_RELEASED,
        )
        assert release_entries.count() == 1

        fee_entries = LedgerEntry.objects.filter(
            reference_id=order.id,
            entry_type=EntryType.FEE_COLLECTED,
        )
        assert fee_entries.count() == 1

    def test_full_escrow_flow_with_refund_while_held(
        self,
        test_user,
        mock_stripe_adapter,
        recipient_profile,
        payment_succeeded_event_data,
    ):
        """
        Test escrow flow with refund while funds are held.

        This tests the refund path:
        1. Create payment (DRAFT -> PENDING)
        2. Handle success webhook (PENDING -> HELD)
        3. Refund while held (full refund, no fee to worry about)
        """
        mock_stripe_adapter.create_refund = MagicMock(
            return_value=MagicMock(
                id="re_test_full_refund",
                status="succeeded",
            )
        )

        strategy = EscrowPaymentStrategy(stripe_adapter=mock_stripe_adapter)

        # Step 1: Create payment
        params = CreatePaymentParams(
            payer=test_user,
            amount_cents=10000,
            currency="usd",
            metadata={"recipient_profile_id": str(recipient_profile.pk)},
        )

        create_result = strategy.create_payment(params)
        assert create_result.success is True
        order = create_result.data.payment_order

        # Step 2: Handle payment success
        success_result = strategy.handle_payment_succeeded(
            order, payment_succeeded_event_data
        )
        assert success_result.success is True
        order = success_result.data
        assert order.state == PaymentOrderState.HELD

        # Step 3: Refund while held
        refund_result = strategy.refund_held_payment(
            order,
            amount_cents=None,  # Full refund
            reason="Customer requested cancellation",
        )
        assert refund_result.success is True

        # Stripe should have been called with full amount
        mock_stripe_adapter.create_refund.assert_called_once()
        call_kwargs = mock_stripe_adapter.create_refund.call_args
        # Verify full amount was refunded
        assert call_kwargs[1].get("amount_cents") == 10000

    def test_escrow_flow_refund_blocked_after_payout_complete(
        self,
        test_user,
        mock_stripe_adapter,
        recipient_profile,
        connected_account,
        payment_succeeded_event_data,
    ):
        """
        Test that refunds are blocked after payout completes.

        Flow:
        1. Create -> Hold -> Release -> Complete payout
        2. Attempt refund - should be blocked
        """
        strategy = EscrowPaymentStrategy(stripe_adapter=mock_stripe_adapter)

        # Create and process payment through to RELEASED
        params = CreatePaymentParams(
            payer=test_user,
            amount_cents=10000,
            currency="usd",
            metadata={"recipient_profile_id": str(recipient_profile.pk)},
        )

        create_result = strategy.create_payment(params)
        order = create_result.data.payment_order

        strategy.handle_payment_succeeded(order, payment_succeeded_event_data)
        # Get fresh instance to avoid django-fsm refresh_from_db issues
        order = PaymentOrder.objects.get(pk=order.pk)

        release_result = strategy.release_hold(order)
        order = release_result.data

        # Complete the payout
        payout = Payout.objects.get(payment_order=order)
        payout.process()
        payout.save()
        payout.complete()
        payout.save()

        # Transition order to SETTLED - get fresh instance first
        order = PaymentOrder.objects.get(pk=order.pk)
        order.settle_from_released()
        order.save()

        # Attempt refund - should be blocked
        refund_result = strategy.refund_held_payment(
            order,
            reason="Late complaint",
        )

        assert refund_result.success is False
        assert refund_result.error_code == "PAYOUT_ALREADY_PAID"

    def test_escrow_flow_concurrent_release_prevention(
        self,
        test_user,
        mock_stripe_adapter,
        recipient_profile,
        connected_account,
        payment_succeeded_event_data,
    ):
        """
        Test that concurrent releases are prevented.

        Simulates two processes trying to release the same hold.
        """

        strategy = EscrowPaymentStrategy(stripe_adapter=mock_stripe_adapter)

        # Create held order
        params = CreatePaymentParams(
            payer=test_user,
            amount_cents=10000,
            currency="usd",
            metadata={"recipient_profile_id": str(recipient_profile.pk)},
        )

        create_result = strategy.create_payment(params)
        order = create_result.data.payment_order

        strategy.handle_payment_succeeded(order, payment_succeeded_event_data)
        # Get fresh instance to avoid django-fsm refresh_from_db issues
        order = PaymentOrder.objects.get(pk=order.pk)

        # First release should succeed
        result1 = strategy.release_hold(order)
        assert result1.success is True

        # Second release should be idempotent (already released)
        order = PaymentOrder.objects.get(pk=order.pk)
        result2 = strategy.release_hold(order)
        assert result2.success is True
        assert result2.data.state == PaymentOrderState.RELEASED

        # Only one payout should exist
        payout_count = Payout.objects.filter(payment_order=order).count()
        assert payout_count == 1


@pytest.mark.django_db
@pytest.mark.integration
class TestEscrowWorkerIntegration:
    """Integration tests for escrow worker tasks."""

    def test_expired_hold_auto_release(
        self,
        test_user,
        mock_stripe_adapter,
        recipient_profile,
        connected_account,
        payment_succeeded_event_data,
    ):
        """
        Test auto-release of expired holds via worker.

        Simulates the full flow:
        1. Create payment and hold
        2. Hold expires
        3. Worker detects and queues release
        4. Release executes successfully
        """
        from unittest.mock import patch
        from payments.workers.hold_manager import (
            process_expired_holds,
        )

        strategy = EscrowPaymentStrategy(stripe_adapter=mock_stripe_adapter)

        # Create held order
        params = CreatePaymentParams(
            payer=test_user,
            amount_cents=10000,
            currency="usd",
            metadata={"recipient_profile_id": str(recipient_profile.pk)},
        )

        create_result = strategy.create_payment(params)
        order = create_result.data.payment_order

        strategy.handle_payment_succeeded(order, payment_succeeded_event_data)
        # Get fresh instance to avoid django-fsm refresh_from_db issues
        order = PaymentOrder.objects.get(pk=order.pk)

        # Manually expire the hold
        fund_hold = FundHold.objects.get(payment_order=order)
        fund_hold.expires_at = timezone.now() - timedelta(hours=1)
        fund_hold.save()

        # Run the worker to find expired holds
        with patch("payments.workers.hold_manager.release_single_hold") as mock_release:
            mock_release.delay = MagicMock()
            result = process_expired_holds()

        assert result["queued_count"] == 1
        mock_release.delay.assert_called_once_with(
            str(fund_hold.id), reason="expiration"
        )

        # Execute the release directly with the strategy that has the mock adapter
        # (not through the worker, which would create a new strategy instance)
        order = PaymentOrder.objects.get(pk=order.pk)
        release_result = strategy.release_hold(order, release_reason="expiration")

        assert release_result.success is True

        # Verify the release happened
        order = PaymentOrder.objects.get(pk=order.pk)
        assert order.state == PaymentOrderState.RELEASED

        fund_hold = FundHold.objects.get(pk=fund_hold.pk)
        assert fund_hold.released is True

        payout = Payout.objects.get(payment_order=order)
        assert payout.amount_cents == 8500  # After 15% fee

    def test_worker_idempotent_for_already_released(
        self,
        test_user,
        mock_stripe_adapter,
        recipient_profile,
        connected_account,
        payment_succeeded_event_data,
    ):
        """
        Test that worker is idempotent for already released holds.
        """
        from payments.workers.hold_manager import release_single_hold

        strategy = EscrowPaymentStrategy(stripe_adapter=mock_stripe_adapter)

        # Create and release an order
        params = CreatePaymentParams(
            payer=test_user,
            amount_cents=10000,
            currency="usd",
            metadata={"recipient_profile_id": str(recipient_profile.pk)},
        )

        create_result = strategy.create_payment(params)
        order = create_result.data.payment_order

        strategy.handle_payment_succeeded(order, payment_succeeded_event_data)
        # Get fresh instance to avoid django-fsm refresh_from_db issues
        order = PaymentOrder.objects.get(pk=order.pk)

        # Release manually
        strategy.release_hold(order)
        order = PaymentOrder.objects.get(pk=order.pk)

        fund_hold = FundHold.objects.get(payment_order=order)
        initial_payout_count = Payout.objects.filter(payment_order=order).count()

        # Try to release again via worker
        result = release_single_hold(str(fund_hold.id), reason="expiration")

        assert result["status"] == "already_released"

        # No additional payouts should be created
        final_payout_count = Payout.objects.filter(payment_order=order).count()
        assert final_payout_count == initial_payout_count
