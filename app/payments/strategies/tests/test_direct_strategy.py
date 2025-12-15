"""
Tests for DirectPaymentStrategy.

Tests cover:
- Payment creation with Stripe integration
- State transitions on payment success
- State transitions on payment failure
- Ledger entry recording
- Platform fee calculation
- Error handling and edge cases
"""

import uuid

import pytest
from django.test import override_settings

from payments.exceptions import StripeCardDeclinedError
from payments.ledger.models import AccountType, EntryType, LedgerAccount, LedgerEntry
from payments.models import PaymentOrder
from payments.state_machines import PaymentOrderState, PaymentStrategyType
from payments.strategies import CreatePaymentParams, DirectPaymentStrategy


# =============================================================================
# CreatePaymentParams Tests
# =============================================================================


class TestCreatePaymentParams:
    """Tests for CreatePaymentParams validation."""

    def test_valid_params(self, test_user):
        """Should create params with valid values."""
        params = CreatePaymentParams(
            payer=test_user,
            amount_cents=5000,
            currency="usd",
        )

        assert params.payer == test_user
        assert params.amount_cents == 5000
        assert params.currency == "usd"

    def test_amount_must_be_positive(self, test_user):
        """Should raise ValueError for zero or negative amount."""
        with pytest.raises(ValueError, match="amount_cents must be positive"):
            CreatePaymentParams(
                payer=test_user,
                amount_cents=0,
            )

    def test_currency_required(self, test_user):
        """Should raise ValueError for empty currency."""
        with pytest.raises(ValueError, match="currency is required"):
            CreatePaymentParams(
                payer=test_user,
                amount_cents=5000,
                currency="",
            )

    def test_optional_fields(self, test_user):
        """Should allow optional fields."""
        reference_id = uuid.uuid4()
        params = CreatePaymentParams(
            payer=test_user,
            amount_cents=5000,
            reference_id=reference_id,
            reference_type="session",
            metadata={"note": "Test payment"},
        )

        assert params.reference_id == reference_id
        assert params.reference_type == "session"
        assert params.metadata == {"note": "Test payment"}


# =============================================================================
# DirectPaymentStrategy.create_payment Tests
# =============================================================================


class TestDirectPaymentStrategyCreatePayment:
    """Tests for DirectPaymentStrategy.create_payment."""

    def test_create_payment_success(self, test_user, mock_stripe_adapter):
        """Should create payment order and return client secret."""
        strategy = DirectPaymentStrategy(stripe_adapter=mock_stripe_adapter)

        params = CreatePaymentParams(
            payer=test_user,
            amount_cents=5000,
            currency="usd",
        )

        result = strategy.create_payment(params)

        assert result.success is True
        assert result.data is not None
        assert result.data.payment_order is not None
        assert result.data.client_secret is not None

        # Verify payment order was created
        order = result.data.payment_order
        assert order.payer == test_user
        assert order.amount_cents == 5000
        assert order.currency == "usd"
        assert order.state == PaymentOrderState.PENDING
        assert order.strategy_type == PaymentStrategyType.DIRECT
        assert order.stripe_payment_intent_id is not None

    def test_create_payment_with_reference(self, test_user, mock_stripe_adapter):
        """Should create payment with reference to business entity."""
        strategy = DirectPaymentStrategy(stripe_adapter=mock_stripe_adapter)

        reference_id = uuid.uuid4()
        params = CreatePaymentParams(
            payer=test_user,
            amount_cents=10000,
            reference_id=reference_id,
            reference_type="session",
            metadata={"session_topic": "Service description"},
        )

        result = strategy.create_payment(params)

        assert result.success is True
        order = result.data.payment_order
        assert order.reference_id == reference_id
        assert order.reference_type == "session"
        assert order.metadata["session_topic"] == "Service description"

    def test_create_payment_stripe_call(self, test_user, mock_stripe_adapter):
        """Should call Stripe with correct parameters."""
        strategy = DirectPaymentStrategy(stripe_adapter=mock_stripe_adapter)

        params = CreatePaymentParams(
            payer=test_user,
            amount_cents=7500,
            currency="eur",
        )

        strategy.create_payment(params)

        # Verify Stripe was called
        assert "create_payment_intent" in mock_stripe_adapter.calls
        call = mock_stripe_adapter.calls["create_payment_intent"][0]

        stripe_params = call["params"]
        assert stripe_params.amount_cents == 7500
        assert stripe_params.currency == "eur"
        assert stripe_params.idempotency_key is not None
        assert "payment_order_id" in stripe_params.metadata

    def test_create_payment_stripe_error(self, test_user, mock_stripe_adapter):
        """Should return failure on Stripe error."""
        mock_stripe_adapter.create_payment_intent_side_effect = StripeCardDeclinedError(
            "Your card was declined",
            decline_code="generic_decline",
        )

        strategy = DirectPaymentStrategy(stripe_adapter=mock_stripe_adapter)

        params = CreatePaymentParams(
            payer=test_user,
            amount_cents=5000,
        )

        result = strategy.create_payment(params)

        assert result.success is False
        assert result.error is not None
        assert "declined" in result.error.lower()

    def test_create_payment_persists_to_db(self, test_user, mock_stripe_adapter):
        """Should persist payment order to database."""
        strategy = DirectPaymentStrategy(stripe_adapter=mock_stripe_adapter)

        params = CreatePaymentParams(
            payer=test_user,
            amount_cents=5000,
        )

        result = strategy.create_payment(params)

        # Verify persisted to DB
        order_id = result.data.payment_order.id
        db_order = PaymentOrder.objects.get(id=order_id)
        assert db_order.amount_cents == 5000
        assert db_order.state == PaymentOrderState.PENDING


# =============================================================================
# DirectPaymentStrategy.handle_payment_succeeded Tests
# =============================================================================


class TestDirectPaymentStrategyHandleSuccess:
    """Tests for DirectPaymentStrategy.handle_payment_succeeded."""

    def test_handle_success_from_pending(
        self, pending_payment_order, payment_succeeded_event_data
    ):
        """Should transition from PENDING to SETTLED."""
        strategy = DirectPaymentStrategy()

        result = strategy.handle_payment_succeeded(
            pending_payment_order, payment_succeeded_event_data
        )

        assert result.success is True
        assert result.data.state == PaymentOrderState.SETTLED
        assert result.data.settled_at is not None
        assert result.data.captured_at is not None

    def test_handle_success_creates_ledger_entries(
        self, pending_payment_order, payment_succeeded_event_data
    ):
        """Should create ledger entries for payment and fee."""
        strategy = DirectPaymentStrategy()

        result = strategy.handle_payment_succeeded(
            pending_payment_order, payment_succeeded_event_data
        )

        assert result.success is True

        # Verify ledger entries were created
        entries = LedgerEntry.objects.filter(
            reference_type="payment_order",
            reference_id=pending_payment_order.id,
        ).order_by("created_at")

        assert entries.count() == 2

        # Entry 1: Payment received
        payment_entry = entries[0]
        assert payment_entry.entry_type == EntryType.PAYMENT_RECEIVED
        assert payment_entry.amount_cents == pending_payment_order.amount_cents

        # Entry 2: Platform fee
        fee_entry = entries[1]
        assert fee_entry.entry_type == EntryType.FEE_COLLECTED
        # 15% of 10000 = 1500
        assert fee_entry.amount_cents == 1500

    def test_handle_success_idempotent_for_settled(
        self, settled_payment_order, payment_succeeded_event_data
    ):
        """Should be idempotent for already settled orders."""
        strategy = DirectPaymentStrategy()

        result = strategy.handle_payment_succeeded(
            settled_payment_order, payment_succeeded_event_data
        )

        assert result.success is True
        assert result.data.state == PaymentOrderState.SETTLED

    def test_handle_success_idempotent_for_captured(
        self, captured_payment_order, payment_succeeded_event_data
    ):
        """Should be idempotent for already captured orders."""
        strategy = DirectPaymentStrategy()

        result = strategy.handle_payment_succeeded(
            captured_payment_order, payment_succeeded_event_data
        )

        assert result.success is True
        # Already captured, so we return success

    def test_handle_success_invalid_state(
        self, failed_payment_order, payment_succeeded_event_data
    ):
        """Should fail for invalid starting state."""
        strategy = DirectPaymentStrategy()

        result = strategy.handle_payment_succeeded(
            failed_payment_order, payment_succeeded_event_data
        )

        assert result.success is False
        assert result.error_code == "INVALID_STATE"


# =============================================================================
# DirectPaymentStrategy.handle_payment_failed Tests
# =============================================================================


class TestDirectPaymentStrategyHandleFailure:
    """Tests for DirectPaymentStrategy.handle_payment_failed."""

    def test_handle_failure_from_pending(
        self, pending_payment_order, payment_failed_event_data
    ):
        """Should transition from PENDING to FAILED."""
        strategy = DirectPaymentStrategy()

        result = strategy.handle_payment_failed(
            pending_payment_order,
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
        strategy = DirectPaymentStrategy()

        result = strategy.handle_payment_failed(
            failed_payment_order,
            payment_failed_event_data,
            reason="Another failure",
        )

        assert result.success is True
        assert result.data.state == PaymentOrderState.FAILED

    def test_handle_failure_invalid_state(
        self, settled_payment_order, payment_failed_event_data
    ):
        """Should fail for invalid starting state."""
        strategy = DirectPaymentStrategy()

        result = strategy.handle_payment_failed(
            settled_payment_order,
            payment_failed_event_data,
            reason="Should not work",
        )

        assert result.success is False
        assert result.error_code == "INVALID_STATE"


# =============================================================================
# DirectPaymentStrategy.calculate_platform_fee Tests
# =============================================================================


class TestDirectPaymentStrategyPlatformFee:
    """Tests for DirectPaymentStrategy.calculate_platform_fee."""

    def test_calculate_fee_default(self):
        """Should calculate 15% fee by default."""
        strategy = DirectPaymentStrategy()

        # 15% of 10000 = 1500
        assert strategy.calculate_platform_fee(10000) == 1500

        # 15% of 9999 = 1499 (integer division)
        assert strategy.calculate_platform_fee(9999) == 1499

        # 15% of 100 = 15
        assert strategy.calculate_platform_fee(100) == 15

    @override_settings(PLATFORM_FEE_PERCENT=10)
    def test_calculate_fee_custom_percent(self):
        """Should use custom fee percentage from settings."""
        strategy = DirectPaymentStrategy()

        # 10% of 10000 = 1000
        assert strategy.calculate_platform_fee(10000) == 1000

    @override_settings(PLATFORM_FEE_PERCENT=0)
    def test_calculate_fee_zero(self):
        """Should return zero when fee is 0%."""
        strategy = DirectPaymentStrategy()

        assert strategy.calculate_platform_fee(10000) == 0

    @override_settings(PLATFORM_FEE_PERCENT=20)
    def test_calculate_fee_higher_percent(self):
        """Should handle higher fee percentages."""
        strategy = DirectPaymentStrategy()

        # 20% of 10000 = 2000
        assert strategy.calculate_platform_fee(10000) == 2000


# =============================================================================
# Ledger Account Creation Tests
# =============================================================================


class TestLedgerAccountCreation:
    """Tests for ledger account creation in DirectPaymentStrategy."""

    def test_creates_required_accounts(
        self, pending_payment_order, payment_succeeded_event_data
    ):
        """Should create all required ledger accounts."""
        # Delete any existing accounts
        LedgerAccount.objects.all().delete()

        strategy = DirectPaymentStrategy()
        result = strategy.handle_payment_succeeded(
            pending_payment_order, payment_succeeded_event_data
        )

        assert result.success is True

        # Verify accounts were created
        external = LedgerAccount.objects.filter(
            type=AccountType.EXTERNAL_STRIPE
        ).first()
        escrow = LedgerAccount.objects.filter(type=AccountType.PLATFORM_ESCROW).first()
        revenue = LedgerAccount.objects.filter(
            type=AccountType.PLATFORM_REVENUE
        ).first()

        assert external is not None
        assert external.allow_negative is True

        assert escrow is not None
        assert escrow.allow_negative is False

        assert revenue is not None
        assert revenue.allow_negative is False

    def test_reuses_existing_accounts(
        self, pending_payment_order, payment_succeeded_event_data
    ):
        """Should reuse existing ledger accounts."""
        # Pre-create accounts
        from payments.ledger.services import LedgerService

        LedgerService.get_or_create_account(
            AccountType.EXTERNAL_STRIPE, allow_negative=True
        )
        LedgerService.get_or_create_account(AccountType.PLATFORM_ESCROW)
        LedgerService.get_or_create_account(AccountType.PLATFORM_REVENUE)

        account_count_before = LedgerAccount.objects.count()

        strategy = DirectPaymentStrategy()
        result = strategy.handle_payment_succeeded(
            pending_payment_order, payment_succeeded_event_data
        )

        assert result.success is True

        # Should not create new accounts
        account_count_after = LedgerAccount.objects.count()
        assert account_count_after == account_count_before


# =============================================================================
# Ledger Entry Idempotency Tests
# =============================================================================


class TestLedgerEntryIdempotency:
    """Tests for ledger entry idempotency in DirectPaymentStrategy."""

    def test_ledger_entries_are_idempotent(
        self, pending_payment_order, payment_succeeded_event_data
    ):
        """Should not create duplicate ledger entries on retry."""
        strategy = DirectPaymentStrategy()

        # First call
        result1 = strategy.handle_payment_succeeded(
            pending_payment_order, payment_succeeded_event_data
        )
        assert result1.success is True

        entry_count_after_first = LedgerEntry.objects.filter(
            reference_id=pending_payment_order.id
        ).count()

        # Reload order to get fresh state
        order = PaymentOrder.objects.get(id=pending_payment_order.id)

        # Second call (simulating retry)
        result2 = strategy.handle_payment_succeeded(order, payment_succeeded_event_data)
        assert result2.success is True  # Idempotent success

        entry_count_after_second = LedgerEntry.objects.filter(
            reference_id=pending_payment_order.id
        ).count()

        # Should have same number of entries
        assert entry_count_after_second == entry_count_after_first
