"""
Pytest fixtures for webhook tests.

Provides fixtures for testing webhook views, handlers, and tasks including
mock webhook payloads, WebhookEvent objects, and payment orders.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from authentication.models import Profile, User
from payments.models import ConnectedAccount, PaymentOrder, Payout, WebhookEvent
from payments.state_machines import (
    OnboardingStatus,
    PaymentStrategyType,
    WebhookEventStatus,
)


# =============================================================================
# User Fixtures
# =============================================================================


@pytest.fixture
def test_user(db):
    """Create a test user for payments."""
    return User.objects.create_user(
        email="webhook_test@example.com",
        password="testpass123",
    )


# =============================================================================
# Payment Order Fixtures
# =============================================================================


@pytest.fixture
def pending_payment_order(db, test_user):
    """Create a payment order in PENDING state."""
    order = PaymentOrder.objects.create(
        payer=test_user,
        amount_cents=10000,
        currency="usd",
        strategy_type=PaymentStrategyType.DIRECT,
        stripe_payment_intent_id="pi_test_webhook_123",
    )
    order.submit()
    order.save()
    return order


@pytest.fixture
def processing_payment_order(db, test_user):
    """Create a payment order in PROCESSING state."""
    order = PaymentOrder.objects.create(
        payer=test_user,
        amount_cents=10000,
        currency="usd",
        strategy_type=PaymentStrategyType.DIRECT,
        stripe_payment_intent_id="pi_test_processing_webhook_123",
    )
    order.submit()
    order.save()
    order.process()
    order.save()
    return order


@pytest.fixture
def draft_payment_order(db, test_user):
    """Create a payment order in DRAFT state."""
    return PaymentOrder.objects.create(
        payer=test_user,
        amount_cents=10000,
        currency="usd",
        strategy_type=PaymentStrategyType.DIRECT,
        stripe_payment_intent_id="pi_test_draft_123",
    )


# =============================================================================
# Webhook Event Fixtures
# =============================================================================


@pytest.fixture
def pending_webhook_event(db):
    """Create a WebhookEvent in PENDING status."""
    return WebhookEvent.objects.create(
        stripe_event_id="evt_test_pending_123",
        event_type="payment_intent.succeeded",
        payload={
            "id": "evt_test_pending_123",
            "type": "payment_intent.succeeded",
            "data": {
                "object": {
                    "id": "pi_test_webhook_123",
                    "object": "payment_intent",
                    "amount": 10000,
                    "currency": "usd",
                    "status": "succeeded",
                }
            },
        },
        status=WebhookEventStatus.PENDING,
    )


@pytest.fixture
def processing_webhook_event(db):
    """Create a WebhookEvent in PROCESSING status."""
    event = WebhookEvent.objects.create(
        stripe_event_id="evt_test_processing_456",
        event_type="payment_intent.succeeded",
        payload={
            "id": "evt_test_processing_456",
            "type": "payment_intent.succeeded",
            "data": {
                "object": {
                    "id": "pi_test_processing_webhook_123",
                    "object": "payment_intent",
                    "amount": 10000,
                    "currency": "usd",
                    "status": "succeeded",
                }
            },
        },
        status=WebhookEventStatus.PROCESSING,
    )
    return event


@pytest.fixture
def processed_webhook_event(db):
    """Create a WebhookEvent in PROCESSED status."""
    from django.utils import timezone

    event = WebhookEvent.objects.create(
        stripe_event_id="evt_test_processed_789",
        event_type="payment_intent.succeeded",
        payload={
            "id": "evt_test_processed_789",
            "type": "payment_intent.succeeded",
            "data": {
                "object": {
                    "id": "pi_test_processed_123",
                    "object": "payment_intent",
                    "amount": 10000,
                    "currency": "usd",
                    "status": "succeeded",
                }
            },
        },
        status=WebhookEventStatus.PROCESSED,
        processed_at=timezone.now(),
    )
    return event


@pytest.fixture
def failed_webhook_event(db):
    """Create a WebhookEvent in FAILED status."""
    return WebhookEvent.objects.create(
        stripe_event_id="evt_test_failed_101",
        event_type="payment_intent.succeeded",
        payload={
            "id": "evt_test_failed_101",
            "type": "payment_intent.succeeded",
            "data": {
                "object": {
                    "id": "pi_test_failed_123",
                    "object": "payment_intent",
                    "amount": 10000,
                    "currency": "usd",
                    "status": "succeeded",
                }
            },
        },
        status=WebhookEventStatus.FAILED,
        error_message="Previous processing failed",
        retry_count=1,
    )


# =============================================================================
# Webhook Payload Fixtures
# =============================================================================


@pytest.fixture
def payment_intent_succeeded_payload():
    """Stripe payment_intent.succeeded webhook payload."""
    return {
        "id": "evt_test_new_123",
        "type": "payment_intent.succeeded",
        "data": {
            "object": {
                "id": "pi_test_webhook_123",
                "object": "payment_intent",
                "amount": 10000,
                "amount_received": 10000,
                "currency": "usd",
                "status": "succeeded",
                "metadata": {},
            }
        },
    }


@pytest.fixture
def payment_intent_failed_payload():
    """Stripe payment_intent.payment_failed webhook payload."""
    return {
        "id": "evt_test_failed_new_123",
        "type": "payment_intent.payment_failed",
        "data": {
            "object": {
                "id": "pi_test_webhook_123",
                "object": "payment_intent",
                "amount": 10000,
                "currency": "usd",
                "status": "requires_payment_method",
                "last_payment_error": {
                    "code": "card_declined",
                    "decline_code": "generic_decline",
                    "message": "Your card was declined.",
                },
                "metadata": {},
            }
        },
    }


@pytest.fixture
def payment_intent_canceled_payload():
    """Stripe payment_intent.canceled webhook payload."""
    return {
        "id": "evt_test_canceled_123",
        "type": "payment_intent.canceled",
        "data": {
            "object": {
                "id": "pi_test_draft_123",
                "object": "payment_intent",
                "amount": 10000,
                "currency": "usd",
                "status": "canceled",
                "cancellation_reason": "requested_by_customer",
                "metadata": {},
            }
        },
    }


@pytest.fixture
def unknown_event_payload():
    """Stripe webhook payload for an unhandled event type."""
    return {
        "id": "evt_test_unknown_123",
        "type": "customer.subscription.created",
        "data": {
            "object": {
                "id": "sub_test_123",
                "object": "subscription",
            }
        },
    }


# =============================================================================
# Request Factory Fixtures
# =============================================================================


@pytest.fixture
def stripe_webhook_request_factory(client):
    """Factory for creating mock Stripe webhook requests."""

    def _create_request(payload: dict, signature: str = "test_signature"):
        """
        Create a mock webhook request.

        Args:
            payload: The webhook payload dict
            signature: The Stripe-Signature header value

        Returns:
            Mock request object
        """
        request = MagicMock()
        request.body = json.dumps(payload).encode()
        request.headers = {"Stripe-Signature": signature}
        return request

    return _create_request


# =============================================================================
# Mock Adapters
# =============================================================================


@pytest.fixture
def mock_stripe_verify_signature():
    """Mock the Stripe signature verification."""
    with patch(
        "payments.webhooks.views.StripeAdapter.verify_webhook_signature"
    ) as mock:
        yield mock


@pytest.fixture
def mock_celery_task():
    """Mock the Celery task to prevent actual task execution."""
    with patch("payments.webhooks.views.process_webhook_event.delay") as mock:
        yield mock


@pytest.fixture
def mock_ledger_service():
    """Mock the ledger entry recording to prevent actual ledger entries."""
    with patch(
        "payments.strategies.direct.DirectPaymentStrategy._record_payment_ledger_entries"
    ) as mock:
        mock.return_value = None  # Method doesn't return anything
        yield mock


# =============================================================================
# Profile and Connected Account Fixtures
# =============================================================================


@pytest.fixture
def test_profile(db, test_user):
    """Create a profile for test user."""
    profile, _ = Profile.objects.get_or_create(user=test_user)
    return profile


@pytest.fixture
def connected_account_fixture(db, test_profile):
    """Create a connected account for testing."""
    return ConnectedAccount.objects.create(
        profile=test_profile,
        stripe_account_id="acct_test_webhook_123",
        onboarding_status=OnboardingStatus.COMPLETE,
        payouts_enabled=True,
        charges_enabled=True,
    )


# =============================================================================
# Settled Payment Order Fixture
# =============================================================================


@pytest.fixture
def settled_payment_order(db, test_user, mock_ledger_service):
    """Create a settled payment order for refund tests."""
    order = PaymentOrder.objects.create(
        payer=test_user,
        amount_cents=10000,
        currency="usd",
        strategy_type=PaymentStrategyType.DIRECT,
        stripe_payment_intent_id="pi_test_settled_for_refund_123",
    )
    order.submit()
    order.save()
    order.process()
    order.save()
    order.capture()
    order.save()
    order.settle_from_captured()
    order.save()
    return order


# =============================================================================
# Payout Fixtures for Transfer Handler Tests
# =============================================================================


@pytest.fixture
def processing_payout_with_transfer_id(
    db, pending_payment_order, connected_account_fixture
):
    """Create a processing payout with stripe_transfer_id set."""
    payout = Payout.objects.create(
        payment_order=pending_payment_order,
        connected_account=connected_account_fixture,
        amount_cents=9000,
        currency="usd",
        stripe_transfer_id="tr_test_processing_123",
    )
    payout.process()
    payout.save()
    return payout


@pytest.fixture
def scheduled_payout_with_transfer_id(
    db, pending_payment_order, connected_account_fixture
):
    """Create a scheduled payout with stripe_transfer_id set."""
    payout = Payout.objects.create(
        payment_order=pending_payment_order,
        connected_account=connected_account_fixture,
        amount_cents=9000,
        currency="usd",
        stripe_transfer_id="tr_test_scheduled_123",
    )
    payout.process()
    payout.save()
    payout.mark_scheduled()
    payout.save()
    return payout


@pytest.fixture
def paid_payout_with_transfer_id(db, pending_payment_order, connected_account_fixture):
    """Create a paid payout with stripe_transfer_id set."""
    payout = Payout.objects.create(
        payment_order=pending_payment_order,
        connected_account=connected_account_fixture,
        amount_cents=9000,
        currency="usd",
        stripe_transfer_id="tr_test_paid_123",
    )
    payout.process()
    payout.save()
    payout.complete()
    payout.save()
    return payout


@pytest.fixture
def failed_payout_with_transfer_id(
    db, pending_payment_order, connected_account_fixture
):
    """Create a failed payout with stripe_transfer_id set."""
    payout = Payout.objects.create(
        payment_order=pending_payment_order,
        connected_account=connected_account_fixture,
        amount_cents=9000,
        currency="usd",
        stripe_transfer_id="tr_test_failed_123",
    )
    payout.process()
    payout.save()
    payout.fail(reason="Initial failure for test")
    payout.save()
    return payout
