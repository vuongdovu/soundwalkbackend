"""
Tests for payment domain models.

Tests model field validation, constraints, defaults, and basic
functionality for all payment models.
"""

import uuid
from datetime import timedelta

import pytest
from django.db import IntegrityError
from django.utils import timezone

from payments.models import (
    ConnectedAccount,
    FundHold,
    PaymentOrder,
    Payout,
    Refund,
    WebhookEvent,
)
from payments.state_machines import (
    OnboardingStatus,
    PaymentOrderState,
    PaymentStrategyType,
    PayoutState,
    RefundState,
    WebhookEventStatus,
)


# =============================================================================
# ConnectedAccount Tests
# =============================================================================


class TestConnectedAccountModel:
    """Tests for ConnectedAccount model."""

    def test_create_with_required_fields(self, db, profile):
        """Should create account with required fields."""
        account = ConnectedAccount.objects.create(
            profile=profile,
            stripe_account_id="acct_test123",
        )

        assert account.pk is not None
        assert isinstance(account.pk, uuid.UUID)
        assert account.stripe_account_id == "acct_test123"

    def test_default_values(self, db, profile):
        """Should have correct default values."""
        account = ConnectedAccount.objects.create(
            profile=profile,
            stripe_account_id="acct_test123",
        )

        assert account.onboarding_status == OnboardingStatus.NOT_STARTED
        assert account.payouts_enabled is False
        assert account.charges_enabled is False
        assert account.version == 1
        assert account.metadata == {}

    def test_stripe_account_id_unique(self, db, profile, another_profile):
        """Stripe account ID should be unique."""
        ConnectedAccount.objects.create(
            profile=profile,
            stripe_account_id="acct_unique123",
        )

        with pytest.raises(IntegrityError):
            ConnectedAccount.objects.create(
                profile=another_profile,
                stripe_account_id="acct_unique123",
            )

    def test_profile_one_to_one(self, db, profile):
        """Profile can only have one connected account."""
        ConnectedAccount.objects.create(
            profile=profile,
            stripe_account_id="acct_first",
        )

        with pytest.raises(IntegrityError):
            ConnectedAccount.objects.create(
                profile=profile,
                stripe_account_id="acct_second",
            )

    def test_is_ready_for_payouts_false_when_not_complete(self, db, profile):
        """is_ready_for_payouts should be False when not complete."""
        account = ConnectedAccount.objects.create(
            profile=profile,
            stripe_account_id="acct_test",
            onboarding_status=OnboardingStatus.IN_PROGRESS,
            payouts_enabled=True,
        )

        assert account.is_ready_for_payouts is False

    def test_is_ready_for_payouts_false_when_not_enabled(self, db, profile):
        """is_ready_for_payouts should be False when payouts not enabled."""
        account = ConnectedAccount.objects.create(
            profile=profile,
            stripe_account_id="acct_test",
            onboarding_status=OnboardingStatus.COMPLETE,
            payouts_enabled=False,
        )

        assert account.is_ready_for_payouts is False

    def test_is_ready_for_payouts_true_when_complete_and_enabled(self, db, profile):
        """is_ready_for_payouts should be True when complete and enabled."""
        account = ConnectedAccount.objects.create(
            profile=profile,
            stripe_account_id="acct_test",
            onboarding_status=OnboardingStatus.COMPLETE,
            payouts_enabled=True,
        )

        assert account.is_ready_for_payouts is True

    def test_version_increments_on_save(self, db, profile):
        """Version should increment on each save."""
        account = ConnectedAccount.objects.create(
            profile=profile,
            stripe_account_id="acct_test",
        )
        assert account.version == 1

        account.payouts_enabled = True
        account.save()
        assert account.version == 2


# =============================================================================
# PaymentOrder Tests
# =============================================================================


class TestPaymentOrderModel:
    """Tests for PaymentOrder model."""

    def test_create_with_required_fields(self, db, user):
        """Should create order with required fields."""
        order = PaymentOrder.objects.create(
            payer=user,
            amount_cents=5000,
        )

        assert order.pk is not None
        assert isinstance(order.pk, uuid.UUID)
        assert order.amount_cents == 5000

    def test_default_values(self, db, user):
        """Should have correct default values."""
        order = PaymentOrder.objects.create(
            payer=user,
            amount_cents=5000,
        )

        assert order.currency == "usd"
        assert order.strategy_type == PaymentStrategyType.DIRECT
        assert order.state == PaymentOrderState.DRAFT
        assert order.version == 1
        assert order.metadata == {}
        assert order.stripe_payment_intent_id is None

    def test_amount_must_be_positive(self, db, user):
        """Amount must be greater than 0."""
        with pytest.raises(IntegrityError):
            PaymentOrder.objects.create(
                payer=user,
                amount_cents=0,
            )

    def test_stripe_payment_intent_id_unique(self, db, user):
        """Stripe PaymentIntent ID should be unique."""
        PaymentOrder.objects.create(
            payer=user,
            amount_cents=5000,
            stripe_payment_intent_id="pi_test123",
        )

        with pytest.raises(IntegrityError):
            PaymentOrder.objects.create(
                payer=user,
                amount_cents=3000,
                stripe_payment_intent_id="pi_test123",
            )

    def test_stripe_payment_intent_id_nullable(self, db, user):
        """Stripe PaymentIntent ID can be null."""
        order = PaymentOrder.objects.create(
            payer=user,
            amount_cents=5000,
            stripe_payment_intent_id=None,
        )
        assert order.stripe_payment_intent_id is None

    def test_generic_reference_pattern(self, db, user):
        """Should support generic reference pattern."""
        booking_id = uuid.uuid4()
        order = PaymentOrder.objects.create(
            payer=user,
            amount_cents=5000,
            reference_id=booking_id,
            reference_type="booking",
        )

        assert order.reference_id == booking_id
        assert order.reference_type == "booking"

    def test_version_increments_on_save(self, db, user):
        """Version should increment on each save."""
        order = PaymentOrder.objects.create(
            payer=user,
            amount_cents=5000,
        )
        assert order.version == 1

        order.metadata = {"updated": True}
        order.save()
        assert order.version == 2

    def test_str_representation(self, db, user):
        """String representation should include key info."""
        order = PaymentOrder.objects.create(
            payer=user,
            amount_cents=5000,
            currency="usd",
        )
        str_repr = str(order)

        assert "PaymentOrder" in str_repr
        assert "draft" in str_repr
        assert "50.00" in str_repr


# =============================================================================
# FundHold Tests
# =============================================================================


class TestFundHoldModel:
    """Tests for FundHold model."""

    def test_create_with_required_fields(self, db, payment_order):
        """Should create hold with required fields."""
        hold = FundHold.objects.create(
            payment_order=payment_order,
            amount_cents=5000,
            expires_at=timezone.now() + timedelta(days=7),
        )

        assert hold.pk is not None
        assert isinstance(hold.pk, uuid.UUID)

    def test_default_values(self, db, payment_order):
        """Should have correct default values."""
        hold = FundHold.objects.create(
            payment_order=payment_order,
            amount_cents=5000,
            expires_at=timezone.now() + timedelta(days=7),
        )

        assert hold.currency == "usd"
        assert hold.released is False
        assert hold.released_at is None
        assert hold.released_to_payout is None
        assert hold.version == 1
        assert hold.metadata == {}

    def test_amount_must_be_positive(self, db, payment_order):
        """Amount must be greater than 0."""
        with pytest.raises(IntegrityError):
            FundHold.objects.create(
                payment_order=payment_order,
                amount_cents=0,
                expires_at=timezone.now() + timedelta(days=7),
            )

    def test_is_expired_false_before_expiration(self, db, payment_order):
        """is_expired should be False before expiration."""
        hold = FundHold.objects.create(
            payment_order=payment_order,
            amount_cents=5000,
            expires_at=timezone.now() + timedelta(days=7),
        )

        assert hold.is_expired is False

    def test_is_expired_true_after_expiration(self, db, payment_order):
        """is_expired should be True after expiration."""
        hold = FundHold.objects.create(
            payment_order=payment_order,
            amount_cents=5000,
            expires_at=timezone.now() - timedelta(days=1),
        )

        assert hold.is_expired is True

    def test_is_expired_false_when_released(self, db, payment_order):
        """is_expired should be False when released, even if past expiration."""
        hold = FundHold.objects.create(
            payment_order=payment_order,
            amount_cents=5000,
            expires_at=timezone.now() - timedelta(days=1),
            released=True,
        )

        assert hold.is_expired is False

    def test_version_increments_on_save(self, db, payment_order):
        """Version should increment on each save."""
        hold = FundHold.objects.create(
            payment_order=payment_order,
            amount_cents=5000,
            expires_at=timezone.now() + timedelta(days=7),
        )
        assert hold.version == 1

        hold.metadata = {"updated": True}
        hold.save()
        assert hold.version == 2


# =============================================================================
# Payout Tests
# =============================================================================


class TestPayoutModel:
    """Tests for Payout model."""

    def test_create_with_required_fields(self, db, payment_order, connected_account):
        """Should create payout with required fields."""
        payout = Payout.objects.create(
            payment_order=payment_order,
            connected_account=connected_account,
            amount_cents=4500,
        )

        assert payout.pk is not None
        assert isinstance(payout.pk, uuid.UUID)

    def test_default_values(self, db, payment_order, connected_account):
        """Should have correct default values."""
        payout = Payout.objects.create(
            payment_order=payment_order,
            connected_account=connected_account,
            amount_cents=4500,
        )

        assert payout.currency == "usd"
        assert payout.state == PayoutState.PENDING
        assert payout.version == 1
        assert payout.stripe_transfer_id is None

    def test_amount_must_be_positive(self, db, payment_order, connected_account):
        """Amount must be greater than 0."""
        with pytest.raises(IntegrityError):
            Payout.objects.create(
                payment_order=payment_order,
                connected_account=connected_account,
                amount_cents=0,
            )

    def test_stripe_transfer_id_unique(self, db, payment_order, connected_account):
        """Stripe Transfer ID should be unique."""
        Payout.objects.create(
            payment_order=payment_order,
            connected_account=connected_account,
            amount_cents=4500,
            stripe_transfer_id="tr_test123",
        )

        with pytest.raises(IntegrityError):
            Payout.objects.create(
                payment_order=payment_order,
                connected_account=connected_account,
                amount_cents=2000,
                stripe_transfer_id="tr_test123",
            )

    def test_version_increments_on_save(self, db, payment_order, connected_account):
        """Version should increment on each save."""
        payout = Payout.objects.create(
            payment_order=payment_order,
            connected_account=connected_account,
            amount_cents=4500,
        )
        assert payout.version == 1

        payout.metadata = {"updated": True}
        payout.save()
        assert payout.version == 2


# =============================================================================
# Refund Tests
# =============================================================================


class TestRefundModel:
    """Tests for Refund model."""

    def test_create_with_required_fields(self, db, payment_order):
        """Should create refund with required fields."""
        refund = Refund.objects.create(
            payment_order=payment_order,
            amount_cents=2500,
        )

        assert refund.pk is not None
        assert isinstance(refund.pk, uuid.UUID)

    def test_default_values(self, db, payment_order):
        """Should have correct default values."""
        refund = Refund.objects.create(
            payment_order=payment_order,
            amount_cents=2500,
        )

        assert refund.currency == "usd"
        assert refund.state == RefundState.REQUESTED
        assert refund.version == 1
        assert refund.reason is None

    def test_amount_must_be_positive(self, db, payment_order):
        """Amount must be greater than 0."""
        with pytest.raises(IntegrityError):
            Refund.objects.create(
                payment_order=payment_order,
                amount_cents=0,
            )

    def test_stripe_refund_id_unique(self, db, payment_order):
        """Stripe Refund ID should be unique."""
        Refund.objects.create(
            payment_order=payment_order,
            amount_cents=2500,
            stripe_refund_id="re_test123",
        )

        with pytest.raises(IntegrityError):
            Refund.objects.create(
                payment_order=payment_order,
                amount_cents=1000,
                stripe_refund_id="re_test123",
            )

    def test_version_increments_on_save(self, db, payment_order):
        """Version should increment on each save."""
        refund = Refund.objects.create(
            payment_order=payment_order,
            amount_cents=2500,
        )
        assert refund.version == 1

        refund.reason = "Customer request"
        refund.save()
        assert refund.version == 2


# =============================================================================
# WebhookEvent Tests
# =============================================================================


class TestWebhookEventModel:
    """Tests for WebhookEvent model."""

    def test_create_with_required_fields(self, db):
        """Should create event with required fields."""
        event = WebhookEvent.objects.create(
            stripe_event_id="evt_test123",
            event_type="payment_intent.succeeded",
            payload={"id": "pi_test", "object": "payment_intent"},
        )

        assert event.pk is not None
        assert isinstance(event.pk, uuid.UUID)

    def test_default_values(self, db):
        """Should have correct default values."""
        event = WebhookEvent.objects.create(
            stripe_event_id="evt_test123",
            event_type="payment_intent.succeeded",
            payload={},
        )

        assert event.status == WebhookEventStatus.PENDING
        assert event.retry_count == 0
        assert event.processed_at is None
        assert event.error_message is None

    def test_stripe_event_id_unique(self, db):
        """Stripe Event ID should be unique (idempotency)."""
        WebhookEvent.objects.create(
            stripe_event_id="evt_unique123",
            event_type="payment_intent.succeeded",
            payload={},
        )

        with pytest.raises(IntegrityError):
            WebhookEvent.objects.create(
                stripe_event_id="evt_unique123",
                event_type="payment_intent.failed",
                payload={},
            )

    def test_is_processed_property(self, db):
        """is_processed should reflect status."""
        event = WebhookEvent.objects.create(
            stripe_event_id="evt_test",
            event_type="payment_intent.succeeded",
            payload={},
        )

        assert event.is_processed is False

        event.status = WebhookEventStatus.PROCESSED
        assert event.is_processed is True

    def test_get_object_id(self, db):
        """get_object_id should extract ID from payload."""
        event = WebhookEvent.objects.create(
            stripe_event_id="evt_test",
            event_type="payment_intent.succeeded",
            payload={
                "data": {
                    "object": {
                        "id": "pi_12345",
                        "object": "payment_intent",
                    }
                }
            },
        )

        assert event.get_object_id() == "pi_12345"

    def test_mark_processing(self, db):
        """mark_processing should update status and increment retry."""
        event = WebhookEvent.objects.create(
            stripe_event_id="evt_test",
            event_type="payment_intent.succeeded",
            payload={},
        )

        event.mark_processing()

        assert event.status == WebhookEventStatus.PROCESSING
        assert event.retry_count == 1

    def test_mark_processed(self, db):
        """mark_processed should update status and timestamp."""
        event = WebhookEvent.objects.create(
            stripe_event_id="evt_test",
            event_type="payment_intent.succeeded",
            payload={},
        )

        event.mark_processed()

        assert event.status == WebhookEventStatus.PROCESSED
        assert event.processed_at is not None

    def test_mark_failed(self, db):
        """mark_failed should update status and error message."""
        event = WebhookEvent.objects.create(
            stripe_event_id="evt_test",
            event_type="payment_intent.succeeded",
            payload={},
        )

        event.mark_failed("Test error")

        assert event.status == WebhookEventStatus.FAILED
        assert event.error_message == "Test error"


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def user(db):
    """Create a test user."""
    from django.contrib.auth import get_user_model

    User = get_user_model()
    return User.objects.create_user(
        email="test@example.com",
        password="testpass123",
    )


@pytest.fixture
def another_user(db):
    """Create another test user."""
    from django.contrib.auth import get_user_model

    User = get_user_model()
    return User.objects.create_user(
        email="another@example.com",
        password="testpass123",
    )


@pytest.fixture
def profile(db, user):
    """Get or create profile for test user."""
    from authentication.models import Profile

    profile, _ = Profile.objects.get_or_create(user=user)
    return profile


@pytest.fixture
def another_profile(db, another_user):
    """Get or create profile for another user."""
    from authentication.models import Profile

    profile, _ = Profile.objects.get_or_create(user=another_user)
    return profile


@pytest.fixture
def connected_account(db, profile):
    """Create a test connected account."""
    return ConnectedAccount.objects.create(
        profile=profile,
        stripe_account_id="acct_fixture123",
        onboarding_status=OnboardingStatus.COMPLETE,
        payouts_enabled=True,
    )


@pytest.fixture
def payment_order(db, user):
    """Create a test payment order."""
    return PaymentOrder.objects.create(
        payer=user,
        amount_cents=10000,
        currency="usd",
        strategy_type=PaymentStrategyType.DIRECT,
    )
