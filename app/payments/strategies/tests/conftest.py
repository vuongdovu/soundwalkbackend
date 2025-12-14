"""
Pytest fixtures for payment strategy tests.

This module provides fixtures for testing payment strategies, including
mock Stripe adapters, test users, and payment orders in various states.
"""

import uuid
from dataclasses import dataclass
from typing import Any

import pytest

from authentication.models import User
from payments.adapters import PaymentIntentResult
from payments.models import PaymentOrder
from payments.state_machines import PaymentStrategyType


# =============================================================================
# User Fixtures
# =============================================================================


@pytest.fixture
def test_user(db):
    """Create a test user for payments."""
    return User.objects.create_user(
        email="testpayer@example.com",
        password="testpass123",
    )


# =============================================================================
# Mock Stripe Adapter
# =============================================================================


@dataclass
class MockPaymentIntentResult:
    """Mock PaymentIntent result for testing."""

    id: str = "pi_test_123456"
    status: str = "requires_payment_method"
    amount_cents: int = 5000
    currency: str = "usd"
    client_secret: str = "pi_test_123456_secret_abc123"
    captured: bool = False
    metadata: dict = None
    raw_response: dict = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}
        if self.raw_response is None:
            self.raw_response = {}


class MockStripeAdapter:
    """
    Mock Stripe adapter for testing.

    Provides configurable responses for all Stripe operations.
    Use the class attributes to customize behavior per test.
    """

    # Default responses (can be overridden in tests)
    create_payment_intent_response: PaymentIntentResult = None
    create_payment_intent_side_effect: Exception = None

    # Track calls for assertions
    calls: dict[str, list[Any]] = {}

    @classmethod
    def reset(cls):
        """Reset all mock state."""
        cls.create_payment_intent_response = None
        cls.create_payment_intent_side_effect = None
        cls.calls = {}

    @classmethod
    def create_payment_intent(cls, params, trace_id=None):
        """Mock create_payment_intent."""
        # Track the call
        if "create_payment_intent" not in cls.calls:
            cls.calls["create_payment_intent"] = []
        cls.calls["create_payment_intent"].append(
            {"params": params, "trace_id": trace_id}
        )

        # Raise exception if configured
        if cls.create_payment_intent_side_effect:
            raise cls.create_payment_intent_side_effect

        # Return configured response or default
        if cls.create_payment_intent_response:
            return cls.create_payment_intent_response

        return PaymentIntentResult(
            id=f"pi_test_{uuid.uuid4().hex[:8]}",
            status="requires_payment_method",
            amount_cents=params.amount_cents,
            currency=params.currency,
            client_secret=f"pi_test_secret_{uuid.uuid4().hex[:8]}",
            captured=False,
            metadata=params.metadata,
            raw_response={},
        )


@pytest.fixture
def mock_stripe_adapter():
    """Provide a clean MockStripeAdapter for each test."""
    MockStripeAdapter.reset()
    return MockStripeAdapter


# =============================================================================
# Payment Order Fixtures
# =============================================================================


@pytest.fixture
def draft_payment_order(db, test_user):
    """Create a payment order in DRAFT state."""
    return PaymentOrder.objects.create(
        payer=test_user,
        amount_cents=10000,
        currency="usd",
        strategy_type=PaymentStrategyType.DIRECT,
    )


@pytest.fixture
def pending_payment_order(db, test_user):
    """Create a payment order in PENDING state."""
    order = PaymentOrder.objects.create(
        payer=test_user,
        amount_cents=10000,
        currency="usd",
        strategy_type=PaymentStrategyType.DIRECT,
        stripe_payment_intent_id="pi_test_pending_123",
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
        stripe_payment_intent_id="pi_test_processing_123",
    )
    order.submit()
    order.save()
    order.process()
    order.save()
    return order


@pytest.fixture
def captured_payment_order(db, test_user):
    """Create a payment order in CAPTURED state."""
    order = PaymentOrder.objects.create(
        payer=test_user,
        amount_cents=10000,
        currency="usd",
        strategy_type=PaymentStrategyType.DIRECT,
        stripe_payment_intent_id="pi_test_captured_123",
    )
    order.submit()
    order.save()
    order.process()
    order.save()
    order.capture()
    order.save()
    return order


@pytest.fixture
def settled_payment_order(db, test_user):
    """Create a payment order in SETTLED state."""
    order = PaymentOrder.objects.create(
        payer=test_user,
        amount_cents=10000,
        currency="usd",
        strategy_type=PaymentStrategyType.DIRECT,
        stripe_payment_intent_id="pi_test_settled_123",
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


@pytest.fixture
def failed_payment_order(db, test_user):
    """Create a payment order in FAILED state."""
    order = PaymentOrder.objects.create(
        payer=test_user,
        amount_cents=10000,
        currency="usd",
        strategy_type=PaymentStrategyType.DIRECT,
        stripe_payment_intent_id="pi_test_failed_123",
    )
    order.submit()
    order.save()
    order.process()
    order.save()
    order.fail(reason="Test failure")
    order.save()
    return order


# =============================================================================
# Webhook Event Data Fixtures
# =============================================================================


@pytest.fixture
def payment_succeeded_event_data():
    """Mock Stripe payment_intent.succeeded webhook event data."""
    return {
        "id": "evt_test_succeeded_123",
        "type": "payment_intent.succeeded",
        "data": {
            "object": {
                "id": "pi_test_123",
                "object": "payment_intent",
                "amount": 10000,
                "amount_received": 10000,
                "currency": "usd",
                "status": "succeeded",
                "metadata": {
                    "payment_order_id": "test-uuid-here",
                },
            }
        },
    }


@pytest.fixture
def payment_failed_event_data():
    """Mock Stripe payment_intent.payment_failed webhook event data."""
    return {
        "id": "evt_test_failed_123",
        "type": "payment_intent.payment_failed",
        "data": {
            "object": {
                "id": "pi_test_123",
                "object": "payment_intent",
                "amount": 10000,
                "currency": "usd",
                "status": "requires_payment_method",
                "last_payment_error": {
                    "code": "card_declined",
                    "decline_code": "generic_decline",
                    "message": "Your card was declined.",
                },
                "metadata": {
                    "payment_order_id": "test-uuid-here",
                },
            }
        },
    }
