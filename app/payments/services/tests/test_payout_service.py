"""
Tests for PayoutService.

Tests cover:
- Payout execution with two-phase commit pattern
- Connected account validation
- Distributed lock acquisition
- Stripe transfer creation
- Error handling (transient vs permanent)
- Idempotency behavior
- State transitions

Note: This test file uses fixtures from payments/tests/conftest.py for
shared test data like connected_account, pending_payout, etc.
"""

import uuid
from unittest.mock import MagicMock

import pytest
from django.utils import timezone


from payments.adapters import TransferResult
from payments.exceptions import (
    LockAcquisitionError,
    PaymentNotFoundError,
    StripeAPIUnavailableError,
    StripeInvalidAccountError,
    StripeRateLimitError,
    StripeTimeoutError,
)
from payments.models import Payout
from payments.services import PayoutService
from payments.state_machines import OnboardingStatus, PayoutState
from payments.tests.factories import (
    ConnectedAccountFactory,
    PaymentOrderFactory,
    PayoutFactory,
)


def get_fresh_payout(payout_id) -> Payout:
    """
    Get a fresh Payout instance from the database.

    This is needed because django-fsm's protected FSMField doesn't allow
    direct state assignment via refresh_from_db(). We get a new instance
    to check the current database state.
    """
    return Payout.objects.get(id=payout_id)


# =============================================================================
# Test Fixtures
# Note: These fixtures shadow the ones in conftest.py to provide service-specific
# test data without dependencies on the broader fixtures.
# =============================================================================


@pytest.fixture
def payout_test_user(db):
    """Create a test user for payout tests."""
    from payments.tests.factories import UserFactory

    return UserFactory()


@pytest.fixture
def payout_test_profile(db, payout_test_user):
    """Get or create profile for payout test user."""
    from authentication.models import Profile

    profile, _ = Profile.objects.get_or_create(user=payout_test_user)
    return profile


@pytest.fixture
def payout_connected_account(db, payout_test_profile):
    """Create a connected account ready for payouts."""
    return ConnectedAccountFactory(
        profile=payout_test_profile,
        onboarding_status=OnboardingStatus.COMPLETE,
        payouts_enabled=True,
        charges_enabled=True,
    )


@pytest.fixture
def payout_incomplete_connected_account(db, payout_test_profile):
    """Create a connected account NOT ready for payouts."""
    return ConnectedAccountFactory(
        profile=payout_test_profile,
        onboarding_status=OnboardingStatus.IN_PROGRESS,
        payouts_enabled=False,
        charges_enabled=False,
    )


@pytest.fixture
def payout_test_order(db, payout_test_user):
    """Create a payment order for payout testing."""
    return PaymentOrderFactory(payer=payout_test_user)


@pytest.fixture
def service_pending_payout(db, payout_test_order, payout_connected_account):
    """Create a pending payout ready for execution."""
    return PayoutFactory(
        payment_order=payout_test_order,
        connected_account=payout_connected_account,
        amount_cents=9000,
        currency="usd",
    )


@pytest.fixture
def mock_stripe_adapter():
    """Mock the StripeAdapter for testing."""
    mock_adapter = MagicMock()

    def mock_create_transfer(
        amount_cents,
        currency="usd",
        destination_account_id=None,
        idempotency_key=None,
        metadata=None,
    ):
        return TransferResult(
            id=f"tr_mock_{uuid.uuid4().hex[:8]}",
            amount_cents=amount_cents,
            currency=currency,
            destination_account=destination_account_id,
        )

    mock_adapter.create_transfer = mock_create_transfer
    return mock_adapter


@pytest.fixture
def mock_redis_lock(mocker):
    """Mock Redis for distributed locking."""
    mock_redis = mocker.MagicMock()
    mock_redis.set.return_value = True
    mock_redis.get.return_value = None
    mock_redis.delete.return_value = 1
    mock_redis.eval.return_value = 1

    mocker.patch(
        "payments.locks.get_redis_connection",
        return_value=mock_redis,
    )
    return mock_redis


# =============================================================================
# PayoutService.execute_payout Tests
# =============================================================================


class TestPayoutServiceExecutePayout:
    """Tests for PayoutService.execute_payout."""

    def test_execute_payout_success(
        self, service_pending_payout, mock_redis_lock, mock_stripe_adapter
    ):
        """Should execute payout and store transfer_id."""
        # Inject mock adapter
        PayoutService.set_stripe_adapter(mock_stripe_adapter)
        try:
            result = PayoutService.execute_payout(service_pending_payout.id)

            assert result.success is True
            assert result.data is not None
            assert result.data.stripe_transfer_id is not None
            assert result.data.stripe_transfer_id.startswith("tr_mock_")

            # Verify payout was updated
            updated_payout = get_fresh_payout(service_pending_payout.id)
            assert updated_payout.state == PayoutState.PROCESSING
            assert updated_payout.stripe_transfer_id is not None
        finally:
            PayoutService.set_stripe_adapter(None)

    def test_execute_payout_not_found(self, db, mock_redis_lock):
        """Should raise PaymentNotFoundError for nonexistent payout."""
        fake_id = uuid.uuid4()

        with pytest.raises(PaymentNotFoundError) as exc_info:
            PayoutService.execute_payout(fake_id)

        assert str(fake_id) in str(exc_info.value)

    def test_execute_payout_already_paid(
        self, db, payout_test_order, payout_connected_account, mock_redis_lock
    ):
        """Should return success for already paid payout (idempotent)."""
        payout = PayoutFactory(
            payment_order=payout_test_order,
            connected_account=payout_connected_account,
        )
        # Manually transition to PAID
        payout.process()
        payout.save()
        payout.stripe_transfer_id = "tr_already_paid"
        payout.complete()
        payout.save()

        result = PayoutService.execute_payout(payout.id)

        assert result.success is True
        assert result.data.payout.state == PayoutState.PAID

    def test_execute_payout_already_processing(
        self, service_pending_payout, mock_redis_lock
    ):
        """Should return success for payout already in PROCESSING (idempotent)."""
        # Manually transition to PROCESSING
        service_pending_payout.process()
        service_pending_payout.stripe_transfer_id = "tr_in_progress"
        service_pending_payout.save()

        result = PayoutService.execute_payout(service_pending_payout.id)

        assert result.success is True
        assert result.data.payout.state == PayoutState.PROCESSING

    def test_execute_payout_failed_state_returns_error(
        self, db, payout_test_order, payout_connected_account, mock_redis_lock
    ):
        """Should return failure for payout in FAILED state."""
        payout = PayoutFactory(
            payment_order=payout_test_order,
            connected_account=payout_connected_account,
        )
        # Manually transition to FAILED
        payout.process()
        payout.save()
        payout.fail(reason="Previous attempt failed")
        payout.save()

        result = PayoutService.execute_payout(payout.id)

        assert result.success is False
        assert result.error_code == "PAYOUT_FAILED"
        assert "retry()" in result.error.lower()


class TestPayoutServiceConnectedAccountValidation:
    """Tests for connected account validation in PayoutService."""

    def test_execute_payout_account_not_ready(
        self,
        db,
        payout_test_order,
        payout_incomplete_connected_account,
        mock_redis_lock,
    ):
        """Should fail if connected account is not ready for payouts."""
        payout = PayoutFactory(
            payment_order=payout_test_order,
            connected_account=payout_incomplete_connected_account,
        )

        result = PayoutService.execute_payout(payout.id)

        assert result.success is False
        assert result.error_code == "ACCOUNT_NOT_READY"
        assert "not ready for payouts" in result.error.lower()

        # Payout should remain in PENDING
        updated_payout = get_fresh_payout(payout.id)
        assert updated_payout.state == PayoutState.PENDING


class TestPayoutServiceStripeErrors:
    """Tests for Stripe error handling in PayoutService."""

    def test_execute_payout_rate_limit_raises(
        self, service_pending_payout, mock_redis_lock
    ):
        """Should raise StripeRateLimitError (transient - for retry)."""
        mock_adapter = MagicMock()
        mock_adapter.create_transfer.side_effect = StripeRateLimitError("Rate limited")
        PayoutService.set_stripe_adapter(mock_adapter)

        try:
            with pytest.raises(StripeRateLimitError):
                PayoutService.execute_payout(service_pending_payout.id)

            # Payout should be in PROCESSING (committed before Stripe call)
            updated_payout = get_fresh_payout(service_pending_payout.id)
            assert updated_payout.state == PayoutState.PROCESSING
        finally:
            PayoutService.set_stripe_adapter(None)

    def test_execute_payout_api_unavailable_raises(
        self, service_pending_payout, mock_redis_lock
    ):
        """Should raise StripeAPIUnavailableError (transient - for retry)."""
        mock_adapter = MagicMock()
        mock_adapter.create_transfer.side_effect = StripeAPIUnavailableError(
            "Stripe unavailable"
        )
        PayoutService.set_stripe_adapter(mock_adapter)

        try:
            with pytest.raises(StripeAPIUnavailableError):
                PayoutService.execute_payout(service_pending_payout.id)

            # Payout should be in PROCESSING
            updated_payout = get_fresh_payout(service_pending_payout.id)
            assert updated_payout.state == PayoutState.PROCESSING
        finally:
            PayoutService.set_stripe_adapter(None)

    def test_execute_payout_timeout_raises(
        self, service_pending_payout, mock_redis_lock
    ):
        """Should raise StripeTimeoutError (transient - for retry)."""
        mock_adapter = MagicMock()
        mock_adapter.create_transfer.side_effect = StripeTimeoutError(
            "Request timed out"
        )
        PayoutService.set_stripe_adapter(mock_adapter)

        try:
            with pytest.raises(StripeTimeoutError):
                PayoutService.execute_payout(service_pending_payout.id)

            # Payout should be in PROCESSING
            updated_payout = get_fresh_payout(service_pending_payout.id)
            assert updated_payout.state == PayoutState.PROCESSING
        finally:
            PayoutService.set_stripe_adapter(None)

    def test_execute_payout_invalid_account_fails(
        self, service_pending_payout, mock_redis_lock
    ):
        """Should mark payout as FAILED for invalid account (permanent)."""
        mock_adapter = MagicMock()
        mock_adapter.create_transfer.side_effect = StripeInvalidAccountError(
            "No such account"
        )
        PayoutService.set_stripe_adapter(mock_adapter)

        try:
            result = PayoutService.execute_payout(service_pending_payout.id)

            assert result.success is False
            assert result.error_code == "PAYOUT_FAILED"

            # Payout should be marked as FAILED
            updated_payout = get_fresh_payout(service_pending_payout.id)
            assert updated_payout.state == PayoutState.FAILED
            assert updated_payout.failure_reason is not None
        finally:
            PayoutService.set_stripe_adapter(None)


class TestPayoutServiceDistributedLocking:
    """Tests for distributed locking in PayoutService."""

    def test_execute_payout_acquires_lock(
        self, service_pending_payout, mock_redis_lock, mock_stripe_adapter
    ):
        """Should acquire distributed lock before execution."""
        PayoutService.set_stripe_adapter(mock_stripe_adapter)

        try:
            PayoutService.execute_payout(service_pending_payout.id)

            # Verify Redis SET was called for lock acquisition
            expected_key = f"lock:payout:execute:{service_pending_payout.id}"
            set_calls = [
                call
                for call in mock_redis_lock.set.call_args_list
                if expected_key in str(call)
            ]
            assert len(set_calls) > 0, "Lock should be acquired"
        finally:
            PayoutService.set_stripe_adapter(None)

    def test_execute_payout_lock_failure(self, service_pending_payout, mocker):
        """Should raise LockAcquisitionError when lock cannot be acquired."""
        # Mock DistributedLock at the service level to simulate lock failure
        # This avoids re-testing the lock's timeout loop (tested in test_locks.py)
        mock_lock = mocker.patch("payments.services.payout_service.DistributedLock")
        mock_lock.return_value.__enter__.side_effect = LockAcquisitionError(
            "Lock already held", details={"key": "test"}
        )

        with pytest.raises(LockAcquisitionError):
            PayoutService.execute_payout(service_pending_payout.id)

        # Payout should remain in PENDING
        updated_payout = get_fresh_payout(service_pending_payout.id)
        assert updated_payout.state == PayoutState.PENDING


class TestPayoutServiceIdempotency:
    """Tests for idempotency in PayoutService."""

    def test_execute_payout_idempotency_key_includes_attempt(
        self, service_pending_payout, mock_redis_lock
    ):
        """Should include attempt number in idempotency key."""
        call_idempotency_keys = []

        def capture_create_transfer(**kwargs):
            call_idempotency_keys.append(kwargs.get("idempotency_key"))
            return TransferResult(
                id="tr_mock",
                amount_cents=service_pending_payout.amount_cents,
                currency=service_pending_payout.currency,
                destination_account=service_pending_payout.connected_account.stripe_account_id,
            )

        mock_adapter = MagicMock()
        mock_adapter.create_transfer.side_effect = capture_create_transfer
        PayoutService.set_stripe_adapter(mock_adapter)

        try:
            PayoutService.execute_payout(service_pending_payout.id, attempt=1)

            assert len(call_idempotency_keys) == 1
            key = call_idempotency_keys[0]
            assert ":1:" in key  # Attempt 1 in key
        finally:
            PayoutService.set_stripe_adapter(None)


class TestPayoutServiceTwoPhaseCommit:
    """Tests for two-phase commit behavior in PayoutService."""

    def test_stripe_failure_leaves_payout_in_processing(
        self, service_pending_payout, mock_redis_lock
    ):
        """Stripe failure should leave payout in PROCESSING state."""
        mock_adapter = MagicMock()
        mock_adapter.create_transfer.side_effect = StripeAPIUnavailableError(
            "Stripe down"
        )
        PayoutService.set_stripe_adapter(mock_adapter)

        try:
            with pytest.raises(StripeAPIUnavailableError):
                PayoutService.execute_payout(service_pending_payout.id)

            # Payout committed to PROCESSING before Stripe call
            updated_payout = get_fresh_payout(service_pending_payout.id)
            assert updated_payout.state == PayoutState.PROCESSING
        finally:
            PayoutService.set_stripe_adapter(None)

    def test_db_failure_after_stripe_success_logs_for_reconciliation(
        self, service_pending_payout, mock_redis_lock, mocker
    ):
        """DB failure after Stripe success should still return success.

        This test verifies the two-phase commit pattern:
        1. Payout transitions to PROCESSING and commits (Phase 1)
        2. Stripe transfer is created (Phase 2)
        3. Even if DB fails when storing stripe_transfer_id, we return success
           because the money is already moving and reconciliation will fix it
        """
        mock_adapter = MagicMock()
        mock_adapter.create_transfer.return_value = TransferResult(
            id="tr_success",
            amount_cents=service_pending_payout.amount_cents,
            currency=service_pending_payout.currency,
            destination_account=service_pending_payout.connected_account.stripe_account_id,
        )
        PayoutService.set_stripe_adapter(mock_adapter)

        # Track select_for_update calls to fail on second (Phase 3)
        original_select_for_update = Payout.objects.select_for_update
        call_count = [0]

        def failing_select_for_update(*args, **kwargs):
            call_count[0] += 1
            qs = original_select_for_update(*args, **kwargs)
            if call_count[0] >= 2:
                # Wrap the queryset to fail on .get()
                def failing_get(*args, **kwargs):
                    raise Exception("Simulated DB failure")

                qs.get = failing_get
            return qs

        mocker.patch.object(
            Payout.objects, "select_for_update", side_effect=failing_select_for_update
        )

        try:
            # This should still return success since Stripe transfer was created
            result = PayoutService.execute_payout(service_pending_payout.id)

            # Even with DB failure, we return success because Stripe has the transfer
            assert result.success is True
            assert result.data.stripe_transfer_id == "tr_success"
        finally:
            PayoutService.set_stripe_adapter(None)


# =============================================================================
# PayoutService.get_pending_payouts Tests
# =============================================================================


class TestPayoutServiceGetPendingPayouts:
    """Tests for PayoutService.get_pending_payouts."""

    def test_get_pending_payouts_returns_pending(
        self, db, payout_test_order, payout_connected_account
    ):
        """Should return payouts in PENDING state."""
        payout = PayoutFactory(
            payment_order=payout_test_order,
            connected_account=payout_connected_account,
        )

        pending = PayoutService.get_pending_payouts()

        assert payout in pending

    def test_get_pending_payouts_excludes_paid(
        self, db, payout_test_order, payout_connected_account
    ):
        """Should exclude payouts in PAID state."""
        payout = PayoutFactory(
            payment_order=payout_test_order,
            connected_account=payout_connected_account,
        )
        payout.process()
        payout.save()
        payout.complete()
        payout.save()

        pending = PayoutService.get_pending_payouts()

        assert payout not in pending

    def test_get_pending_payouts_scheduled_for_now(
        self, db, payout_test_order, payout_connected_account
    ):
        """Should include payouts scheduled for now or earlier."""
        payout = PayoutFactory(
            payment_order=payout_test_order,
            connected_account=payout_connected_account,
        )
        # Schedule for past
        past = timezone.now() - timezone.timedelta(hours=1)
        payout.schedule(scheduled_for=past)
        payout.save()

        pending = PayoutService.get_pending_payouts()

        assert payout in pending

    def test_get_pending_payouts_scheduled_future(
        self, db, payout_test_order, payout_connected_account
    ):
        """Should exclude payouts scheduled for future."""
        payout = PayoutFactory(
            payment_order=payout_test_order,
            connected_account=payout_connected_account,
        )
        # Schedule for future
        future = timezone.now() + timezone.timedelta(hours=1)
        payout.schedule(scheduled_for=future)
        payout.save()

        pending = PayoutService.get_pending_payouts()

        assert payout not in pending

    def test_get_pending_payouts_respects_limit(
        self, db, payout_test_order, payout_connected_account
    ):
        """Should respect limit parameter."""
        for _ in range(5):
            PayoutFactory(
                payment_order=payout_test_order,
                connected_account=payout_connected_account,
            )

        pending = PayoutService.get_pending_payouts(limit=3)

        assert len(pending) == 3


# =============================================================================
# PayoutService.get_failed_payouts Tests
# =============================================================================


class TestPayoutServiceGetFailedPayouts:
    """Tests for PayoutService.get_failed_payouts."""

    def test_get_failed_payouts_returns_failed(
        self, db, payout_test_order, payout_connected_account
    ):
        """Should return payouts in FAILED state."""
        payout = PayoutFactory(
            payment_order=payout_test_order,
            connected_account=payout_connected_account,
        )
        payout.process()
        payout.save()
        payout.fail(reason="Test failure")
        payout.save()

        failed = PayoutService.get_failed_payouts()

        assert payout in failed

    def test_get_failed_payouts_excludes_max_retries(
        self, db, payout_test_order, payout_connected_account
    ):
        """Should exclude payouts that have exceeded max retries."""
        payout = PayoutFactory(
            payment_order=payout_test_order,
            connected_account=payout_connected_account,
            metadata={"retry_count": 10},  # Exceeded max
        )
        payout.process()
        payout.save()
        payout.fail(reason="Test failure")
        payout.save()

        failed = PayoutService.get_failed_payouts(max_retry_count=5)

        assert payout not in failed


# =============================================================================
# PayoutService._validate_payout Tests
# =============================================================================


class TestPayoutServiceValidatePayout:
    """Tests for PayoutService._validate_payout."""

    def test_validate_payout_positive_amount(
        self, db, payout_test_order, payout_connected_account
    ):
        """Should pass validation for positive amount."""
        payout = PayoutFactory(
            payment_order=payout_test_order,
            connected_account=payout_connected_account,
            amount_cents=1000,
        )

        result = PayoutService._validate_payout(payout)

        assert result.success is True

    def test_validate_payout_paid_returns_success(
        self, db, payout_test_order, payout_connected_account
    ):
        """Should return success for already paid payout."""
        payout = PayoutFactory(
            payment_order=payout_test_order,
            connected_account=payout_connected_account,
        )
        payout.process()
        payout.save()
        payout.stripe_transfer_id = "tr_paid"
        payout.complete()
        payout.save()

        result = PayoutService._validate_payout(payout)

        assert result.success is True
        assert result.data.payout.state == PayoutState.PAID


# =============================================================================
# PayoutService.get_payout Tests
# =============================================================================


class TestPayoutServiceGetPayout:
    """Tests for PayoutService.get_payout."""

    def test_get_payout_exists(self, service_pending_payout):
        """Should return payout when it exists."""
        result = PayoutService.get_payout(service_pending_payout.id)

        assert result is not None
        assert result.id == service_pending_payout.id

    def test_get_payout_not_found(self, db):
        """Should return None for nonexistent payout."""
        result = PayoutService.get_payout(uuid.uuid4())

        assert result is None
