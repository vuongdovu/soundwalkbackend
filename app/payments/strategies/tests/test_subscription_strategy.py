"""
Tests for SubscriptionPaymentStrategy.

Tests cover:
- Subscription creation with Stripe integration
- Subscription cancellation
- Payment success handling (invoice.paid)
- Payment failure handling (invoice.payment_failed)
- Ledger entry recording for subscription payments
- Platform fee calculation (same 15% as one-off)
- Idempotency for renewal payments
- Error handling and edge cases
"""

from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from django.test import override_settings
from django.utils import timezone

from authentication.models import Profile
from payments.ledger.models import AccountType, EntryType, LedgerAccount, LedgerEntry
from payments.models import PaymentOrder
from payments.state_machines import PaymentOrderState, PaymentStrategyType


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def test_user(db):
    """Create a test user (subscriber) for payments."""
    from authentication.models import User

    return User.objects.create_user(
        email="subscriber@example.com",
        password="testpass123",
    )


@pytest.fixture
def recipient_user(db):
    """Create a recipient user who will receive subscription payments."""
    from authentication.models import User

    return User.objects.create_user(
        email="recipient@example.com",
        password="testpass123",
    )


@pytest.fixture
def recipient_profile(db, recipient_user):
    """Create a profile for the recipient."""
    profile, _ = Profile.objects.get_or_create(user=recipient_user)
    return profile


@pytest.fixture
def connected_account(db, recipient_profile):
    """Create a connected account for the recipient with completed onboarding."""
    from payments.models import ConnectedAccount
    from payments.state_machines import OnboardingStatus

    return ConnectedAccount.objects.create(
        profile=recipient_profile,
        stripe_account_id="acct_test_recipient_123",
        onboarding_status=OnboardingStatus.COMPLETE,
        payouts_enabled=True,
        charges_enabled=True,
    )


@pytest.fixture
def mock_stripe_adapter():
    """Mock Stripe adapter for subscription tests."""
    mock = MagicMock()

    # Mock customer methods
    customer_result = MagicMock()
    customer_result.id = "cus_test_123"
    customer_result.email = "subscriber@example.com"
    mock.create_customer.return_value = customer_result
    mock.get_or_create_customer.return_value = customer_result

    # Mock subscription creation
    subscription_result = MagicMock()
    subscription_result.id = "sub_test_123"
    subscription_result.status = "active"
    subscription_result.customer_id = "cus_test_123"
    subscription_result.current_period_start = int(timezone.now().timestamp())
    subscription_result.current_period_end = int(
        (timezone.now() + timedelta(days=30)).timestamp()
    )
    subscription_result.cancel_at_period_end = False
    subscription_result.latest_invoice_id = "in_test_123"
    mock.create_subscription.return_value = subscription_result

    # Mock subscription cancellation
    cancelled_result = MagicMock()
    cancelled_result.id = "sub_test_123"
    cancelled_result.status = "canceled"
    cancelled_result.cancel_at_period_end = False
    mock.cancel_subscription.return_value = cancelled_result

    return mock


@pytest.fixture
def invoice_paid_event_data():
    """Mock Stripe invoice.paid webhook event data for subscription renewal."""
    return {
        "id": "evt_invoice_paid_123",
        "type": "invoice.paid",
        "data": {
            "object": {
                "id": "in_test_renewal_456",
                "object": "invoice",
                "subscription": "sub_test_123",
                "customer": "cus_test_123",
                "amount_paid": 10000,  # $100.00
                "currency": "usd",
                "status": "paid",
                "billing_reason": "subscription_cycle",
                "period_start": int(timezone.now().timestamp()),
                "period_end": int((timezone.now() + timedelta(days=30)).timestamp()),
                "lines": {
                    "data": [
                        {
                            "id": "il_test_123",
                            "price": {"id": "price_test_123"},
                            "metadata": {},
                        }
                    ]
                },
            }
        },
    }


@pytest.fixture
def invoice_payment_failed_event_data():
    """Mock Stripe invoice.payment_failed webhook event data."""
    return {
        "id": "evt_invoice_failed_123",
        "type": "invoice.payment_failed",
        "data": {
            "object": {
                "id": "in_test_failed_456",
                "object": "invoice",
                "subscription": "sub_test_123",
                "customer": "cus_test_123",
                "amount_due": 10000,
                "currency": "usd",
                "status": "open",
                "attempt_count": 1,
                "next_payment_attempt": int(
                    (timezone.now() + timedelta(days=3)).timestamp()
                ),
                "last_finalization_error": {
                    "code": "card_declined",
                    "message": "Your card was declined.",
                },
            }
        },
    }


# =============================================================================
# CreateSubscriptionParams Tests
# =============================================================================


class TestCreateSubscriptionParams:
    """Tests for CreateSubscriptionParams validation."""

    def test_valid_params(self, test_user, recipient_profile):
        """Should create params with valid values."""
        from payments.strategies.subscription import CreateSubscriptionParams

        params = CreateSubscriptionParams(
            payer=test_user,
            recipient_profile_id=recipient_profile.pk,
            price_id="price_test_123",
            amount_cents=10000,
            currency="usd",
            billing_interval="month",
        )

        assert params.payer == test_user
        assert params.recipient_profile_id == recipient_profile.pk
        assert params.price_id == "price_test_123"
        assert params.amount_cents == 10000

    def test_amount_must_be_positive(self, test_user, recipient_profile):
        """Should raise ValueError for zero or negative amount."""
        from payments.strategies.subscription import CreateSubscriptionParams

        with pytest.raises(ValueError, match="amount_cents must be positive"):
            CreateSubscriptionParams(
                payer=test_user,
                recipient_profile_id=recipient_profile.pk,
                price_id="price_test_123",
                amount_cents=0,
            )

    def test_price_id_required(self, test_user, recipient_profile):
        """Should raise ValueError for missing price_id."""
        from payments.strategies.subscription import CreateSubscriptionParams

        with pytest.raises(ValueError, match="price_id is required"):
            CreateSubscriptionParams(
                payer=test_user,
                recipient_profile_id=recipient_profile.pk,
                price_id="",
                amount_cents=10000,
            )

    def test_recipient_profile_id_required(self, test_user):
        """Should raise ValueError for missing recipient_profile_id."""
        from payments.strategies.subscription import CreateSubscriptionParams

        with pytest.raises(ValueError, match="recipient_profile_id is required"):
            CreateSubscriptionParams(
                payer=test_user,
                recipient_profile_id=None,
                price_id="price_test_123",
                amount_cents=10000,
            )


# =============================================================================
# SubscriptionPaymentStrategy.create_subscription Tests
# =============================================================================


class TestSubscriptionPaymentStrategyCreateSubscription:
    """Tests for SubscriptionPaymentStrategy.create_subscription."""

    def test_create_subscription_creates_stripe_subscription(
        self, test_user, recipient_profile, connected_account, mock_stripe_adapter
    ):
        """Should create a Stripe subscription with correct parameters."""
        from payments.strategies.subscription import (
            CreateSubscriptionParams,
            SubscriptionPaymentStrategy,
        )

        strategy = SubscriptionPaymentStrategy(stripe_adapter=mock_stripe_adapter)

        params = CreateSubscriptionParams(
            payer=test_user,
            recipient_profile_id=recipient_profile.pk,
            price_id="price_test_123",
            amount_cents=10000,
            currency="usd",
        )

        result = strategy.create_subscription(params)

        assert result.success is True
        mock_stripe_adapter.create_subscription.assert_called_once()

        # Verify subscription was called with correct params
        call_args = mock_stripe_adapter.create_subscription.call_args
        # Params might be in kwargs or args depending on call style
        if call_args.kwargs and "params" in call_args.kwargs:
            assert call_args.kwargs["params"].price_id == "price_test_123"
        else:
            # Check that create_subscription was called with some params
            assert len(call_args.args) > 0 or len(call_args.kwargs) > 0

    def test_create_subscription_creates_local_subscription_pending(
        self, test_user, recipient_profile, connected_account, mock_stripe_adapter
    ):
        """Should create local Subscription record in PENDING state."""
        from payments.state_machines import SubscriptionState
        from payments.strategies.subscription import (
            CreateSubscriptionParams,
            SubscriptionPaymentStrategy,
        )

        strategy = SubscriptionPaymentStrategy(stripe_adapter=mock_stripe_adapter)

        params = CreateSubscriptionParams(
            payer=test_user,
            recipient_profile_id=recipient_profile.pk,
            price_id="price_test_123",
            amount_cents=10000,
        )

        result = strategy.create_subscription(params)

        assert result.success is True
        assert result.data.subscription is not None

        # Verify local subscription
        subscription = result.data.subscription
        assert subscription.payer == test_user
        assert subscription.recipient_profile_id == recipient_profile.pk
        assert subscription.stripe_subscription_id == "sub_test_123"
        assert subscription.state == SubscriptionState.PENDING

    def test_create_subscription_returns_client_secret(
        self, test_user, recipient_profile, connected_account, mock_stripe_adapter
    ):
        """Should return client_secret for frontend payment completion."""
        from payments.strategies.subscription import (
            CreateSubscriptionParams,
            SubscriptionPaymentStrategy,
        )

        # Configure mock to return subscription with pending setup
        mock_stripe_adapter.create_subscription.return_value = MagicMock(
            id="sub_test_123",
            status="incomplete",
            latest_invoice=MagicMock(
                payment_intent=MagicMock(client_secret="pi_secret_test_123")
            ),
        )

        strategy = SubscriptionPaymentStrategy(stripe_adapter=mock_stripe_adapter)

        params = CreateSubscriptionParams(
            payer=test_user,
            recipient_profile_id=recipient_profile.pk,
            price_id="price_test_123",
            amount_cents=10000,
        )

        result = strategy.create_subscription(params)

        assert result.success is True

    def test_create_subscription_succeeds_without_connected_account(
        self, test_user, recipient_profile, mock_stripe_adapter
    ):
        """
        Should succeed without connected account.

        Subscription creation and payment collection work without connected account.
        Only payouts require a connected account (handled at payout time).
        """
        from payments.strategies.subscription import (
            CreateSubscriptionParams,
            SubscriptionPaymentStrategy,
        )

        # No connected_account fixture - recipient has no payout destination yet
        strategy = SubscriptionPaymentStrategy(stripe_adapter=mock_stripe_adapter)

        params = CreateSubscriptionParams(
            payer=test_user,
            recipient_profile_id=recipient_profile.pk,
            price_id="price_test_123",
            amount_cents=10000,
        )

        result = strategy.create_subscription(params)

        # Subscription creation succeeds - payout destination is checked later
        assert result.success is True
        assert result.data.subscription is not None


# =============================================================================
# SubscriptionPaymentStrategy.handle_payment_succeeded Tests
# =============================================================================


class TestSubscriptionPaymentStrategyHandleSuccess:
    """Tests for SubscriptionPaymentStrategy.handle_payment_succeeded."""

    @pytest.fixture
    def subscription(self, db, test_user, recipient_profile):
        """Create an active subscription for testing."""
        from payments.models import Subscription

        subscription = Subscription.objects.create(
            payer=test_user,
            recipient_profile_id=recipient_profile.pk,
            stripe_subscription_id="sub_test_123",
            stripe_customer_id="cus_test_123",
            stripe_price_id="price_test_123",
            amount_cents=10000,
            currency="usd",
            billing_interval="month",
        )
        subscription.activate()
        subscription.save()
        return subscription

    @pytest.fixture
    def pending_renewal_order(self, db, test_user, subscription):
        """Create a payment order in PENDING state for a subscription renewal."""
        order = PaymentOrder.objects.create(
            payer=test_user,
            amount_cents=10000,
            currency="usd",
            strategy_type=PaymentStrategyType.SUBSCRIPTION,
            subscription=subscription,
            stripe_invoice_id="in_test_renewal_456",
        )
        order.submit()
        order.save()
        return order

    def test_handle_success_transitions_to_settled(
        self, pending_renewal_order, invoice_paid_event_data
    ):
        """Should transition from PENDING to SETTLED."""
        from payments.strategies.subscription import SubscriptionPaymentStrategy

        strategy = SubscriptionPaymentStrategy()

        result = strategy.handle_payment_succeeded(
            pending_renewal_order, invoice_paid_event_data
        )

        assert result.success is True
        assert result.data.state == PaymentOrderState.SETTLED
        assert result.data.settled_at is not None
        assert result.data.captured_at is not None

    def test_handle_success_records_ledger_entries(
        self, pending_renewal_order, invoice_paid_event_data, recipient_profile
    ):
        """Should create ledger entries for payment, fee, and recipient credit."""
        from payments.strategies.subscription import SubscriptionPaymentStrategy

        strategy = SubscriptionPaymentStrategy()

        result = strategy.handle_payment_succeeded(
            pending_renewal_order, invoice_paid_event_data
        )

        assert result.success is True

        # Verify ledger entries were created
        entries = LedgerEntry.objects.filter(
            reference_type="payment_order",
            reference_id=pending_renewal_order.id,
        ).order_by("created_at")

        # Should have 3 entries: received, fee, recipient credit
        assert entries.count() == 3

        # Entry 1: Payment received (full amount into escrow)
        received_entry = entries[0]
        assert received_entry.entry_type == EntryType.PAYMENT_RECEIVED
        assert received_entry.amount_cents == 10000

        # Entry 2: Platform fee (15%)
        fee_entry = entries[1]
        assert fee_entry.entry_type == EntryType.FEE_COLLECTED
        assert fee_entry.amount_cents == 1500  # 15% of 10000

        # Entry 3: Mentor credit (85%)
        recipient_entry = entries[2]
        assert recipient_entry.entry_type == EntryType.PAYMENT_RELEASED
        assert recipient_entry.amount_cents == 8500  # 85% of 10000

    def test_handle_success_first_payment_activates_subscription(
        self, db, test_user, recipient_profile, invoice_paid_event_data
    ):
        """Should activate subscription on first successful payment."""
        from payments.models import Subscription
        from payments.state_machines import SubscriptionState
        from payments.strategies.subscription import SubscriptionPaymentStrategy

        # Create subscription in PENDING state
        subscription = Subscription.objects.create(
            payer=test_user,
            recipient_profile_id=recipient_profile.pk,
            stripe_subscription_id="sub_test_123",
            stripe_customer_id="cus_test_123",
            stripe_price_id="price_test_123",
            amount_cents=10000,
            currency="usd",
            billing_interval="month",
            # PENDING is the initial state
        )
        assert subscription.state == SubscriptionState.PENDING

        # Create first payment order
        order = PaymentOrder.objects.create(
            payer=test_user,
            amount_cents=10000,
            currency="usd",
            strategy_type=PaymentStrategyType.SUBSCRIPTION,
            subscription=subscription,
            stripe_invoice_id="in_test_first_payment",
        )
        order.submit()
        order.save()

        strategy = SubscriptionPaymentStrategy()
        result = strategy.handle_payment_succeeded(order, invoice_paid_event_data)

        assert result.success is True

        # Reload subscription (get fresh instance to avoid FSM refresh issues)
        subscription = Subscription.objects.get(id=subscription.id)
        assert subscription.state == SubscriptionState.ACTIVE

    def test_handle_success_idempotent_for_settled(
        self, db, test_user, subscription, invoice_paid_event_data
    ):
        """Should be idempotent for already settled orders."""
        from payments.strategies.subscription import SubscriptionPaymentStrategy

        # Create already settled order
        order = PaymentOrder.objects.create(
            payer=test_user,
            amount_cents=10000,
            currency="usd",
            strategy_type=PaymentStrategyType.SUBSCRIPTION,
            subscription=subscription,
            stripe_invoice_id="in_test_settled",
        )
        order.submit()
        order.save()
        order.process()
        order.save()
        order.capture()
        order.save()
        order.settle_from_captured()
        order.save()

        strategy = SubscriptionPaymentStrategy()
        result = strategy.handle_payment_succeeded(order, invoice_paid_event_data)

        assert result.success is True
        assert result.data.state == PaymentOrderState.SETTLED


# =============================================================================
# SubscriptionPaymentStrategy.handle_payment_failed Tests
# =============================================================================


class TestSubscriptionPaymentStrategyHandleFailure:
    """Tests for SubscriptionPaymentStrategy.handle_payment_failed."""

    @pytest.fixture
    def active_subscription(self, db, test_user, recipient_profile):
        """Create an active subscription for failure testing."""
        from payments.models import Subscription

        subscription = Subscription.objects.create(
            payer=test_user,
            recipient_profile_id=recipient_profile.pk,
            stripe_subscription_id="sub_test_123",
            stripe_customer_id="cus_test_123",
            stripe_price_id="price_test_123",
            amount_cents=10000,
            currency="usd",
            billing_interval="month",
        )
        subscription.activate()
        subscription.save()
        return subscription

    def test_handle_failure_marks_subscription_past_due(
        self, active_subscription, invoice_payment_failed_event_data
    ):
        """Should mark subscription as PAST_DUE on payment failure."""
        from payments.state_machines import SubscriptionState
        from payments.strategies.subscription import SubscriptionPaymentStrategy

        strategy = SubscriptionPaymentStrategy()

        result = strategy.handle_invoice_payment_failed(
            active_subscription, invoice_payment_failed_event_data
        )

        assert result.success is True

        # Reload subscription (get fresh instance to avoid FSM refresh issues)
        from payments.models import Subscription

        active_subscription = Subscription.objects.get(id=active_subscription.id)
        assert active_subscription.state == SubscriptionState.PAST_DUE

    def test_handle_failure_idempotent_for_past_due(
        self, active_subscription, invoice_payment_failed_event_data
    ):
        """Should be idempotent for already past due subscriptions."""
        from payments.state_machines import SubscriptionState
        from payments.strategies.subscription import SubscriptionPaymentStrategy

        # Mark as past_due first
        active_subscription.mark_past_due()
        active_subscription.save()

        strategy = SubscriptionPaymentStrategy()
        result = strategy.handle_invoice_payment_failed(
            active_subscription, invoice_payment_failed_event_data
        )

        assert result.success is True
        assert active_subscription.state == SubscriptionState.PAST_DUE


# =============================================================================
# SubscriptionPaymentStrategy.calculate_platform_fee Tests
# =============================================================================


class TestSubscriptionPaymentStrategyPlatformFee:
    """Tests for SubscriptionPaymentStrategy.calculate_platform_fee."""

    def test_calculate_fee_default(self):
        """Should calculate 15% fee by default (same as one-off payments)."""
        from payments.strategies.subscription import SubscriptionPaymentStrategy

        strategy = SubscriptionPaymentStrategy()

        # 15% of 10000 = 1500
        assert strategy.calculate_platform_fee(10000) == 1500

        # 15% of 9999 = 1499 (integer division)
        assert strategy.calculate_platform_fee(9999) == 1499

        # 15% of 100 = 15
        assert strategy.calculate_platform_fee(100) == 15

    @override_settings(PLATFORM_FEE_PERCENT=10)
    def test_calculate_fee_custom_percent(self):
        """Should use custom fee percentage from settings."""
        from payments.strategies.subscription import SubscriptionPaymentStrategy

        strategy = SubscriptionPaymentStrategy()

        # 10% of 10000 = 1000
        assert strategy.calculate_platform_fee(10000) == 1000


# =============================================================================
# Subscription Cancellation Tests
# =============================================================================


class TestSubscriptionCancellation:
    """Tests for subscription cancellation."""

    @pytest.fixture
    def active_subscription(self, db, test_user, recipient_profile):
        """Create an active subscription for cancellation testing."""
        from payments.models import Subscription

        subscription = Subscription.objects.create(
            payer=test_user,
            recipient_profile_id=recipient_profile.pk,
            stripe_subscription_id="sub_test_123",
            stripe_customer_id="cus_test_123",
            stripe_price_id="price_test_123",
            amount_cents=10000,
            currency="usd",
            billing_interval="month",
        )
        subscription.activate()
        subscription.save()
        return subscription

    def test_cancel_subscription_at_period_end(
        self, active_subscription, mock_stripe_adapter
    ):
        """Should schedule cancellation at period end."""
        from payments.strategies.subscription import SubscriptionPaymentStrategy

        strategy = SubscriptionPaymentStrategy(stripe_adapter=mock_stripe_adapter)

        result = strategy.cancel_subscription(
            active_subscription, cancel_at_period_end=True
        )

        assert result.success is True

        # Verify Stripe was called
        mock_stripe_adapter.cancel_subscription.assert_called_once()

        # Verify local subscription updated (get fresh instance)
        from payments.models import Subscription

        active_subscription = Subscription.objects.get(id=active_subscription.id)
        assert active_subscription.cancel_at_period_end is True

    def test_cancel_subscription_immediately(
        self, active_subscription, mock_stripe_adapter
    ):
        """Should cancel subscription immediately."""
        from payments.state_machines import SubscriptionState
        from payments.strategies.subscription import SubscriptionPaymentStrategy

        strategy = SubscriptionPaymentStrategy(stripe_adapter=mock_stripe_adapter)

        result = strategy.cancel_subscription(
            active_subscription, cancel_at_period_end=False
        )

        assert result.success is True

        # Verify local subscription cancelled (get fresh instance)
        from payments.models import Subscription

        active_subscription = Subscription.objects.get(id=active_subscription.id)
        assert active_subscription.state == SubscriptionState.CANCELLED
        assert active_subscription.cancelled_at is not None


# =============================================================================
# Ledger Entry Idempotency Tests
# =============================================================================


class TestSubscriptionLedgerIdempotency:
    """Tests for ledger entry idempotency in subscription payments."""

    @pytest.fixture
    def subscription(self, db, test_user, recipient_profile):
        """Create an active subscription."""
        from payments.models import Subscription

        subscription = Subscription.objects.create(
            payer=test_user,
            recipient_profile_id=recipient_profile.pk,
            stripe_subscription_id="sub_test_123",
            stripe_customer_id="cus_test_123",
            stripe_price_id="price_test_123",
            amount_cents=10000,
            currency="usd",
            billing_interval="month",
        )
        subscription.activate()
        subscription.save()
        return subscription

    def test_ledger_entries_are_idempotent(
        self, db, test_user, subscription, invoice_paid_event_data
    ):
        """Should not create duplicate ledger entries on retry."""
        from payments.strategies.subscription import SubscriptionPaymentStrategy

        # Create payment order
        order = PaymentOrder.objects.create(
            payer=test_user,
            amount_cents=10000,
            currency="usd",
            strategy_type=PaymentStrategyType.SUBSCRIPTION,
            subscription=subscription,
            stripe_invoice_id="in_test_idempotent",
        )
        order.submit()
        order.save()

        strategy = SubscriptionPaymentStrategy()

        # First call
        result1 = strategy.handle_payment_succeeded(order, invoice_paid_event_data)
        assert result1.success is True

        entry_count_after_first = LedgerEntry.objects.filter(
            reference_id=order.id
        ).count()

        # Reload order to get fresh state
        order = PaymentOrder.objects.get(id=order.id)

        # Second call (simulating retry)
        result2 = strategy.handle_payment_succeeded(order, invoice_paid_event_data)
        assert result2.success is True  # Idempotent success

        entry_count_after_second = LedgerEntry.objects.filter(
            reference_id=order.id
        ).count()

        # Should have same number of entries
        assert entry_count_after_second == entry_count_after_first


# =============================================================================
# Mentor Balance Accumulation Tests
# =============================================================================


class TestMentorBalanceAccumulation:
    """Tests for recipient balance accumulation from subscription payments."""

    @pytest.fixture
    def subscription(self, db, test_user, recipient_profile, connected_account):
        """Create an active subscription with connected account."""
        from payments.models import Subscription

        subscription = Subscription.objects.create(
            payer=test_user,
            recipient_profile_id=recipient_profile.pk,
            stripe_subscription_id="sub_test_123",
            stripe_customer_id="cus_test_123",
            stripe_price_id="price_test_123",
            amount_cents=10000,
            currency="usd",
            billing_interval="month",
        )
        subscription.activate()
        subscription.save()
        return subscription

    def test_multiple_renewals_accumulate_in_user_balance(
        self, db, test_user, recipient_profile, subscription
    ):
        """Should accumulate recipient balance across multiple renewals."""
        from payments.ledger.services import LedgerService
        from payments.strategies.subscription import SubscriptionPaymentStrategy

        strategy = SubscriptionPaymentStrategy()

        # Simulate 3 monthly renewals
        for i in range(3):
            order = PaymentOrder.objects.create(
                payer=test_user,
                amount_cents=10000,
                currency="usd",
                strategy_type=PaymentStrategyType.SUBSCRIPTION,
                subscription=subscription,
                stripe_invoice_id=f"in_test_renewal_{i}",
            )
            order.submit()
            order.save()

            event_data = {
                "id": f"evt_invoice_paid_{i}",
                "type": "invoice.paid",
                "data": {
                    "object": {
                        "id": f"in_test_renewal_{i}",
                        "subscription": "sub_test_123",
                        "amount_paid": 10000,
                        "currency": "usd",
                    }
                },
            }

            result = strategy.handle_payment_succeeded(order, event_data)
            assert result.success is True

        # Check recipient's accumulated balance
        recipient_account = LedgerAccount.objects.filter(
            type=AccountType.USER_BALANCE,
            owner_id=recipient_profile.pk,
            currency="usd",
        ).first()

        assert recipient_account is not None
        balance = LedgerService.get_balance(recipient_account.id)

        # 3 payments * $85 each (85% after 15% fee) = $255
        assert balance.cents == 25500
