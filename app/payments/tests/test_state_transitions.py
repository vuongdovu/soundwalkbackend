"""
Tests for state machine transitions using django-fsm.

Tests valid and invalid state transitions for PaymentOrder,
Payout, and Refund models.
"""

import pytest
from django.utils import timezone
from django_fsm import TransitionNotAllowed

from payments.models import PaymentOrder, Payout, Refund
from payments.state_machines import (
    PaymentOrderState,
    PaymentStrategyType,
    PayoutState,
    RefundState,
    OnboardingStatus,
)


# =============================================================================
# PaymentOrder State Transition Tests
# =============================================================================


class TestPaymentOrderTransitions:
    """Tests for PaymentOrder state machine transitions."""

    # -------------------------------------------------------------------------
    # Valid Transitions
    # -------------------------------------------------------------------------

    def test_draft_to_pending(self, db, draft_order):
        """Should transition from draft to pending."""
        draft_order.submit()
        draft_order.save()

        assert draft_order.state == PaymentOrderState.PENDING

    def test_pending_to_processing(self, db, pending_order):
        """Should transition from pending to processing."""
        pending_order.process()
        pending_order.save()

        assert pending_order.state == PaymentOrderState.PROCESSING

    def test_processing_to_captured(self, db, processing_order):
        """Should transition from processing to captured."""
        processing_order.capture()
        processing_order.save()

        assert processing_order.state == PaymentOrderState.CAPTURED
        assert processing_order.captured_at is not None

    def test_processing_to_held(self, db, processing_order):
        """Should transition from processing to held (escrow)."""
        processing_order.strategy_type = PaymentStrategyType.ESCROW
        processing_order.capture()
        processing_order.save()
        processing_order.hold()
        processing_order.save()

        assert processing_order.state == PaymentOrderState.HELD
        assert processing_order.held_at is not None

    def test_processing_to_failed(self, db, processing_order):
        """Should transition from processing to failed."""
        processing_order.fail(reason="Card declined")
        processing_order.save()

        assert processing_order.state == PaymentOrderState.FAILED
        assert processing_order.failed_at is not None
        assert processing_order.failure_reason == "Card declined"

    def test_captured_to_settled_direct(self, db, captured_order):
        """Should transition from captured to settled (direct path)."""
        captured_order.settle_from_captured()
        captured_order.save()

        assert captured_order.state == PaymentOrderState.SETTLED
        assert captured_order.settled_at is not None

    def test_held_to_released(self, db, held_order):
        """Should transition from held to released."""
        held_order.release()
        held_order.save()

        assert held_order.state == PaymentOrderState.RELEASED
        assert held_order.released_at is not None

    def test_released_to_settled(self, db, released_order):
        """Should transition from released to settled (escrow path)."""
        released_order.settle_from_released()
        released_order.save()

        assert released_order.state == PaymentOrderState.SETTLED
        assert released_order.settled_at is not None

    def test_draft_to_cancelled(self, db, draft_order):
        """Should allow cancellation from draft."""
        draft_order.cancel()
        draft_order.save()

        assert draft_order.state == PaymentOrderState.CANCELLED
        assert draft_order.cancelled_at is not None

    def test_pending_to_cancelled(self, db, pending_order):
        """Should allow cancellation from pending."""
        pending_order.cancel()
        pending_order.save()

        assert pending_order.state == PaymentOrderState.CANCELLED

    def test_captured_to_refunded(self, db, captured_order):
        """Should allow full refund from captured."""
        captured_order.refund_full()
        captured_order.save()

        assert captured_order.state == PaymentOrderState.REFUNDED

    def test_settled_to_partially_refunded(self, db, settled_order):
        """Should allow partial refund from settled."""
        settled_order.refund_partial()
        settled_order.save()

        assert settled_order.state == PaymentOrderState.PARTIALLY_REFUNDED

    def test_failed_to_pending_retry(self, db, failed_order):
        """Should allow retry from failed to pending."""
        failed_order.retry()
        failed_order.save()

        assert failed_order.state == PaymentOrderState.PENDING
        assert failed_order.failed_at is None
        assert failed_order.failure_reason is None

    # -------------------------------------------------------------------------
    # Invalid Transitions
    # -------------------------------------------------------------------------

    def test_draft_cannot_capture(self, db, draft_order):
        """Cannot capture from draft state."""
        with pytest.raises(TransitionNotAllowed):
            draft_order.capture()

    def test_draft_cannot_fail(self, db, draft_order):
        """Cannot fail from draft state."""
        with pytest.raises(TransitionNotAllowed):
            draft_order.fail()

    def test_pending_cannot_capture(self, db, pending_order):
        """Cannot capture from pending state."""
        with pytest.raises(TransitionNotAllowed):
            pending_order.capture()

    def test_captured_cannot_submit(self, db, captured_order):
        """Cannot submit from captured state."""
        with pytest.raises(TransitionNotAllowed):
            captured_order.submit()

    def test_captured_cannot_cancel(self, db, captured_order):
        """Cannot cancel from captured state (must refund)."""
        with pytest.raises(TransitionNotAllowed):
            captured_order.cancel()

    def test_settled_cannot_hold(self, db, settled_order):
        """Cannot hold from settled state."""
        with pytest.raises(TransitionNotAllowed):
            settled_order.hold()

    def test_cancelled_cannot_transition(self, db, cancelled_order):
        """Cannot transition from cancelled state (terminal)."""
        with pytest.raises(TransitionNotAllowed):
            cancelled_order.submit()

    def test_refunded_cannot_transition(self, db, refunded_order):
        """Cannot transition from refunded state (terminal)."""
        with pytest.raises(TransitionNotAllowed):
            refunded_order.refund_partial()


# =============================================================================
# Payout State Transition Tests
# =============================================================================


class TestPayoutTransitions:
    """Tests for Payout state machine transitions."""

    # -------------------------------------------------------------------------
    # Valid Transitions
    # -------------------------------------------------------------------------

    def test_pending_to_scheduled(self, db, pending_payout):
        """Should transition from pending to scheduled."""
        future_time = timezone.now() + timezone.timedelta(days=1)
        pending_payout.schedule(scheduled_for=future_time)
        pending_payout.save()

        assert pending_payout.state == PayoutState.SCHEDULED
        assert pending_payout.scheduled_for == future_time

    def test_pending_to_processing(self, db, pending_payout):
        """Should transition from pending to processing."""
        pending_payout.process()
        pending_payout.save()

        assert pending_payout.state == PayoutState.PROCESSING

    def test_scheduled_to_processing(self, db, scheduled_payout):
        """Should transition from scheduled to processing."""
        scheduled_payout.process()
        scheduled_payout.save()

        assert scheduled_payout.state == PayoutState.PROCESSING

    def test_processing_to_paid(self, db, processing_payout):
        """Should transition from processing to paid."""
        processing_payout.complete()
        processing_payout.save()

        assert processing_payout.state == PayoutState.PAID
        assert processing_payout.paid_at is not None

    def test_processing_to_failed(self, db, processing_payout):
        """Should transition from processing to failed."""
        processing_payout.fail(reason="Account suspended")
        processing_payout.save()

        assert processing_payout.state == PayoutState.FAILED
        assert processing_payout.failed_at is not None
        assert processing_payout.failure_reason == "Account suspended"

    def test_failed_to_pending_retry(self, db, failed_payout):
        """Should allow retry from failed to pending."""
        failed_payout.retry()
        failed_payout.save()

        assert failed_payout.state == PayoutState.PENDING
        assert failed_payout.failed_at is None
        assert failed_payout.failure_reason is None

    # -------------------------------------------------------------------------
    # Invalid Transitions
    # -------------------------------------------------------------------------

    def test_pending_cannot_complete(self, db, pending_payout):
        """Cannot complete from pending state."""
        with pytest.raises(TransitionNotAllowed):
            pending_payout.complete()

    def test_scheduled_cannot_complete(self, db, scheduled_payout):
        """Cannot complete from scheduled state."""
        with pytest.raises(TransitionNotAllowed):
            scheduled_payout.complete()

    def test_paid_cannot_transition(self, db, paid_payout):
        """Cannot transition from paid state (terminal)."""
        with pytest.raises(TransitionNotAllowed):
            paid_payout.fail()


# =============================================================================
# Refund State Transition Tests
# =============================================================================


class TestRefundTransitions:
    """Tests for Refund state machine transitions."""

    # -------------------------------------------------------------------------
    # Valid Transitions
    # -------------------------------------------------------------------------

    def test_requested_to_processing(self, db, requested_refund):
        """Should transition from requested to processing."""
        requested_refund.process()
        requested_refund.save()

        assert requested_refund.state == RefundState.PROCESSING

    def test_processing_to_completed(self, db, processing_refund):
        """Should transition from processing to completed."""
        processing_refund.complete()
        processing_refund.save()

        assert processing_refund.state == RefundState.COMPLETED
        assert processing_refund.completed_at is not None

    def test_processing_to_failed(self, db, processing_refund):
        """Should transition from processing to failed."""
        processing_refund.fail(reason="Refund limit exceeded")
        processing_refund.save()

        assert processing_refund.state == RefundState.FAILED
        assert processing_refund.failed_at is not None
        assert processing_refund.failure_reason == "Refund limit exceeded"

    # -------------------------------------------------------------------------
    # Invalid Transitions
    # -------------------------------------------------------------------------

    def test_requested_cannot_complete(self, db, requested_refund):
        """Cannot complete from requested state."""
        with pytest.raises(TransitionNotAllowed):
            requested_refund.complete()

    def test_completed_cannot_transition(self, db, completed_refund):
        """Cannot transition from completed state (terminal)."""
        with pytest.raises(TransitionNotAllowed):
            completed_refund.fail()

    def test_failed_cannot_transition(self, db, failed_refund):
        """Cannot transition from failed state (terminal)."""
        with pytest.raises(TransitionNotAllowed):
            failed_refund.complete()


# =============================================================================
# Fixtures - PaymentOrder states
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
def profile(db, user):
    """Get profile for test user."""
    from authentication.models import Profile

    profile, _ = Profile.objects.get_or_create(user=user)
    return profile


@pytest.fixture
def connected_account(db, profile):
    """Create a connected account."""
    from payments.models import ConnectedAccount

    return ConnectedAccount.objects.create(
        profile=profile,
        stripe_account_id="acct_test_transitions",
        onboarding_status=OnboardingStatus.COMPLETE,
        payouts_enabled=True,
    )


@pytest.fixture
def draft_order(db, user):
    """Create a draft payment order."""
    return PaymentOrder.objects.create(
        payer=user,
        amount_cents=10000,
        strategy_type=PaymentStrategyType.DIRECT,
    )


@pytest.fixture
def pending_order(db, draft_order):
    """Create a pending payment order."""
    draft_order.submit()
    draft_order.save()
    return draft_order


@pytest.fixture
def processing_order(db, pending_order):
    """Create a processing payment order."""
    pending_order.process()
    pending_order.save()
    return pending_order


@pytest.fixture
def captured_order(db, processing_order):
    """Create a captured payment order."""
    processing_order.capture()
    processing_order.save()
    return processing_order


@pytest.fixture
def held_order(db, user):
    """Create a held payment order (escrow path)."""
    order = PaymentOrder.objects.create(
        payer=user,
        amount_cents=10000,
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
def released_order(db, held_order):
    """Create a released payment order."""
    held_order.release()
    held_order.save()
    return held_order


@pytest.fixture
def settled_order(db, captured_order):
    """Create a settled payment order (direct path)."""
    captured_order.settle_from_captured()
    captured_order.save()
    return captured_order


@pytest.fixture
def failed_order(db, processing_order):
    """Create a failed payment order."""
    processing_order.fail(reason="Card declined")
    processing_order.save()
    return processing_order


@pytest.fixture
def cancelled_order(db, draft_order):
    """Create a cancelled payment order."""
    draft_order.cancel()
    draft_order.save()
    return draft_order


@pytest.fixture
def refunded_order(db, captured_order):
    """Create a refunded payment order."""
    captured_order.refund_full()
    captured_order.save()
    return captured_order


# =============================================================================
# Fixtures - Payout states
# =============================================================================


@pytest.fixture
def pending_payout(db, captured_order, connected_account):
    """Create a pending payout."""
    return Payout.objects.create(
        payment_order=captured_order,
        connected_account=connected_account,
        amount_cents=9000,
    )


@pytest.fixture
def scheduled_payout(db, pending_payout):
    """Create a scheduled payout."""
    future_time = timezone.now() + timezone.timedelta(days=1)
    pending_payout.schedule(scheduled_for=future_time)
    pending_payout.save()
    return pending_payout


@pytest.fixture
def processing_payout(db, pending_payout):
    """Create a processing payout."""
    pending_payout.process()
    pending_payout.save()
    return pending_payout


@pytest.fixture
def paid_payout(db, processing_payout):
    """Create a paid payout."""
    processing_payout.complete()
    processing_payout.save()
    return processing_payout


@pytest.fixture
def failed_payout(db, pending_payout):
    """Create a failed payout."""
    pending_payout.process()
    pending_payout.save()
    pending_payout.fail(reason="Account suspended")
    pending_payout.save()
    return pending_payout


# =============================================================================
# Fixtures - Refund states
# =============================================================================


@pytest.fixture
def requested_refund(db, captured_order):
    """Create a requested refund."""
    return Refund.objects.create(
        payment_order=captured_order,
        amount_cents=5000,
        reason="Customer request",
    )


@pytest.fixture
def processing_refund(db, requested_refund):
    """Create a processing refund."""
    requested_refund.process()
    requested_refund.save()
    return requested_refund


@pytest.fixture
def completed_refund(db, processing_refund):
    """Create a completed refund."""
    processing_refund.complete()
    processing_refund.save()
    return processing_refund


@pytest.fixture
def failed_refund(db, requested_refund):
    """Create a failed refund."""
    requested_refund.process()
    requested_refund.save()
    requested_refund.fail(reason="Refund limit exceeded")
    requested_refund.save()
    return requested_refund
