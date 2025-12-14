"""
Pytest fixtures for payment tests.

This module provides fixtures for creating payment-related test data.
Fixtures are designed to provide objects in various states for testing
state transitions and business logic.

Usage:
    def test_capture_payment(processing_order):
        processing_order.capture()
        processing_order.save()
        assert processing_order.state == PaymentOrderState.CAPTURED
"""

import pytest
from django.utils import timezone

from payments.state_machines import (
    OnboardingStatus,
    PaymentStrategyType,
)
from payments.tests.factories import (
    ConnectedAccountFactory,
    FundHoldFactory,
    PaymentOrderFactory,
    PayoutFactory,
    RefundFactory,
    UserFactory,
    WebhookEventFactory,
)


# =============================================================================
# User and Profile Fixtures
# =============================================================================


@pytest.fixture
def user(db):
    """Create a test user."""
    return UserFactory()


@pytest.fixture
def profile(db, user):
    """Get or create profile for test user."""
    from authentication.models import Profile

    profile, _ = Profile.objects.get_or_create(user=user)
    return profile


# =============================================================================
# Connected Account Fixtures
# =============================================================================


@pytest.fixture
def connected_account(db, profile):
    """Create a connected account with complete onboarding."""
    return ConnectedAccountFactory(profile=profile)


@pytest.fixture
def incomplete_connected_account(db, profile):
    """Create a connected account with in-progress onboarding."""
    return ConnectedAccountFactory(
        profile=profile,
        onboarding_status=OnboardingStatus.IN_PROGRESS,
        payouts_enabled=False,
        charges_enabled=False,
    )


# =============================================================================
# PaymentOrder State Fixtures
# =============================================================================


@pytest.fixture
def draft_order(db, user):
    """Create a draft payment order."""
    return PaymentOrderFactory(payer=user)


@pytest.fixture
def pending_order(db, user):
    """Create a pending payment order."""
    order = PaymentOrderFactory(payer=user)
    order.submit()
    order.save()
    return order


@pytest.fixture
def processing_order(db, user):
    """Create a processing payment order."""
    order = PaymentOrderFactory(payer=user)
    order.submit()
    order.save()
    order.process()
    order.save()
    return order


@pytest.fixture
def captured_order(db, user):
    """Create a captured payment order (direct path)."""
    order = PaymentOrderFactory(payer=user)
    order.submit()
    order.save()
    order.process()
    order.save()
    order.capture()
    order.save()
    return order


@pytest.fixture
def held_order(db, user):
    """Create a held payment order (escrow path)."""
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
    return order


@pytest.fixture
def released_order(db, user):
    """Create a released payment order (escrow path)."""
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
    return order


@pytest.fixture
def settled_order(db, user):
    """Create a settled payment order (direct path)."""
    order = PaymentOrderFactory(payer=user)
    order.submit()
    order.save()
    order.process()
    order.save()
    order.capture()
    order.save()
    order.settle_from_captured()
    order.save()
    return order


@pytest.fixture
def failed_order(db, user):
    """Create a failed payment order."""
    order = PaymentOrderFactory(payer=user)
    order.submit()
    order.save()
    order.process()
    order.save()
    order.fail(reason="Card declined")
    order.save()
    return order


@pytest.fixture
def cancelled_order(db, user):
    """Create a cancelled payment order."""
    order = PaymentOrderFactory(payer=user)
    order.cancel()
    order.save()
    return order


@pytest.fixture
def refunded_order(db, user):
    """Create a refunded payment order."""
    order = PaymentOrderFactory(payer=user)
    order.submit()
    order.save()
    order.process()
    order.save()
    order.capture()
    order.save()
    order.refund_full()
    order.save()
    return order


# =============================================================================
# Payout State Fixtures
# =============================================================================


@pytest.fixture
def pending_payout(db, captured_order, connected_account):
    """Create a pending payout."""
    return PayoutFactory(
        payment_order=captured_order,
        connected_account=connected_account,
    )


@pytest.fixture
def scheduled_payout(db, captured_order, connected_account):
    """Create a scheduled payout."""
    payout = PayoutFactory(
        payment_order=captured_order,
        connected_account=connected_account,
    )
    future_time = timezone.now() + timezone.timedelta(days=1)
    payout.schedule(scheduled_for=future_time)
    payout.save()
    return payout


@pytest.fixture
def processing_payout(db, captured_order, connected_account):
    """Create a processing payout."""
    payout = PayoutFactory(
        payment_order=captured_order,
        connected_account=connected_account,
    )
    payout.process()
    payout.save()
    return payout


@pytest.fixture
def paid_payout(db, captured_order, connected_account):
    """Create a paid payout."""
    payout = PayoutFactory(
        payment_order=captured_order,
        connected_account=connected_account,
    )
    payout.process()
    payout.save()
    payout.complete()
    payout.save()
    return payout


@pytest.fixture
def failed_payout(db, captured_order, connected_account):
    """Create a failed payout."""
    payout = PayoutFactory(
        payment_order=captured_order,
        connected_account=connected_account,
    )
    payout.process()
    payout.save()
    payout.fail(reason="Account suspended")
    payout.save()
    return payout


# =============================================================================
# Refund State Fixtures
# =============================================================================


@pytest.fixture
def requested_refund(db, captured_order):
    """Create a requested refund."""
    return RefundFactory(payment_order=captured_order)


@pytest.fixture
def processing_refund(db, captured_order):
    """Create a processing refund."""
    refund = RefundFactory(payment_order=captured_order)
    refund.process()
    refund.save()
    return refund


@pytest.fixture
def completed_refund(db, captured_order):
    """Create a completed refund."""
    refund = RefundFactory(payment_order=captured_order)
    refund.process()
    refund.save()
    refund.complete()
    refund.save()
    return refund


@pytest.fixture
def failed_refund(db, captured_order):
    """Create a failed refund."""
    refund = RefundFactory(payment_order=captured_order)
    refund.process()
    refund.save()
    refund.fail(reason="Refund limit exceeded")
    refund.save()
    return refund


# =============================================================================
# FundHold Fixtures
# =============================================================================


@pytest.fixture
def active_fund_hold(db, held_order):
    """Create an active (unreleased) fund hold."""
    return FundHoldFactory(
        payment_order=held_order,
        amount_cents=held_order.amount_cents,
        expires_at=timezone.now() + timezone.timedelta(days=7),
        released=False,
    )


@pytest.fixture
def released_fund_hold(db, released_order, paid_payout):
    """Create a released fund hold linked to a payout."""
    return FundHoldFactory(
        payment_order=released_order,
        amount_cents=released_order.amount_cents,
        expires_at=timezone.now() + timezone.timedelta(days=7),
        released=True,
        released_at=timezone.now(),
        released_to_payout=paid_payout,
    )


@pytest.fixture
def expired_fund_hold(db, held_order):
    """Create an expired (but unreleased) fund hold."""
    return FundHoldFactory(
        payment_order=held_order,
        amount_cents=held_order.amount_cents,
        expires_at=timezone.now() - timezone.timedelta(days=1),
        released=False,
    )


# =============================================================================
# WebhookEvent Fixtures
# =============================================================================


@pytest.fixture
def pending_webhook(db):
    """Create a pending webhook event."""
    return WebhookEventFactory()


@pytest.fixture
def processed_webhook(db):
    """Create a processed webhook event."""
    webhook = WebhookEventFactory()
    webhook.mark_processing()
    webhook.mark_processed()
    webhook.save()
    return webhook


@pytest.fixture
def failed_webhook(db):
    """Create a failed webhook event."""
    webhook = WebhookEventFactory()
    webhook.mark_processing()
    webhook.mark_failed("Processing error: test failure")
    webhook.save()
    return webhook


# =============================================================================
# Mock Redis Fixture (for lock tests)
# =============================================================================


@pytest.fixture
def mock_redis(mocker):
    """
    Mock Redis client for distributed lock tests.

    Returns a MagicMock configured for basic lock operations.
    """
    mock_client = mocker.MagicMock()
    mock_client.set.return_value = True
    mock_client.get.return_value = None
    mock_client.delete.return_value = 1
    mock_client.register_script.return_value = mocker.MagicMock(return_value=1)

    mocker.patch(
        "django.core.cache.cache",
        new_callable=lambda: mock_client,
    )

    return mock_client
