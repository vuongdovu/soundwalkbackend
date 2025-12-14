"""
Pytest fixtures for Stripe adapter tests.

This module provides fixtures for testing the Stripe adapter, including
mock Stripe API responses, error conditions, and test data.

Sections:
    - Mock Stripe Client Fixtures
    - Test Data Fixtures
    - Error Response Fixtures
"""

import uuid
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

import pytest
import stripe


# =============================================================================
# Test Data Fixtures
# =============================================================================


@pytest.fixture
def payment_order_id():
    """Generate a random UUID for testing."""
    return uuid.uuid4()


@pytest.fixture
def trace_id():
    """Generate a trace ID for testing."""
    return f"trace-{uuid.uuid4().hex[:16]}"


@pytest.fixture
def idempotency_key():
    """Generate an idempotency key for testing."""
    return f"test-{uuid.uuid4()}"


# =============================================================================
# Mock Stripe Response Fixtures
# =============================================================================


@dataclass
class MockStripeObject:
    """Mock Stripe API object with to_dict support."""

    data: dict[str, Any]

    def __getattr__(self, name: str) -> Any:
        if name == "data":
            return self.__dict__["data"]
        return self.data.get(name)

    def to_dict(self) -> dict[str, Any]:
        return self.data


@pytest.fixture
def mock_payment_intent():
    """Create a mock PaymentIntent response."""

    def _create(
        id: str = "pi_test123456",
        status: str = "requires_payment_method",
        amount: int = 5000,
        currency: str = "usd",
        client_secret: str = "pi_test123456_secret_abc123",
        amount_received: int = 0,
        metadata: dict | None = None,
    ) -> MockStripeObject:
        return MockStripeObject(
            {
                "id": id,
                "object": "payment_intent",
                "status": status,
                "amount": amount,
                "currency": currency,
                "client_secret": client_secret,
                "amount_received": amount_received,
                "metadata": metadata or {},
            }
        )

    return _create


@pytest.fixture
def mock_transfer():
    """Create a mock Transfer response."""

    def _create(
        id: str = "tr_test123456",
        amount: int = 4500,
        currency: str = "usd",
        destination: str = "acct_dest123",
        metadata: dict | None = None,
    ) -> MockStripeObject:
        return MockStripeObject(
            {
                "id": id,
                "object": "transfer",
                "amount": amount,
                "currency": currency,
                "destination": destination,
                "metadata": metadata or {},
            }
        )

    return _create


@pytest.fixture
def mock_refund():
    """Create a mock Refund response."""

    def _create(
        id: str = "re_test123456",
        amount: int = 5000,
        currency: str = "usd",
        status: str = "succeeded",
        payment_intent: str = "pi_test123456",
        metadata: dict | None = None,
    ) -> MockStripeObject:
        return MockStripeObject(
            {
                "id": id,
                "object": "refund",
                "amount": amount,
                "currency": currency,
                "status": status,
                "payment_intent": payment_intent,
                "metadata": metadata or {},
            }
        )

    return _create


@dataclass
class MockStripeList:
    """Mock Stripe list response with data attribute."""

    items: list[MockStripeObject]
    has_more: bool = False

    @property
    def data(self) -> list[MockStripeObject]:
        return self.items


@pytest.fixture
def mock_payment_intent_list(mock_payment_intent):
    """Create a mock PaymentIntent list response."""

    def _create(count: int = 3) -> MockStripeList:
        intents = [
            mock_payment_intent(
                id=f"pi_test{i}",
                amount=1000 * (i + 1),
            )
            for i in range(count)
        ]
        return MockStripeList(items=intents, has_more=False)

    return _create


# =============================================================================
# Mock Stripe Error Fixtures
# =============================================================================


@pytest.fixture
def card_error():
    """Create a Stripe CardError."""

    def _create(
        message: str = "Your card was declined.",
        code: str = "card_declined",
        decline_code: str | None = "generic_decline",
    ) -> stripe.error.CardError:
        # CardError user_message is a property that defaults to message
        error = stripe.error.CardError(
            message=message,
            param=None,
            code=code,
        )
        # decline_code is also set via constructor in newer versions
        # but we can set it directly on the error object
        object.__setattr__(error, "_decline_code", decline_code)
        error.decline_code = decline_code
        return error

    return _create


@pytest.fixture
def insufficient_funds_error():
    """Create a Stripe CardError for insufficient funds."""
    error = stripe.error.CardError(
        message="Your card has insufficient funds.",
        param=None,
        code="card_declined",
    )
    error.decline_code = "insufficient_funds"
    return error


@pytest.fixture
def invalid_request_error():
    """Create a Stripe InvalidRequestError."""

    def _create(
        message: str = "Invalid payment intent ID",
        param: str | None = "payment_intent",
        code: str = "resource_missing",
    ) -> stripe.error.InvalidRequestError:
        return stripe.error.InvalidRequestError(
            message=message,
            param=param,
            code=code,
        )

    return _create


@pytest.fixture
def rate_limit_error():
    """Create a Stripe RateLimitError."""
    return stripe.error.RateLimitError(
        message="Too many requests hit the API too quickly.",
    )


@pytest.fixture
def api_connection_error():
    """Create a Stripe APIConnectionError."""
    return stripe.error.APIConnectionError(
        message="Could not connect to Stripe.",
    )


@pytest.fixture
def api_error():
    """Create a Stripe APIError."""
    return stripe.error.APIError(
        message="Something went wrong on Stripe's end.",
    )


@pytest.fixture
def authentication_error():
    """Create a Stripe AuthenticationError."""
    return stripe.error.AuthenticationError(
        message="Invalid API Key provided.",
    )


@pytest.fixture
def signature_verification_error():
    """Create a Stripe SignatureVerificationError."""
    return stripe.error.SignatureVerificationError(
        message="Unable to verify webhook signature.",
        sig_header="bad_signature",
    )


# =============================================================================
# Mock Stripe Client Fixtures
# =============================================================================


@pytest.fixture
def mock_stripe_payment_intent(mock_payment_intent):
    """Mock stripe.PaymentIntent API."""
    with patch("stripe.PaymentIntent") as mock:
        # Default: successful create
        mock.create.return_value = mock_payment_intent()
        mock.capture.return_value = mock_payment_intent(
            status="succeeded", amount_received=5000
        )
        mock.retrieve.return_value = mock_payment_intent()
        mock.list.return_value = MockStripeObject({"data": [], "has_more": False})
        yield mock


@pytest.fixture
def mock_stripe_transfer(mock_transfer):
    """Mock stripe.Transfer API."""
    with patch("stripe.Transfer") as mock:
        mock.create.return_value = mock_transfer()
        yield mock


@pytest.fixture
def mock_stripe_refund(mock_refund):
    """Mock stripe.Refund API."""
    with patch("stripe.Refund") as mock:
        mock.create.return_value = mock_refund()
        yield mock


@pytest.fixture
def mock_stripe_webhook():
    """Mock stripe.Webhook API."""
    with patch("stripe.Webhook") as mock:
        mock.construct_event.return_value = MockStripeObject(
            {
                "id": "evt_test123",
                "type": "payment_intent.succeeded",
                "data": {
                    "object": {
                        "id": "pi_test123",
                        "object": "payment_intent",
                    }
                },
            }
        )
        yield mock


@pytest.fixture
def mock_stripe_http_client():
    """Mock stripe.http_client.RequestsClient."""
    with patch("stripe.http_client.RequestsClient") as mock:
        yield mock
