"""
Tests for PaymentOrchestrator.

Tests cover:
- Payment initiation with different strategies
- Strategy selection and delegation
- PaymentOrder lookups
- Error handling
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest

from authentication.models import User
from payments.models import PaymentOrder
from payments.services import InitiatePaymentParams, PaymentOrchestrator
from payments.state_machines import PaymentStrategyType
from payments.strategies import DirectPaymentStrategy


# =============================================================================
# User Fixtures
# =============================================================================


@pytest.fixture
def test_user(db):
    """Create a test user for payments."""
    return User.objects.create_user(
        email="orchestrator_test@example.com",
        password="testpass123",
    )


# =============================================================================
# InitiatePaymentParams Tests
# =============================================================================


class TestInitiatePaymentParams:
    """Tests for InitiatePaymentParams validation."""

    def test_valid_params(self, test_user):
        """Should create params with valid values."""
        params = InitiatePaymentParams(
            payer=test_user,
            amount_cents=5000,
            currency="usd",
        )

        assert params.payer == test_user
        assert params.amount_cents == 5000
        assert params.currency == "usd"
        assert params.strategy_type == PaymentStrategyType.DIRECT

    def test_amount_must_be_positive(self, test_user):
        """Should raise ValueError for zero or negative amount."""
        with pytest.raises(ValueError, match="amount_cents must be positive"):
            InitiatePaymentParams(
                payer=test_user,
                amount_cents=0,
            )

    def test_currency_required(self, test_user):
        """Should raise ValueError for empty currency."""
        with pytest.raises(ValueError, match="currency is required"):
            InitiatePaymentParams(
                payer=test_user,
                amount_cents=5000,
                currency="",
            )

    def test_default_strategy_type(self, test_user):
        """Should default to DIRECT strategy."""
        params = InitiatePaymentParams(
            payer=test_user,
            amount_cents=5000,
        )

        assert params.strategy_type == PaymentStrategyType.DIRECT

    def test_optional_fields(self, test_user):
        """Should allow optional fields."""
        reference_id = uuid.uuid4()
        params = InitiatePaymentParams(
            payer=test_user,
            amount_cents=5000,
            strategy_type=PaymentStrategyType.DIRECT,
            reference_id=reference_id,
            reference_type="session",
            metadata={"note": "Test payment"},
        )

        assert params.reference_id == reference_id
        assert params.reference_type == "session"
        assert params.metadata == {"note": "Test payment"}


# =============================================================================
# PaymentOrchestrator.initiate_payment Tests
# =============================================================================


class TestPaymentOrchestratorInitiatePayment:
    """Tests for PaymentOrchestrator.initiate_payment."""

    def test_initiate_payment_success(self, test_user):
        """Should initiate payment through direct strategy."""
        # Mock the DirectPaymentStrategy to avoid Stripe calls
        with patch.object(DirectPaymentStrategy, "create_payment") as mock_create:
            # Setup mock to return success
            mock_order = MagicMock(spec=PaymentOrder)
            mock_order.id = uuid.uuid4()

            from core.services import ServiceResult
            from payments.strategies import PaymentResult

            mock_create.return_value = ServiceResult.success(
                PaymentResult(
                    payment_order=mock_order,
                    client_secret="pi_test_secret_123",
                )
            )

            params = InitiatePaymentParams(
                payer=test_user,
                amount_cents=5000,
                currency="usd",
            )

            result = PaymentOrchestrator.initiate_payment(params)

            assert result.success is True
            assert result.data is not None
            assert result.data.client_secret == "pi_test_secret_123"

            # Verify strategy was called with correct params
            mock_create.assert_called_once()
            call_args = mock_create.call_args[0][0]
            assert call_args.payer == test_user
            assert call_args.amount_cents == 5000
            assert call_args.currency == "usd"

    def test_initiate_payment_with_reference(self, test_user):
        """Should pass reference data to strategy."""
        with patch.object(DirectPaymentStrategy, "create_payment") as mock_create:
            mock_order = MagicMock(spec=PaymentOrder)
            mock_order.id = uuid.uuid4()

            from core.services import ServiceResult
            from payments.strategies import PaymentResult

            mock_create.return_value = ServiceResult.success(
                PaymentResult(
                    payment_order=mock_order,
                    client_secret="pi_test_secret_123",
                )
            )

            reference_id = uuid.uuid4()
            params = InitiatePaymentParams(
                payer=test_user,
                amount_cents=10000,
                reference_id=reference_id,
                reference_type="session",
                metadata={"topic": "Python basics"},
            )

            result = PaymentOrchestrator.initiate_payment(params)

            assert result.success is True

            call_args = mock_create.call_args[0][0]
            assert call_args.reference_id == reference_id
            assert call_args.reference_type == "session"
            assert call_args.metadata == {"topic": "Python basics"}

    def test_initiate_payment_strategy_failure(self, test_user):
        """Should propagate strategy failure."""
        with patch.object(DirectPaymentStrategy, "create_payment") as mock_create:
            from core.services import ServiceResult

            mock_create.return_value = ServiceResult.failure(
                "Card was declined",
                error_code="CARD_DECLINED",
            )

            params = InitiatePaymentParams(
                payer=test_user,
                amount_cents=5000,
            )

            result = PaymentOrchestrator.initiate_payment(params)

            assert result.success is False
            assert result.error == "Card was declined"
            assert result.error_code == "CARD_DECLINED"

    def test_initiate_payment_unknown_strategy(self, test_user):
        """Should fail for unknown strategy type."""
        params = InitiatePaymentParams(
            payer=test_user,
            amount_cents=5000,
        )
        # Manually set an invalid strategy type
        params.strategy_type = "invalid_strategy"

        result = PaymentOrchestrator.initiate_payment(params)

        assert result.success is False
        assert result.error_code == "INVALID_PARAMETERS"
        assert "invalid_strategy" in result.error.lower()


# =============================================================================
# PaymentOrchestrator.get_strategy Tests
# =============================================================================


class TestPaymentOrchestratorGetStrategy:
    """Tests for PaymentOrchestrator.get_strategy."""

    def test_get_direct_strategy(self):
        """Should return DirectPaymentStrategy for DIRECT type."""
        strategy = PaymentOrchestrator.get_strategy(PaymentStrategyType.DIRECT)

        assert isinstance(strategy, DirectPaymentStrategy)

    def test_get_unknown_strategy(self):
        """Should raise ValueError for unknown strategy type."""
        with pytest.raises(ValueError, match="Unknown strategy type"):
            PaymentOrchestrator.get_strategy("unknown")

    def test_get_strategy_returns_new_instance(self):
        """Should return new instance each time."""
        strategy1 = PaymentOrchestrator.get_strategy(PaymentStrategyType.DIRECT)
        strategy2 = PaymentOrchestrator.get_strategy(PaymentStrategyType.DIRECT)

        assert strategy1 is not strategy2


# =============================================================================
# PaymentOrchestrator.get_payment_order Tests
# =============================================================================


class TestPaymentOrchestratorGetPaymentOrder:
    """Tests for PaymentOrchestrator.get_payment_order."""

    def test_get_existing_order(self, db, test_user):
        """Should return existing payment order."""
        order = PaymentOrder.objects.create(
            payer=test_user,
            amount_cents=5000,
            currency="usd",
        )

        found = PaymentOrchestrator.get_payment_order(order.id)

        assert found is not None
        assert found.id == order.id
        assert found.amount_cents == 5000

    def test_get_nonexistent_order(self, db):
        """Should return None for nonexistent order."""
        fake_id = uuid.uuid4()

        found = PaymentOrchestrator.get_payment_order(fake_id)

        assert found is None


# =============================================================================
# PaymentOrchestrator.get_payment_by_intent Tests
# =============================================================================


class TestPaymentOrchestratorGetPaymentByIntent:
    """Tests for PaymentOrchestrator.get_payment_by_intent."""

    def test_get_by_intent_existing(self, db, test_user):
        """Should return order by PaymentIntent ID."""
        order = PaymentOrder.objects.create(
            payer=test_user,
            amount_cents=5000,
            currency="usd",
            stripe_payment_intent_id="pi_test_lookup_123",
        )

        found = PaymentOrchestrator.get_payment_by_intent("pi_test_lookup_123")

        assert found is not None
        assert found.id == order.id

    def test_get_by_intent_nonexistent(self, db):
        """Should return None for nonexistent intent."""
        found = PaymentOrchestrator.get_payment_by_intent("pi_does_not_exist")

        assert found is None


# =============================================================================
# PaymentOrchestrator.get_strategy_for_order Tests
# =============================================================================


class TestPaymentOrchestratorGetStrategyForOrder:
    """Tests for PaymentOrchestrator.get_strategy_for_order."""

    def test_get_strategy_for_direct_order(self, db, test_user):
        """Should return DirectPaymentStrategy for direct order."""
        order = PaymentOrder.objects.create(
            payer=test_user,
            amount_cents=5000,
            currency="usd",
            strategy_type=PaymentStrategyType.DIRECT,
        )

        strategy = PaymentOrchestrator.get_strategy_for_order(order)

        assert isinstance(strategy, DirectPaymentStrategy)
