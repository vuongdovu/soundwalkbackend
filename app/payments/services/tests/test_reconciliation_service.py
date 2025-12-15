"""
Tests for ReconciliationService.

Tests cover:
- Discrepancy detection for PaymentOrders
- Discrepancy detection for Payouts
- Auto-healing of clear-cut discrepancies
- Flagging of ambiguous cases for review
- Stuck record detection
- Phase 3 failure recovery (payout without stripe_transfer_id)
- Concurrency safety (lock acquisition)
- Database record persistence

Note: This test file uses mocked Stripe adapters since reconciliation
queries Stripe's actual state.
"""

import uuid
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from payments.adapters import PaymentIntentResult, TransferResult
from payments.models import (
    ConnectedAccount,
    PaymentOrder,
    Payout,
    ReconciliationDiscrepancy,
    ReconciliationRun,
)
from payments.models.reconciliation import (
    DiscrepancyResolution,
    ReconciliationRunStatus,
)
from payments.services.reconciliation_service import (
    Discrepancy,
    DiscrepancyType,
    ReconciliationService,
)
from payments.state_machines import (
    OnboardingStatus,
    PaymentOrderState,
    PayoutState,
)
from payments.tests.factories import (
    PaymentOrderFactory,
)


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def mock_stripe_adapter():
    """Create a mock Stripe adapter for tests."""
    adapter = MagicMock()
    return adapter


@pytest.fixture
def reconciliation_payment_order(db):
    """Create a payment order in PROCESSING state with a Stripe PaymentIntent ID."""
    order = PaymentOrderFactory()
    order.stripe_payment_intent_id = f"pi_test_{uuid.uuid4().hex[:16]}"
    # Transition to PROCESSING
    order.submit()
    order.process()
    order.save()
    return order


@pytest.fixture
def reconciliation_payout(db):
    """Create a payout in PROCESSING state with a Stripe transfer ID."""
    from authentication.models import User

    # Create user directly - this triggers signal to create Profile
    user = User.objects.create_user(
        email=f"payout_test_{uuid.uuid4().hex[:8]}@example.com",
        password="testpass123",
    )
    # Get the auto-created profile
    profile = user.profile

    # Create connected account with the profile
    account = ConnectedAccount.objects.create(
        profile=profile,
        stripe_account_id=f"acct_test_{uuid.uuid4().hex[:8]}",
        onboarding_status=OnboardingStatus.COMPLETE,
        payouts_enabled=True,
        charges_enabled=True,
    )

    order = PaymentOrderFactory()
    order.stripe_payment_intent_id = f"pi_test_{uuid.uuid4().hex[:16]}"
    order.submit()
    order.process()
    order.capture()
    order.save()

    # Use Payout.objects.create to avoid factory nesting
    payout = Payout.objects.create(
        payment_order=order,
        connected_account=account,
        amount_cents=9000,
        currency="usd",
    )
    payout.stripe_transfer_id = f"tr_test_{uuid.uuid4().hex[:16]}"
    payout.schedule(scheduled_for=timezone.now())
    payout.process()
    payout.save()
    return payout


@pytest.fixture
def stuck_payment_order(db):
    """Create a payment order stuck in PROCESSING for many hours."""
    order = PaymentOrderFactory()
    order.stripe_payment_intent_id = f"pi_test_{uuid.uuid4().hex[:16]}"
    order.submit()
    order.process()
    order.save()

    # Manually backdate the updated_at to simulate being stuck
    PaymentOrder.objects.filter(id=order.id).update(
        updated_at=timezone.now() - timedelta(hours=5)
    )

    return PaymentOrder.objects.get(id=order.id)


# =============================================================================
# Helper Functions
# =============================================================================


def create_payment_intent_result(
    status: str, amount: int = 10000
) -> PaymentIntentResult:
    """Create a mock PaymentIntentResult."""
    pi_id = f"pi_{uuid.uuid4().hex}"
    return PaymentIntentResult(
        id=pi_id,
        status=status,
        amount_cents=amount,
        currency="usd",
        client_secret=f"{pi_id}_secret_test",
        captured=status == "succeeded",
        raw_response={
            "id": pi_id,
            "status": status,
            "amount": amount,
            "amount_received": amount if status == "succeeded" else 0,
            "currency": "usd",
        },
    )


def create_transfer_result(
    transfer_id: str,
    status: str,
    payout_id: str = None,
    destination_account: str = "acct_test_dest",
) -> TransferResult:
    """Create a mock TransferResult."""
    metadata = {}
    if payout_id:
        metadata["payout_id"] = payout_id

    return TransferResult(
        id=transfer_id,
        amount_cents=9000,
        currency="usd",
        destination_account=destination_account,
        metadata=metadata,
        raw_response={
            "id": transfer_id,
            "status": status,
            "amount": 9000,
            "currency": "usd",
            "destination": destination_account,
            "metadata": metadata,
        },
    )


# =============================================================================
# Test: PaymentOrder Discrepancy Detection
# =============================================================================


@pytest.mark.django_db
class TestPaymentOrderDiscrepancyDetection:
    """Test discrepancy detection for PaymentOrders."""

    def test_detect_stripe_succeeded_local_processing(
        self, reconciliation_payment_order, mock_stripe_adapter
    ):
        """Detect when Stripe shows succeeded but local state is PROCESSING."""
        # Setup: Stripe says succeeded
        mock_stripe_adapter.retrieve_payment_intent.return_value = (
            create_payment_intent_result("succeeded")
        )

        ReconciliationService.set_stripe_adapter(mock_stripe_adapter)

        try:
            discrepancy = ReconciliationService._check_payment_order(
                reconciliation_payment_order,
                stuck_threshold_hours=2,
            )

            assert discrepancy is not None
            assert (
                discrepancy.discrepancy_type
                == DiscrepancyType.STRIPE_SUCCEEDED_LOCAL_PROCESSING
            )
            assert discrepancy.entity_type == "payment_order"
            assert discrepancy.entity_id == reconciliation_payment_order.id
            assert discrepancy.local_state == PaymentOrderState.PROCESSING
            assert discrepancy.stripe_state == "succeeded"
        finally:
            ReconciliationService.set_stripe_adapter(None)

    def test_detect_stripe_canceled_local_active(
        self, reconciliation_payment_order, mock_stripe_adapter
    ):
        """Detect when Stripe shows canceled but local state is PROCESSING."""
        mock_stripe_adapter.retrieve_payment_intent.return_value = (
            create_payment_intent_result("canceled")
        )

        ReconciliationService.set_stripe_adapter(mock_stripe_adapter)

        try:
            discrepancy = ReconciliationService._check_payment_order(
                reconciliation_payment_order,
                stuck_threshold_hours=2,
            )

            assert discrepancy is not None
            assert (
                discrepancy.discrepancy_type
                == DiscrepancyType.STRIPE_CANCELED_LOCAL_ACTIVE
            )
        finally:
            ReconciliationService.set_stripe_adapter(None)

    def test_detect_stuck_in_processing(self, stuck_payment_order, mock_stripe_adapter):
        """Detect payment orders stuck in PROCESSING for too long."""
        # Stripe shows still processing (not succeeded yet)
        mock_stripe_adapter.retrieve_payment_intent.return_value = (
            create_payment_intent_result("processing")
        )

        ReconciliationService.set_stripe_adapter(mock_stripe_adapter)

        try:
            discrepancy = ReconciliationService._check_payment_order(
                stuck_payment_order,
                stuck_threshold_hours=2,  # Our order is 5 hours old
            )

            assert discrepancy is not None
            assert (
                discrepancy.discrepancy_type
                == DiscrepancyType.PAYMENT_STUCK_IN_PROCESSING
            )
            assert "hours_stuck" in discrepancy.details
        finally:
            ReconciliationService.set_stripe_adapter(None)

    def test_no_discrepancy_when_states_match(
        self, reconciliation_payment_order, mock_stripe_adapter
    ):
        """No discrepancy when local and Stripe states match."""
        # Stripe also shows processing
        mock_stripe_adapter.retrieve_payment_intent.return_value = (
            create_payment_intent_result("processing")
        )

        ReconciliationService.set_stripe_adapter(mock_stripe_adapter)

        try:
            discrepancy = ReconciliationService._check_payment_order(
                reconciliation_payment_order,
                stuck_threshold_hours=2,
            )

            # No discrepancy because states match and not stuck yet
            assert discrepancy is None
        finally:
            ReconciliationService.set_stripe_adapter(None)


# =============================================================================
# Test: Payout Discrepancy Detection
# =============================================================================


@pytest.mark.django_db
class TestPayoutDiscrepancyDetection:
    """Test discrepancy detection for Payouts."""

    def test_detect_stripe_transfer_paid_local_processing(
        self, reconciliation_payout, mock_stripe_adapter
    ):
        """Detect when Stripe transfer is paid but local state is PROCESSING."""
        mock_stripe_adapter.retrieve_transfer.return_value = create_transfer_result(
            reconciliation_payout.stripe_transfer_id,
            "paid",
        )

        ReconciliationService.set_stripe_adapter(mock_stripe_adapter)

        try:
            discrepancy = ReconciliationService._check_payout(
                reconciliation_payout,
                stuck_threshold_hours=2,
            )

            assert discrepancy is not None
            assert (
                discrepancy.discrepancy_type
                == DiscrepancyType.STRIPE_TRANSFER_PAID_LOCAL_PROCESSING
            )
            assert discrepancy.entity_type == "payout"
            assert discrepancy.stripe_state == "paid"
        finally:
            ReconciliationService.set_stripe_adapter(None)

    def test_detect_phase3_failure_transfer_exists_no_id(self, db, mock_stripe_adapter):
        """
        Detect Phase 3 failure scenario where Stripe transfer exists
        but payout has no stripe_transfer_id.

        This is the critical recovery scenario documented in payout_service.py:417-436.
        """
        from authentication.models import User

        # Create user directly - this triggers signal to create Profile
        user = User.objects.create_user(
            email=f"phase3_test_{uuid.uuid4().hex[:8]}@example.com",
            password="testpass123",
        )
        profile = user.profile

        # Create connected account with the profile
        account = ConnectedAccount.objects.create(
            profile=profile,
            stripe_account_id=f"acct_phase3_{uuid.uuid4().hex[:8]}",
            onboarding_status=OnboardingStatus.COMPLETE,
            payouts_enabled=True,
            charges_enabled=True,
        )

        # Create order separately (not through PayoutFactory)
        order = PaymentOrderFactory()
        order.submit()
        order.process()
        order.capture()
        order.save()

        # Create payout with explicit relationships
        payout = Payout.objects.create(
            payment_order=order,
            connected_account=account,
            amount_cents=9000,
            currency="usd",
        )
        payout.schedule(scheduled_for=timezone.now())
        payout.process()
        # Note: NOT setting stripe_transfer_id - simulates Phase 3 failure
        payout.save()

        # Mock finding the transfer via metadata search
        transfer_id = f"tr_found_{uuid.uuid4().hex[:8]}"
        mock_stripe_adapter.list_recent_transfers.return_value = [
            create_transfer_result(transfer_id, "paid", payout_id=str(payout.id))
        ]

        ReconciliationService.set_stripe_adapter(mock_stripe_adapter)

        try:
            # Refresh to get fresh instance
            payout = Payout.objects.select_related(
                "payment_order", "connected_account"
            ).get(id=payout.id)

            discrepancy = ReconciliationService._check_payout(
                payout,
                stuck_threshold_hours=2,
            )

            assert discrepancy is not None
            assert (
                discrepancy.discrepancy_type
                == DiscrepancyType.STRIPE_TRANSFER_EXISTS_LOCAL_PROCESSING_NO_ID
            )
            assert discrepancy.stripe_id == transfer_id
            assert discrepancy.details["transfer_id"] == transfer_id
        finally:
            ReconciliationService.set_stripe_adapter(None)


# =============================================================================
# Test: Healing Logic
# =============================================================================


@pytest.mark.django_db
class TestHealingLogic:
    """Test auto-healing of discrepancies."""

    def test_heal_payment_succeeded_transitions_to_captured(
        self, reconciliation_payment_order, mock_stripe_adapter
    ):
        """Auto-heal transitions payment from PROCESSING to CAPTURED."""
        mock_stripe_adapter.retrieve_payment_intent.return_value = (
            create_payment_intent_result("succeeded")
        )

        ReconciliationService.set_stripe_adapter(mock_stripe_adapter)

        try:
            discrepancy = Discrepancy(
                discrepancy_type=DiscrepancyType.STRIPE_SUCCEEDED_LOCAL_PROCESSING,
                entity_type="payment_order",
                entity_id=reconciliation_payment_order.id,
                stripe_id=reconciliation_payment_order.stripe_payment_intent_id,
                local_state=PaymentOrderState.PROCESSING,
                stripe_state="succeeded",
            )

            # Call the actual healing method directly
            result = ReconciliationService._heal_payment_succeeded(
                discrepancy, run_id=None
            )

            assert result.resolution == DiscrepancyResolution.AUTO_HEALED
            assert "CAPTURED" in result.action_taken

            # Verify state changed
            fresh_order = PaymentOrder.objects.get(id=reconciliation_payment_order.id)
            assert fresh_order.state == PaymentOrderState.CAPTURED
        finally:
            ReconciliationService.set_stripe_adapter(None)

    def test_heal_payout_complete_transitions_to_paid(self, db, mock_stripe_adapter):
        """Auto-heal transitions payout from PROCESSING to PAID."""
        from authentication.models import User

        # Create user directly - this triggers signal to create Profile
        user = User.objects.create_user(
            email=f"heal_complete_{uuid.uuid4().hex[:8]}@example.com",
            password="testpass123",
        )
        profile = user.profile

        # Create connected account with the profile
        account = ConnectedAccount.objects.create(
            profile=profile,
            stripe_account_id=f"acct_heal_{uuid.uuid4().hex[:8]}",
            onboarding_status=OnboardingStatus.COMPLETE,
            payouts_enabled=True,
            charges_enabled=True,
        )

        order = PaymentOrderFactory()
        order.stripe_payment_intent_id = f"pi_test_{uuid.uuid4().hex[:16]}"
        order.submit()
        order.process()
        order.capture()
        order.save()

        payout = Payout.objects.create(
            payment_order=order,
            connected_account=account,
            amount_cents=9000,
            currency="usd",
        )
        payout.stripe_transfer_id = f"tr_test_{uuid.uuid4().hex[:16]}"
        payout.schedule(scheduled_for=timezone.now())
        payout.process()
        payout.save()

        mock_stripe_adapter.retrieve_transfer.return_value = create_transfer_result(
            payout.stripe_transfer_id,
            "paid",
        )

        ReconciliationService.set_stripe_adapter(mock_stripe_adapter)

        try:
            discrepancy = Discrepancy(
                discrepancy_type=DiscrepancyType.STRIPE_TRANSFER_PAID_LOCAL_PROCESSING,
                entity_type="payout",
                entity_id=payout.id,
                stripe_id=payout.stripe_transfer_id,
                local_state=PayoutState.PROCESSING,
                stripe_state="paid",
            )

            result = ReconciliationService._heal_payout_complete(
                discrepancy, run_id=None
            )

            assert result.resolution == DiscrepancyResolution.AUTO_HEALED
            assert "PAID" in result.action_taken

            # Verify state changed
            fresh_payout = Payout.objects.get(id=payout.id)
            assert fresh_payout.state == PayoutState.PAID
        finally:
            ReconciliationService.set_stripe_adapter(None)

    def test_heal_payout_backfills_transfer_id(self, db, mock_stripe_adapter):
        """Auto-heal backfills missing stripe_transfer_id."""
        from authentication.models import User

        # Create user directly - this triggers signal to create Profile
        user = User.objects.create_user(
            email=f"backfill_{uuid.uuid4().hex[:8]}@example.com",
            password="testpass123",
        )
        profile = user.profile

        # Create connected account with the profile
        account = ConnectedAccount.objects.create(
            profile=profile,
            stripe_account_id=f"acct_backfill_{uuid.uuid4().hex[:8]}",
            onboarding_status=OnboardingStatus.COMPLETE,
            payouts_enabled=True,
            charges_enabled=True,
        )

        order = PaymentOrderFactory()
        order.submit()
        order.process()
        order.capture()
        order.save()

        payout = Payout.objects.create(
            payment_order=order,
            connected_account=account,
            amount_cents=9000,
            currency="usd",
        )
        payout.schedule(scheduled_for=timezone.now())
        payout.process()
        payout.save()

        assert payout.stripe_transfer_id is None

        transfer_id = f"tr_backfill_{uuid.uuid4().hex[:8]}"

        discrepancy = Discrepancy(
            discrepancy_type=DiscrepancyType.STRIPE_TRANSFER_EXISTS_LOCAL_PROCESSING_NO_ID,
            entity_type="payout",
            entity_id=payout.id,
            stripe_id=transfer_id,
            local_state=PayoutState.PROCESSING,
            stripe_state="paid",
            details={
                "transfer_id": transfer_id,
                "transfer_status": "paid",
            },
        )

        result = ReconciliationService._heal_payout_backfill_transfer_id(
            discrepancy, run_id=None
        )

        assert result.resolution == DiscrepancyResolution.AUTO_HEALED
        assert transfer_id in result.action_taken

        # Verify transfer_id was backfilled
        fresh_payout = Payout.objects.get(id=payout.id)
        assert fresh_payout.stripe_transfer_id == transfer_id
        # Should also transition to PAID since transfer_status was "paid"
        assert fresh_payout.state == PayoutState.PAID

    def test_flag_for_review_returns_correct_resolution(self):
        """Flagging for review returns FLAGGED_FOR_REVIEW resolution."""
        discrepancy = Discrepancy(
            discrepancy_type=DiscrepancyType.STRIPE_CANCELED_LOCAL_ACTIVE,
            entity_type="payment_order",
            entity_id=uuid.uuid4(),
            stripe_id="pi_test",
            local_state=PaymentOrderState.PROCESSING,
            stripe_state="canceled",
        )

        result = ReconciliationService._flag_for_review(discrepancy, run_id=None)

        assert result.resolution == DiscrepancyResolution.FLAGGED_FOR_REVIEW
        assert result.action_taken == "Flagged for manual review"


# =============================================================================
# Test: Full Reconciliation Run
# =============================================================================


@pytest.mark.django_db
class TestFullReconciliationRun:
    """Test full reconciliation run workflow."""

    def test_run_creates_reconciliation_run_record(self, mock_stripe_adapter):
        """Running reconciliation creates a ReconciliationRun record."""
        mock_stripe_adapter.retrieve_payment_intent.return_value = (
            create_payment_intent_result("processing")
        )

        ReconciliationService.set_stripe_adapter(mock_stripe_adapter)

        try:
            # Mock the distributed lock
            with patch("payments.services.reconciliation_service.DistributedLock"):
                result = ReconciliationService.run_reconciliation(
                    lookback_hours=24,
                    stuck_threshold_hours=2,
                    max_records=100,
                )

            assert result.success
            run_result = result.data

            # Verify run record was created
            run = ReconciliationRun.objects.get(id=run_result.run_id)
            assert run.status == ReconciliationRunStatus.COMPLETED
            assert run.lookback_hours == 24
            assert run.stuck_threshold_hours == 2
            assert run.completed_at is not None
        finally:
            ReconciliationService.set_stripe_adapter(None)

    def test_run_records_discrepancies(
        self, reconciliation_payment_order, mock_stripe_adapter
    ):
        """Reconciliation run records discrepancies to database."""
        # Setup: Stripe says succeeded
        mock_stripe_adapter.retrieve_payment_intent.return_value = (
            create_payment_intent_result("succeeded")
        )

        ReconciliationService.set_stripe_adapter(mock_stripe_adapter)

        try:
            with patch("payments.services.reconciliation_service.DistributedLock"):
                result = ReconciliationService.run_reconciliation(
                    lookback_hours=24,
                    stuck_threshold_hours=2,
                )

            assert result.success
            run_result = result.data

            # Verify discrepancy was recorded
            discrepancies = ReconciliationDiscrepancy.objects.filter(
                run_id=run_result.run_id
            )
            assert discrepancies.count() >= 1

            discrepancy = discrepancies.first()
            assert discrepancy.entity_type == "payment_order"
            assert discrepancy.resolution == DiscrepancyResolution.AUTO_HEALED
        finally:
            ReconciliationService.set_stripe_adapter(None)


# =============================================================================
# Test: Single Entity Reconciliation
# =============================================================================


@pytest.mark.django_db
class TestSingleEntityReconciliation:
    """Test on-demand single entity reconciliation."""

    def test_reconcile_single_payment_order_not_found(self, db):
        """Reconciling non-existent payment order returns failure."""
        fake_id = uuid.uuid4()

        result = ReconciliationService.reconcile_payment_order(fake_id)

        assert not result.success
        assert "not found" in result.error.lower()

    def test_reconcile_single_payout_not_found(self, db):
        """Reconciling non-existent payout returns failure."""
        fake_id = uuid.uuid4()

        result = ReconciliationService.reconcile_payout(fake_id)

        assert not result.success
        assert "not found" in result.error.lower()

    def test_reconcile_single_payment_order_no_discrepancy(
        self, reconciliation_payment_order, mock_stripe_adapter
    ):
        """Single payment order reconciliation with no discrepancy."""
        # Stripe also shows processing (states match)
        mock_stripe_adapter.retrieve_payment_intent.return_value = (
            create_payment_intent_result("processing")
        )

        ReconciliationService.set_stripe_adapter(mock_stripe_adapter)

        try:
            result = ReconciliationService.reconcile_payment_order(
                reconciliation_payment_order.id
            )

            assert result.success
            assert result.data is None  # No discrepancy found
        finally:
            ReconciliationService.set_stripe_adapter(None)


# =============================================================================
# Test: Idempotency and Race Conditions
# =============================================================================


@pytest.mark.django_db
class TestIdempotencyAndRaceConditions:
    """Test handling of concurrent operations and idempotency."""

    def test_heal_skips_if_state_already_changed(
        self, reconciliation_payment_order, mock_stripe_adapter
    ):
        """Healing skips if state already transitioned (e.g., by webhook)."""
        # First, simulate webhook already fixed the state
        reconciliation_payment_order.capture()
        reconciliation_payment_order.save()

        # Now try to heal (should detect state changed)
        discrepancy = Discrepancy(
            discrepancy_type=DiscrepancyType.STRIPE_SUCCEEDED_LOCAL_PROCESSING,
            entity_type="payment_order",
            entity_id=reconciliation_payment_order.id,
            stripe_id=reconciliation_payment_order.stripe_payment_intent_id,
            local_state=PaymentOrderState.PROCESSING,  # Old state when detected
            stripe_state="succeeded",
        )

        result = ReconciliationService._heal_payment_succeeded(discrepancy, run_id=None)

        # Should report as healed but note state already changed
        assert result.resolution == DiscrepancyResolution.AUTO_HEALED
        assert "already transitioned" in result.action_taken.lower()


__all__ = [
    "TestPaymentOrderDiscrepancyDetection",
    "TestPayoutDiscrepancyDetection",
    "TestHealingLogic",
    "TestFullReconciliationRun",
    "TestSingleEntityReconciliation",
    "TestIdempotencyAndRaceConditions",
]
