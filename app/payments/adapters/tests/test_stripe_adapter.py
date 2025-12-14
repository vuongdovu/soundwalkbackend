"""
Tests for Stripe adapter.

Tests cover:
- Idempotency key generation
- Error translation for each exception type
- Successful API operations
- Timeout handling
- Helper functions (is_retryable, backoff_delay)
"""

import uuid
from datetime import datetime, timezone

import pytest
import stripe
from django.test import override_settings

from payments.adapters import (
    CreatePaymentIntentParams,
    IdempotencyKeyGenerator,
    PaymentIntentResult,
    RefundResult,
    StripeAdapter,
    TransferResult,
    backoff_delay,
    is_retryable_stripe_error,
)
from payments.exceptions import (
    StripeAPIUnavailableError,
    StripeCardDeclinedError,
    StripeInsufficientFundsError,
    StripeInvalidAccountError,
    StripeInvalidRequestError,
    StripeRateLimitError,
    StripeTimeoutError,
)


# =============================================================================
# CreatePaymentIntentParams Tests
# =============================================================================


class TestCreatePaymentIntentParams:
    """Tests for CreatePaymentIntentParams dataclass validation."""

    def test_valid_params(self):
        """Should create params with valid values."""
        params = CreatePaymentIntentParams(
            amount_cents=5000,
            currency="usd",
            idempotency_key="test-key-123",
        )

        assert params.amount_cents == 5000
        assert params.currency == "usd"
        assert params.idempotency_key == "test-key-123"
        assert params.capture_method == "automatic"
        assert params.payment_method_types == ["card"]

    def test_amount_must_be_positive(self):
        """Should raise ValueError for zero or negative amount."""
        with pytest.raises(ValueError, match="amount_cents must be positive"):
            CreatePaymentIntentParams(
                amount_cents=0,
                currency="usd",
                idempotency_key="test-key",
            )

        with pytest.raises(ValueError, match="amount_cents must be positive"):
            CreatePaymentIntentParams(
                amount_cents=-100,
                currency="usd",
                idempotency_key="test-key",
            )

    def test_idempotency_key_required(self):
        """Should raise ValueError for empty idempotency key."""
        with pytest.raises(ValueError, match="idempotency_key is required"):
            CreatePaymentIntentParams(
                amount_cents=5000,
                currency="usd",
                idempotency_key="",
            )

    def test_currency_required(self):
        """Should raise ValueError for empty currency."""
        with pytest.raises(ValueError, match="currency is required"):
            CreatePaymentIntentParams(
                amount_cents=5000,
                currency="",
                idempotency_key="test-key",
            )

    def test_optional_fields(self):
        """Should allow optional fields."""
        params = CreatePaymentIntentParams(
            amount_cents=5000,
            currency="usd",
            idempotency_key="test-key",
            customer_id="cus_test123",
            metadata={"order_id": "123"},
            capture_method="manual",
            payment_method_types=["card", "us_bank_account"],
            transfer_data={"destination": "acct_test"},
        )

        assert params.customer_id == "cus_test123"
        assert params.metadata == {"order_id": "123"}
        assert params.capture_method == "manual"
        assert params.payment_method_types == ["card", "us_bank_account"]
        assert params.transfer_data == {"destination": "acct_test"}


# =============================================================================
# IdempotencyKeyGenerator Tests
# =============================================================================


class TestIdempotencyKeyGenerator:
    """Tests for IdempotencyKeyGenerator."""

    def test_generate_key_format(self):
        """Should generate key in correct format."""
        entity_id = uuid.uuid4()
        key = IdempotencyKeyGenerator.generate(
            operation="create_intent",
            entity_id=entity_id,
            attempt=1,
        )

        parts = key.split(":")
        assert len(parts) == 4
        assert parts[0] == "create_intent"
        assert parts[1] == str(entity_id)
        assert parts[2] == "1"
        assert len(parts[3]) == 8  # 8 character hash

    def test_generate_key_with_string_entity_id(self):
        """Should accept string entity ID."""
        key = IdempotencyKeyGenerator.generate(
            operation="capture",
            entity_id="order_123",
            attempt=2,
        )

        parts = key.split(":")
        assert parts[0] == "capture"
        assert parts[1] == "order_123"
        assert parts[2] == "2"

    def test_same_inputs_produce_same_key(self):
        """Same inputs should produce same key (deterministic)."""
        entity_id = uuid.uuid4()
        key1 = IdempotencyKeyGenerator.generate(
            operation="refund",
            entity_id=entity_id,
            attempt=1,
        )
        key2 = IdempotencyKeyGenerator.generate(
            operation="refund",
            entity_id=entity_id,
            attempt=1,
        )

        assert key1 == key2

    def test_different_attempts_produce_different_keys(self):
        """Different attempt numbers should produce different keys."""
        entity_id = uuid.uuid4()
        key1 = IdempotencyKeyGenerator.generate(
            operation="transfer",
            entity_id=entity_id,
            attempt=1,
        )
        key2 = IdempotencyKeyGenerator.generate(
            operation="transfer",
            entity_id=entity_id,
            attempt=2,
        )

        assert key1 != key2

    def test_different_operations_produce_different_keys(self):
        """Different operations should produce different keys."""
        entity_id = uuid.uuid4()
        key1 = IdempotencyKeyGenerator.generate(
            operation="create",
            entity_id=entity_id,
        )
        key2 = IdempotencyKeyGenerator.generate(
            operation="capture",
            entity_id=entity_id,
        )

        assert key1 != key2


# =============================================================================
# Helper Function Tests
# =============================================================================


class TestIsRetryableStripeError:
    """Tests for is_retryable_stripe_error helper."""

    def test_retryable_errors(self):
        """Should return True for retryable Stripe errors."""
        assert is_retryable_stripe_error(StripeRateLimitError("Rate limited")) is True
        assert (
            is_retryable_stripe_error(StripeAPIUnavailableError("Unavailable")) is True
        )
        assert is_retryable_stripe_error(StripeTimeoutError("Timeout")) is True

    def test_non_retryable_errors(self):
        """Should return False for non-retryable Stripe errors."""
        assert is_retryable_stripe_error(StripeCardDeclinedError("Declined")) is False
        assert (
            is_retryable_stripe_error(StripeInsufficientFundsError("No funds")) is False
        )
        assert (
            is_retryable_stripe_error(StripeInvalidAccountError("Bad account")) is False
        )
        assert (
            is_retryable_stripe_error(StripeInvalidRequestError("Bad request")) is False
        )

    def test_non_stripe_errors(self):
        """Should return False for non-Stripe errors."""
        assert is_retryable_stripe_error(ValueError("test")) is False
        assert is_retryable_stripe_error(RuntimeError("test")) is False
        assert is_retryable_stripe_error(Exception("test")) is False


class TestBackoffDelay:
    """Tests for backoff_delay helper."""

    def test_exponential_growth(self):
        """Should grow exponentially with attempt number."""
        # Without jitter, delays should be 1, 2, 4, 8, etc
        # With jitter (0-25%), we test the base value is correct
        delay0 = backoff_delay(0, base=1.0, max_delay=60.0)
        delay1 = backoff_delay(1, base=1.0, max_delay=60.0)
        delay2 = backoff_delay(2, base=1.0, max_delay=60.0)

        # Should be approximately 1, 2, 4 (with up to 25% jitter)
        assert 1.0 <= delay0 <= 1.25
        assert 2.0 <= delay1 <= 2.5
        assert 4.0 <= delay2 <= 5.0

    def test_respects_max_delay(self):
        """Should cap at max_delay."""
        delay = backoff_delay(10, base=1.0, max_delay=60.0)

        # 2^10 = 1024, should cap at 60 + jitter
        assert delay <= 75.0  # 60 + 25% jitter

    def test_custom_base(self):
        """Should use custom base value."""
        delay = backoff_delay(0, base=2.0, max_delay=60.0)

        assert 2.0 <= delay <= 2.5

    def test_jitter_added(self):
        """Should add jitter (0-25%) to delay."""
        # Run multiple times to verify jitter varies
        delays = [backoff_delay(1, base=1.0, max_delay=60.0) for _ in range(10)]

        # At attempt 1, base delay is 2
        # All should be >= 2.0 and <= 2.5
        for d in delays:
            assert 2.0 <= d <= 2.5


# =============================================================================
# StripeAdapter Error Translation Tests
# =============================================================================


class TestStripeAdapterErrorTranslation:
    """Tests for Stripe error translation to domain exceptions."""

    @pytest.fixture(autouse=True)
    def setup_mocks(self, mock_stripe_http_client):
        """Set up common mocks for all tests."""
        pass

    def test_card_declined_error(self, mock_stripe_payment_intent, card_error):
        """Should translate CardError to StripeCardDeclinedError."""
        mock_stripe_payment_intent.create.side_effect = card_error()

        params = CreatePaymentIntentParams(
            amount_cents=5000,
            currency="usd",
            idempotency_key="test-key",
        )

        with pytest.raises(StripeCardDeclinedError) as exc_info:
            StripeAdapter.create_payment_intent(params)

        assert exc_info.value.decline_code == "generic_decline"
        assert exc_info.value.is_retryable is False

    def test_insufficient_funds_error(
        self, mock_stripe_payment_intent, insufficient_funds_error
    ):
        """Should translate insufficient funds to StripeInsufficientFundsError."""
        mock_stripe_payment_intent.create.side_effect = insufficient_funds_error

        params = CreatePaymentIntentParams(
            amount_cents=5000,
            currency="usd",
            idempotency_key="test-key",
        )

        with pytest.raises(StripeInsufficientFundsError) as exc_info:
            StripeAdapter.create_payment_intent(params)

        assert exc_info.value.decline_code == "insufficient_funds"
        assert exc_info.value.is_retryable is False

    def test_invalid_request_error(
        self, mock_stripe_payment_intent, invalid_request_error
    ):
        """Should translate InvalidRequestError to StripeInvalidRequestError."""
        mock_stripe_payment_intent.create.side_effect = invalid_request_error()

        params = CreatePaymentIntentParams(
            amount_cents=5000,
            currency="usd",
            idempotency_key="test-key",
        )

        with pytest.raises(StripeInvalidRequestError) as exc_info:
            StripeAdapter.create_payment_intent(params)

        assert exc_info.value.is_retryable is False

    def test_invalid_account_error(self, mock_stripe_transfer, invalid_request_error):
        """Should translate account-related InvalidRequestError to StripeInvalidAccountError."""
        error = invalid_request_error(
            message="No such account: acct_invalid",
            code="account_invalid",
        )
        mock_stripe_transfer.create.side_effect = error

        with pytest.raises(StripeInvalidAccountError):
            StripeAdapter.create_transfer(
                amount_cents=5000,
                destination_account="acct_invalid",
                idempotency_key="test-key",
            )

    def test_rate_limit_error(self, mock_stripe_payment_intent, rate_limit_error):
        """Should translate RateLimitError to StripeRateLimitError."""
        mock_stripe_payment_intent.create.side_effect = rate_limit_error

        params = CreatePaymentIntentParams(
            amount_cents=5000,
            currency="usd",
            idempotency_key="test-key",
        )

        with pytest.raises(StripeRateLimitError) as exc_info:
            StripeAdapter.create_payment_intent(params)

        assert exc_info.value.is_retryable is True

    def test_api_connection_error(
        self, mock_stripe_payment_intent, api_connection_error
    ):
        """Should translate APIConnectionError to StripeAPIUnavailableError."""
        mock_stripe_payment_intent.create.side_effect = api_connection_error

        params = CreatePaymentIntentParams(
            amount_cents=5000,
            currency="usd",
            idempotency_key="test-key",
        )

        with pytest.raises(StripeAPIUnavailableError) as exc_info:
            StripeAdapter.create_payment_intent(params)

        assert exc_info.value.is_retryable is True

    def test_api_error(self, mock_stripe_payment_intent, api_error):
        """Should translate APIError to StripeAPIUnavailableError."""
        mock_stripe_payment_intent.create.side_effect = api_error

        params = CreatePaymentIntentParams(
            amount_cents=5000,
            currency="usd",
            idempotency_key="test-key",
        )

        with pytest.raises(StripeAPIUnavailableError) as exc_info:
            StripeAdapter.create_payment_intent(params)

        assert exc_info.value.is_retryable is True

    def test_authentication_error(
        self, mock_stripe_payment_intent, authentication_error
    ):
        """Should translate AuthenticationError to StripeInvalidRequestError."""
        mock_stripe_payment_intent.create.side_effect = authentication_error

        params = CreatePaymentIntentParams(
            amount_cents=5000,
            currency="usd",
            idempotency_key="test-key",
        )

        with pytest.raises(StripeInvalidRequestError) as exc_info:
            StripeAdapter.create_payment_intent(params)

        # Auth errors are permanent, not retryable
        assert exc_info.value.is_retryable is False

    def test_unknown_error(self, mock_stripe_payment_intent):
        """Should wrap unknown errors in StripeAPIUnavailableError."""
        mock_stripe_payment_intent.create.side_effect = RuntimeError("Unexpected")

        params = CreatePaymentIntentParams(
            amount_cents=5000,
            currency="usd",
            idempotency_key="test-key",
        )

        with pytest.raises(StripeAPIUnavailableError) as exc_info:
            StripeAdapter.create_payment_intent(params)

        assert "Unexpected" in str(exc_info.value)


# =============================================================================
# StripeAdapter API Operation Tests
# =============================================================================


class TestStripeAdapterCreatePaymentIntent:
    """Tests for StripeAdapter.create_payment_intent."""

    @pytest.fixture(autouse=True)
    def setup_mocks(self, mock_stripe_http_client):
        """Set up common mocks for all tests."""
        pass

    def test_create_payment_intent_success(
        self, mock_stripe_payment_intent, mock_payment_intent
    ):
        """Should create PaymentIntent and return result."""
        expected = mock_payment_intent(
            id="pi_test123",
            status="requires_payment_method",
            amount=5000,
        )
        mock_stripe_payment_intent.create.return_value = expected

        params = CreatePaymentIntentParams(
            amount_cents=5000,
            currency="usd",
            idempotency_key="test-key-123",
            metadata={"order_id": "order_123"},
        )

        result = StripeAdapter.create_payment_intent(params)

        assert isinstance(result, PaymentIntentResult)
        assert result.id == "pi_test123"
        assert result.status == "requires_payment_method"
        assert result.amount_cents == 5000
        assert result.currency == "usd"
        assert result.client_secret is not None
        assert result.captured is False

        # Verify call parameters
        mock_stripe_payment_intent.create.assert_called_once()
        call_kwargs = mock_stripe_payment_intent.create.call_args.kwargs
        assert call_kwargs["amount"] == 5000
        assert call_kwargs["currency"] == "usd"
        assert call_kwargs["idempotency_key"] == "test-key-123"
        assert call_kwargs["metadata"] == {"order_id": "order_123"}

    def test_create_payment_intent_with_all_options(
        self, mock_stripe_payment_intent, mock_payment_intent
    ):
        """Should pass all optional parameters to Stripe."""
        mock_stripe_payment_intent.create.return_value = mock_payment_intent()

        params = CreatePaymentIntentParams(
            amount_cents=10000,
            currency="eur",
            idempotency_key="test-key",
            customer_id="cus_test123",
            payment_method_types=["card", "sepa_debit"],
            capture_method="manual",
            transfer_data={"destination": "acct_dest"},
            metadata={"custom": "value"},
        )

        StripeAdapter.create_payment_intent(params)

        call_kwargs = mock_stripe_payment_intent.create.call_args.kwargs
        assert call_kwargs["customer"] == "cus_test123"
        assert call_kwargs["payment_method_types"] == ["card", "sepa_debit"]
        assert call_kwargs["capture_method"] == "manual"
        assert call_kwargs["transfer_data"] == {"destination": "acct_dest"}

    def test_create_payment_intent_with_trace_id(
        self, mock_stripe_payment_intent, mock_payment_intent, trace_id
    ):
        """Should accept trace_id for logging."""
        mock_stripe_payment_intent.create.return_value = mock_payment_intent()

        params = CreatePaymentIntentParams(
            amount_cents=5000,
            currency="usd",
            idempotency_key="test-key",
        )

        # Should not raise
        result = StripeAdapter.create_payment_intent(params, trace_id=trace_id)
        assert result is not None


class TestStripeAdapterCapturePaymentIntent:
    """Tests for StripeAdapter.capture_payment_intent."""

    @pytest.fixture(autouse=True)
    def setup_mocks(self, mock_stripe_http_client):
        """Set up common mocks for all tests."""
        pass

    def test_capture_payment_intent_success(
        self, mock_stripe_payment_intent, mock_payment_intent
    ):
        """Should capture PaymentIntent and return result."""
        captured = mock_payment_intent(
            id="pi_test123",
            status="succeeded",
            amount=5000,
            amount_received=5000,
        )
        mock_stripe_payment_intent.capture.return_value = captured

        result = StripeAdapter.capture_payment_intent(
            payment_intent_id="pi_test123",
            idempotency_key="capture-key",
        )

        assert isinstance(result, PaymentIntentResult)
        assert result.id == "pi_test123"
        assert result.status == "succeeded"
        assert result.captured is True

        mock_stripe_payment_intent.capture.assert_called_once_with(
            "pi_test123",
            idempotency_key="capture-key",
        )

    def test_capture_partial_amount(
        self, mock_stripe_payment_intent, mock_payment_intent
    ):
        """Should support partial capture."""
        mock_stripe_payment_intent.capture.return_value = mock_payment_intent(
            amount_received=3000,
        )

        StripeAdapter.capture_payment_intent(
            payment_intent_id="pi_test123",
            idempotency_key="capture-key",
            amount_to_capture=3000,
        )

        call_kwargs = mock_stripe_payment_intent.capture.call_args.kwargs
        assert call_kwargs["amount_to_capture"] == 3000


class TestStripeAdapterCreateTransfer:
    """Tests for StripeAdapter.create_transfer."""

    @pytest.fixture(autouse=True)
    def setup_mocks(self, mock_stripe_http_client):
        """Set up common mocks for all tests."""
        pass

    def test_create_transfer_success(self, mock_stripe_transfer, mock_transfer):
        """Should create Transfer and return result."""
        mock_stripe_transfer.create.return_value = mock_transfer(
            id="tr_test123",
            amount=4500,
            destination="acct_dest123",
        )

        result = StripeAdapter.create_transfer(
            amount_cents=4500,
            destination_account="acct_dest123",
            idempotency_key="transfer-key",
        )

        assert isinstance(result, TransferResult)
        assert result.id == "tr_test123"
        assert result.amount_cents == 4500
        assert result.destination_account == "acct_dest123"

    def test_create_transfer_with_source_transaction(
        self, mock_stripe_transfer, mock_transfer
    ):
        """Should support source_transaction parameter."""
        mock_stripe_transfer.create.return_value = mock_transfer()

        StripeAdapter.create_transfer(
            amount_cents=4500,
            destination_account="acct_dest",
            idempotency_key="transfer-key",
            source_transaction="pi_source123",
        )

        call_kwargs = mock_stripe_transfer.create.call_args.kwargs
        assert call_kwargs["source_transaction"] == "pi_source123"


class TestStripeAdapterCreateRefund:
    """Tests for StripeAdapter.create_refund."""

    @pytest.fixture(autouse=True)
    def setup_mocks(self, mock_stripe_http_client):
        """Set up common mocks for all tests."""
        pass

    def test_create_refund_success(self, mock_stripe_refund, mock_refund):
        """Should create Refund and return result."""
        mock_stripe_refund.create.return_value = mock_refund(
            id="re_test123",
            amount=5000,
            status="succeeded",
            payment_intent="pi_original",
        )

        result = StripeAdapter.create_refund(
            payment_intent_id="pi_original",
            idempotency_key="refund-key",
        )

        assert isinstance(result, RefundResult)
        assert result.id == "re_test123"
        assert result.amount_cents == 5000
        assert result.status == "succeeded"
        assert result.payment_intent_id == "pi_original"

    def test_create_partial_refund(self, mock_stripe_refund, mock_refund):
        """Should support partial refund amount."""
        mock_stripe_refund.create.return_value = mock_refund(amount=2500)

        StripeAdapter.create_refund(
            payment_intent_id="pi_test",
            idempotency_key="refund-key",
            amount_cents=2500,
        )

        call_kwargs = mock_stripe_refund.create.call_args.kwargs
        assert call_kwargs["amount"] == 2500

    def test_create_refund_with_reason(self, mock_stripe_refund, mock_refund):
        """Should support refund reason."""
        mock_stripe_refund.create.return_value = mock_refund()

        StripeAdapter.create_refund(
            payment_intent_id="pi_test",
            idempotency_key="refund-key",
            reason="requested_by_customer",
        )

        call_kwargs = mock_stripe_refund.create.call_args.kwargs
        assert call_kwargs["reason"] == "requested_by_customer"


class TestStripeAdapterRetrievePaymentIntent:
    """Tests for StripeAdapter.retrieve_payment_intent."""

    @pytest.fixture(autouse=True)
    def setup_mocks(self, mock_stripe_http_client):
        """Set up common mocks for all tests."""
        pass

    def test_retrieve_payment_intent_success(
        self, mock_stripe_payment_intent, mock_payment_intent
    ):
        """Should retrieve PaymentIntent and return result."""
        mock_stripe_payment_intent.retrieve.return_value = mock_payment_intent(
            id="pi_test123",
            status="succeeded",
            amount=5000,
            amount_received=5000,
        )

        result = StripeAdapter.retrieve_payment_intent("pi_test123")

        assert isinstance(result, PaymentIntentResult)
        assert result.id == "pi_test123"
        assert result.status == "succeeded"
        mock_stripe_payment_intent.retrieve.assert_called_once_with("pi_test123")

    def test_retrieve_not_found(
        self, mock_stripe_payment_intent, invalid_request_error
    ):
        """Should raise StripeInvalidRequestError for not found."""
        mock_stripe_payment_intent.retrieve.side_effect = invalid_request_error(
            message="No such payment_intent: 'pi_invalid'"
        )

        with pytest.raises(StripeInvalidRequestError):
            StripeAdapter.retrieve_payment_intent("pi_invalid")


class TestStripeAdapterListRecentPaymentIntents:
    """Tests for StripeAdapter.list_recent_payment_intents."""

    @pytest.fixture(autouse=True)
    def setup_mocks(self, mock_stripe_http_client):
        """Set up common mocks for all tests."""
        pass

    def test_list_recent_payment_intents_success(
        self, mock_stripe_payment_intent, mock_payment_intent_list
    ):
        """Should list PaymentIntents and return results."""
        mock_stripe_payment_intent.list.return_value = mock_payment_intent_list(count=3)

        created_after = datetime(2024, 1, 1, tzinfo=timezone.utc)
        results = StripeAdapter.list_recent_payment_intents(created_after)

        assert len(results) == 3
        assert all(isinstance(r, PaymentIntentResult) for r in results)

        call_kwargs = mock_stripe_payment_intent.list.call_args.kwargs
        assert "created" in call_kwargs
        assert call_kwargs["limit"] == 100

    def test_list_respects_limit(
        self, mock_stripe_payment_intent, mock_payment_intent_list
    ):
        """Should respect limit parameter."""
        mock_stripe_payment_intent.list.return_value = mock_payment_intent_list(count=2)

        created_after = datetime(2024, 1, 1, tzinfo=timezone.utc)
        StripeAdapter.list_recent_payment_intents(created_after, limit=50)

        call_kwargs = mock_stripe_payment_intent.list.call_args.kwargs
        assert call_kwargs["limit"] == 50

    def test_list_caps_limit_at_100(
        self, mock_stripe_payment_intent, mock_payment_intent_list
    ):
        """Should cap limit at 100 (Stripe max)."""
        mock_stripe_payment_intent.list.return_value = mock_payment_intent_list(count=0)

        created_after = datetime(2024, 1, 1, tzinfo=timezone.utc)
        StripeAdapter.list_recent_payment_intents(created_after, limit=200)

        call_kwargs = mock_stripe_payment_intent.list.call_args.kwargs
        assert call_kwargs["limit"] == 100


class TestStripeAdapterVerifyWebhookSignature:
    """Tests for StripeAdapter.verify_webhook_signature."""

    def test_verify_webhook_signature_success(self, mock_stripe_webhook):
        """Should verify and return event data."""
        result = StripeAdapter.verify_webhook_signature(
            payload=b'{"id": "evt_test"}',
            signature="test_signature",
        )

        assert result["id"] == "evt_test123"
        assert result["type"] == "payment_intent.succeeded"

    def test_verify_webhook_signature_invalid(
        self, mock_stripe_webhook, signature_verification_error
    ):
        """Should raise StripeInvalidRequestError for invalid signature."""
        mock_stripe_webhook.construct_event.side_effect = signature_verification_error

        with pytest.raises(StripeInvalidRequestError) as exc_info:
            StripeAdapter.verify_webhook_signature(
                payload=b"tampered",
                signature="bad_signature",
            )

        assert "signature" in str(exc_info.value).lower()


# =============================================================================
# Configuration Tests
# =============================================================================


class TestStripeAdapterConfiguration:
    """Tests for Stripe adapter configuration."""

    @pytest.fixture(autouse=True)
    def setup_mocks(self, mock_stripe_http_client):
        """Set up common mocks for all tests."""
        pass

    @override_settings(STRIPE_SECRET_KEY="sk_test_custom")
    def test_uses_settings_api_key(
        self, mock_stripe_payment_intent, mock_payment_intent
    ):
        """Should use API key from settings."""
        mock_stripe_payment_intent.create.return_value = mock_payment_intent()

        params = CreatePaymentIntentParams(
            amount_cents=5000,
            currency="usd",
            idempotency_key="test-key",
        )

        StripeAdapter.create_payment_intent(params)

        assert stripe.api_key == "sk_test_custom"

    @override_settings(STRIPE_API_TIMEOUT_SECONDS=30)
    def test_uses_settings_timeout(
        self, mock_stripe_payment_intent, mock_payment_intent, mock_stripe_http_client
    ):
        """Should use timeout from settings."""
        mock_stripe_payment_intent.create.return_value = mock_payment_intent()

        params = CreatePaymentIntentParams(
            amount_cents=5000,
            currency="usd",
            idempotency_key="test-key",
        )

        StripeAdapter.create_payment_intent(params)

        mock_stripe_http_client.assert_called_with(timeout=30)
