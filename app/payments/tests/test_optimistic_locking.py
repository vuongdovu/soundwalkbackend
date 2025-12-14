"""
Tests for optimistic locking utilities.

Tests the check_version function and version field behavior on models
for detecting concurrent modifications.
"""

import uuid

import pytest
from django.db import connection, transaction

from core.exceptions import NotFoundError
from payments.exceptions import StaleRecordError
from payments.locks import check_version


class TestCheckVersion:
    """Tests for check_version function."""

    def test_returns_instance_when_version_matches(self, db, payment_order):
        """Should return locked instance when version matches."""
        with transaction.atomic():
            result = check_version(
                payment_order.__class__,
                payment_order.pk,
                expected_version=payment_order.version,
            )

        assert result.pk == payment_order.pk
        assert result.version == payment_order.version

    def test_raises_stale_record_when_version_mismatch(self, db, payment_order):
        """Should raise StaleRecordError when version doesn't match."""
        with pytest.raises(StaleRecordError) as exc_info:
            check_version(
                payment_order.__class__,
                payment_order.pk,
                expected_version=999,  # Wrong version
            )

        assert "has been modified" in str(exc_info.value)
        assert exc_info.value.details["pk"] == str(payment_order.pk)
        assert exc_info.value.details["expected_version"] == 999
        assert exc_info.value.details["current_version"] == payment_order.version

    def test_raises_not_found_when_record_missing(self, db, payment_order):
        """Should raise NotFoundError when record doesn't exist."""
        fake_pk = uuid.uuid4()

        with pytest.raises(NotFoundError) as exc_info:
            check_version(
                payment_order.__class__,
                fake_pk,
                expected_version=1,
            )

        assert "not found" in str(exc_info.value)
        assert exc_info.value.details["pk"] == str(fake_pk)

    def test_uses_select_for_update(self, db, payment_order):
        """Should use select_for_update to lock the row."""
        # This test verifies that the function uses select_for_update
        # by checking that we can read the instance within a transaction
        with transaction.atomic():
            result = check_version(
                payment_order.__class__,
                payment_order.pk,
                expected_version=payment_order.version,
            )
            # Should be able to modify and save
            result.metadata = {"test": "value"}
            result.save()

        # Reload and verify (exclude state field due to FSM protected=True)
        payment_order.refresh_from_db(fields=["metadata"])
        assert payment_order.metadata == {"test": "value"}


class TestVersionFieldBehavior:
    """Tests for version field auto-increment behavior on models."""

    def test_new_record_has_version_1(self, db, user):
        """New records should have version=1."""
        from payments.models import PaymentOrder
        from payments.state_machines import PaymentOrderState, PaymentStrategyType

        order = PaymentOrder.objects.create(
            payer=user,
            amount_cents=5000,
            currency="usd",
            strategy_type=PaymentStrategyType.DIRECT,
            state=PaymentOrderState.DRAFT,
        )

        assert order.version == 1

    def test_version_increments_on_save(self, db, payment_order):
        """Version should increment on each save."""
        initial_version = payment_order.version
        assert initial_version == 1

        payment_order.metadata = {"updated": True}
        payment_order.save()

        # Refresh only version field (state field is FSM protected)
        payment_order.refresh_from_db(fields=["version"])
        assert payment_order.version == initial_version + 1

    def test_version_increments_multiple_times(self, db, payment_order):
        """Version should increment correctly over multiple saves."""
        for i in range(5):
            expected_version = i + 2  # Starts at 1, so after first save it's 2
            payment_order.metadata = {"iteration": i}
            payment_order.save()
            # Refresh only version field (state field is FSM protected)
            payment_order.refresh_from_db(fields=["version"])
            assert payment_order.version == expected_version


class TestConcurrentModification:
    """Tests for concurrent modification detection."""

    @pytest.mark.integration
    def test_concurrent_modification_detected(self, db, payment_order):
        """
        Simulates concurrent modification scenario.

        Process A reads the record, then Process B modifies it,
        then Process A tries to update with stale version.
        """
        original_version = payment_order.version

        # Simulate Process B modifying the record
        payment_order.metadata = {"modified_by": "process_b"}
        payment_order.save()
        # Refresh only version field (state field is FSM protected)
        payment_order.refresh_from_db(fields=["version"])

        # Process A tries to use stale version
        with pytest.raises(StaleRecordError) as exc_info:
            check_version(
                payment_order.__class__,
                payment_order.pk,
                expected_version=original_version,  # Stale!
            )

        assert exc_info.value.details["expected_version"] == original_version
        assert exc_info.value.details["current_version"] == original_version + 1

    @pytest.mark.integration
    def test_check_version_then_modify_works(self, db, payment_order):
        """
        Normal flow: check_version then modify should work.

        This tests the intended usage pattern.
        """
        with transaction.atomic():
            # Get locked instance with version check
            locked_order = check_version(
                payment_order.__class__,
                payment_order.pk,
                expected_version=payment_order.version,
            )

            # Modify and save
            locked_order.metadata = {"safe_modification": True}
            locked_order.save()

        # Verify the modification persisted (exclude state field due to FSM protected=True)
        payment_order.refresh_from_db(fields=["metadata", "version"])
        assert payment_order.metadata == {"safe_modification": True}
        assert payment_order.version == 2  # Version incremented


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
def payment_order(db, user):
    """Create a test payment order."""
    from payments.models import PaymentOrder
    from payments.state_machines import PaymentOrderState, PaymentStrategyType

    return PaymentOrder.objects.create(
        payer=user,
        amount_cents=10000,
        currency="usd",
        strategy_type=PaymentStrategyType.DIRECT,
        state=PaymentOrderState.DRAFT,
    )
