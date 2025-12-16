"""
Tests for storage quota tracking with atomic operations.

This module tests:
- Atomic add/subtract storage operations using F() expressions
- Concurrent operation handling
- Negative value prevention
- Edge cases (exactly at quota, one byte over)

TDD: These tests were written before the implementation.
"""

import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
from django.db import connection

from authentication.tests.factories import UserFactory


def create_unique_user(**kwargs):
    """Create a user with a unique email to avoid factory sequence collisions."""
    unique_email = f"test_{uuid.uuid4().hex[:8]}@example.com"
    return UserFactory(email=unique_email, **kwargs)


# =============================================================================
# Atomic Storage Operations Tests
# =============================================================================


@pytest.mark.django_db
class TestAtomicStorageOperations:
    """Tests for atomic add/subtract storage operations using F() expressions."""

    def test_add_storage_usage_increments_correctly(self):
        """
        add_storage_usage should increment total_storage_bytes.

        Why it matters: Basic functionality verification.
        """
        user = create_unique_user(email_verified=True)
        user.profile.total_storage_bytes = 0
        user.profile.save()

        user.profile.add_storage_usage(1000)

        user.profile.refresh_from_db()
        assert user.profile.total_storage_bytes == 1000

    def test_add_storage_usage_multiple_calls(self):
        """
        Multiple sequential add_storage_usage calls should accumulate.

        Why it matters: Verify correct summation.
        """
        user = create_unique_user(email_verified=True)
        user.profile.total_storage_bytes = 0
        user.profile.save()

        user.profile.add_storage_usage(1000)
        user.profile.add_storage_usage(2000)
        user.profile.add_storage_usage(500)

        user.profile.refresh_from_db()
        assert user.profile.total_storage_bytes == 3500

    def test_subtract_storage_usage_decrements_correctly(self):
        """
        subtract_storage_usage should decrement total_storage_bytes.

        Why it matters: Basic functionality verification.
        """
        user = create_unique_user(email_verified=True)
        user.profile.total_storage_bytes = 5000
        user.profile.save()

        user.profile.subtract_storage_usage(2000)

        user.profile.refresh_from_db()
        assert user.profile.total_storage_bytes == 3000

    def test_subtract_storage_usage_prevents_negative(self):
        """
        Subtracting more than current usage should result in 0, not negative.

        Why it matters: Storage usage cannot be negative.
        """
        user = create_unique_user(email_verified=True)
        user.profile.total_storage_bytes = 1000
        user.profile.save()

        user.profile.subtract_storage_usage(5000)  # More than available

        user.profile.refresh_from_db()
        assert user.profile.total_storage_bytes == 0

    def test_subtract_to_exactly_zero(self):
        """
        Subtracting exact amount should result in exactly 0.

        Why it matters: Edge case verification.
        """
        user = create_unique_user(email_verified=True)
        user.profile.total_storage_bytes = 1000
        user.profile.save()

        user.profile.subtract_storage_usage(1000)

        user.profile.refresh_from_db()
        assert user.profile.total_storage_bytes == 0

    def test_add_storage_refreshes_instance(self):
        """
        add_storage_usage should refresh the instance with updated value.

        Why it matters: Caller should see updated value immediately.
        """
        user = create_unique_user(email_verified=True)
        user.profile.total_storage_bytes = 0
        user.profile.save()

        user.profile.add_storage_usage(1000)

        # Instance should have updated value without explicit refresh
        assert user.profile.total_storage_bytes == 1000

    def test_subtract_storage_refreshes_instance(self):
        """
        subtract_storage_usage should refresh the instance with updated value.

        Why it matters: Caller should see updated value immediately.
        """
        user = create_unique_user(email_verified=True)
        user.profile.total_storage_bytes = 5000
        user.profile.save()

        user.profile.subtract_storage_usage(2000)

        # Instance should have updated value without explicit refresh
        assert user.profile.total_storage_bytes == 3000


@pytest.mark.django_db(transaction=True)
class TestAtomicStorageOperationsConcurrent:
    """
    Tests for concurrent storage operations requiring real transactions.

    These tests verify that F() expressions provide true atomicity
    under concurrent access. They require transaction=True for proper
    concurrency testing.
    """

    def test_add_storage_usage_is_atomic_concurrent(self):
        """
        Concurrent add operations should all be applied without lost updates.

        Why it matters: Prevents race conditions from concurrent uploads.
        """
        user = create_unique_user(email_verified=True)
        user.profile.total_storage_bytes = 0
        user.profile.save()
        profile_pk = user.profile.pk

        # Simulate 10 concurrent uploads of 1000 bytes each
        def add_bytes():
            connection.close()  # Force new connection for thread
            from authentication.models import Profile

            profile = Profile.objects.get(pk=profile_pk)
            profile.add_storage_usage(1000)
            return True

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(add_bytes) for _ in range(10)]
            for future in as_completed(futures):
                future.result()

        user.profile.refresh_from_db()
        assert user.profile.total_storage_bytes == 10000

    def test_subtract_storage_usage_is_atomic_concurrent(self):
        """
        Concurrent subtract operations should all be applied correctly.

        Why it matters: Prevents race conditions from concurrent deletes.
        """
        user = create_unique_user(email_verified=True)
        user.profile.total_storage_bytes = 10000
        user.profile.save()
        profile_pk = user.profile.pk

        # Simulate 5 concurrent deletes of 1000 bytes each
        def subtract_bytes():
            connection.close()  # Force new connection for thread
            from authentication.models import Profile

            profile = Profile.objects.get(pk=profile_pk)
            profile.subtract_storage_usage(1000)
            return True

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(subtract_bytes) for _ in range(5)]
            for future in as_completed(futures):
                future.result()

        user.profile.refresh_from_db()
        assert user.profile.total_storage_bytes == 5000

    def test_concurrent_subtract_does_not_go_negative(self):
        """
        Multiple concurrent subtracts that exceed total should floor at 0.

        Why it matters: Even with race conditions, we shouldn't go negative.
        """
        user = create_unique_user(email_verified=True)
        user.profile.total_storage_bytes = 3000
        user.profile.save()
        profile_pk = user.profile.pk

        # Simulate 10 concurrent deletes of 1000 bytes each (total 10000 > 3000)
        def subtract_bytes():
            connection.close()  # Force new connection for thread
            from authentication.models import Profile

            profile = Profile.objects.get(pk=profile_pk)
            profile.subtract_storage_usage(1000)
            return True

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(subtract_bytes) for _ in range(10)]
            for future in as_completed(futures):
                future.result()

        user.profile.refresh_from_db()
        assert user.profile.total_storage_bytes >= 0


# =============================================================================
# can_upload() Tests
# =============================================================================


@pytest.mark.django_db
class TestCanUpload:
    """Tests for can_upload() quota check method."""

    def test_can_upload_returns_true_when_under_quota(self, db):
        """
        can_upload should return True when file fits within quota.

        Why it matters: Allow uploads when there's room.
        """
        user = create_unique_user(email_verified=True)
        user.profile.total_storage_bytes = 1000
        user.profile.storage_quota_bytes = 10000
        user.profile.save()

        assert user.profile.can_upload(5000) is True

    def test_can_upload_returns_true_exactly_at_quota(self, db):
        """
        can_upload should return True when file fills exactly to quota.

        Why it matters: Edge case - should allow upload that exactly fills quota.
        """
        user = create_unique_user(email_verified=True)
        user.profile.total_storage_bytes = 5000
        user.profile.storage_quota_bytes = 10000
        user.profile.save()

        # 5000 + 5000 = 10000 (exactly at quota)
        assert user.profile.can_upload(5000) is True

    def test_can_upload_returns_false_one_byte_over(self, db):
        """
        can_upload should return False when file exceeds quota by 1 byte.

        Why it matters: Strict quota enforcement.
        """
        user = create_unique_user(email_verified=True)
        user.profile.total_storage_bytes = 5000
        user.profile.storage_quota_bytes = 10000
        user.profile.save()

        # 5000 + 5001 = 10001 (1 byte over)
        assert user.profile.can_upload(5001) is False

    def test_can_upload_returns_false_when_at_quota(self, db):
        """
        can_upload should return False when already at quota.

        Why it matters: Cannot upload anything when quota is full.
        """
        user = create_unique_user(email_verified=True)
        user.profile.total_storage_bytes = 10000
        user.profile.storage_quota_bytes = 10000
        user.profile.save()

        assert user.profile.can_upload(1) is False

    def test_can_upload_returns_false_when_over_quota(self, db):
        """
        can_upload should return False when already over quota.

        Why it matters: Handle corrupted state gracefully.
        """
        user = create_unique_user(email_verified=True)
        user.profile.total_storage_bytes = 15000  # Over quota
        user.profile.storage_quota_bytes = 10000
        user.profile.save()

        assert user.profile.can_upload(1) is False

    def test_can_upload_zero_size_file(self, db):
        """
        can_upload should return True for zero-size file.

        Why it matters: Edge case - empty files don't consume quota.
        """
        user = create_unique_user(email_verified=True)
        user.profile.total_storage_bytes = 10000
        user.profile.storage_quota_bytes = 10000
        user.profile.save()

        assert user.profile.can_upload(0) is True

    def test_can_upload_with_zero_quota(self, db):
        """
        can_upload should return False with zero quota (disabled uploads).

        Why it matters: Handle disabled accounts gracefully.
        """
        user = create_unique_user(email_verified=True)
        user.profile.total_storage_bytes = 0
        user.profile.storage_quota_bytes = 0
        user.profile.save()

        assert user.profile.can_upload(1) is False


# =============================================================================
# Storage Property Tests
# =============================================================================


@pytest.mark.django_db
class TestStorageProperties:
    """Tests for storage-related property methods."""

    def test_storage_used_percent_calculation(self, db):
        """
        storage_used_percent should calculate correct percentage.

        Why it matters: UI displays percentage to users.
        """
        user = create_unique_user(email_verified=True)
        user.profile.total_storage_bytes = 2500
        user.profile.storage_quota_bytes = 10000
        user.profile.save()

        assert user.profile.storage_used_percent == 25.0

    def test_storage_used_percent_at_100(self, db):
        """
        storage_used_percent should return 100.0 when at quota.

        Why it matters: Boundary condition.
        """
        user = create_unique_user(email_verified=True)
        user.profile.total_storage_bytes = 10000
        user.profile.storage_quota_bytes = 10000
        user.profile.save()

        assert user.profile.storage_used_percent == 100.0

    def test_storage_used_percent_over_100(self, db):
        """
        storage_used_percent can exceed 100 if over quota (data integrity issue).

        Why it matters: Handle corrupted state without crashing.
        """
        user = create_unique_user(email_verified=True)
        user.profile.total_storage_bytes = 15000
        user.profile.storage_quota_bytes = 10000
        user.profile.save()

        assert user.profile.storage_used_percent == 150.0

    def test_storage_used_percent_with_zero_quota(self, db):
        """
        storage_used_percent should return 100.0 if quota is zero.

        Why it matters: Avoid division by zero.
        """
        user = create_unique_user(email_verified=True)
        user.profile.total_storage_bytes = 100
        user.profile.storage_quota_bytes = 0
        user.profile.save()

        assert user.profile.storage_used_percent == 100.0

    def test_storage_remaining_bytes_calculation(self, db):
        """
        storage_remaining_bytes should calculate correct remaining space.

        Why it matters: Used to determine uploadable file sizes.
        """
        user = create_unique_user(email_verified=True)
        user.profile.total_storage_bytes = 3000
        user.profile.storage_quota_bytes = 10000
        user.profile.save()

        assert user.profile.storage_remaining_bytes == 7000

    def test_storage_remaining_bytes_at_zero(self, db):
        """
        storage_remaining_bytes should be 0 when at quota.

        Why it matters: Boundary condition.
        """
        user = create_unique_user(email_verified=True)
        user.profile.total_storage_bytes = 10000
        user.profile.storage_quota_bytes = 10000
        user.profile.save()

        assert user.profile.storage_remaining_bytes == 0

    def test_storage_remaining_bytes_never_negative(self, db):
        """
        storage_remaining_bytes should never be negative (floor at 0).

        Why it matters: Handle corrupted state gracefully.
        """
        user = create_unique_user(email_verified=True)
        user.profile.total_storage_bytes = 15000
        user.profile.storage_quota_bytes = 10000
        user.profile.save()

        assert user.profile.storage_remaining_bytes == 0

    def test_storage_used_mb_calculation(self, db):
        """
        storage_used_mb should convert bytes to megabytes.

        Why it matters: Human-readable display.
        """
        user = create_unique_user(email_verified=True)
        user.profile.total_storage_bytes = 5 * 1024 * 1024  # 5MB
        user.profile.save()

        assert user.profile.storage_used_mb == 5.0

    def test_storage_quota_mb_calculation(self, db):
        """
        storage_quota_mb should convert quota bytes to megabytes.

        Why it matters: Human-readable display.
        """
        user = create_unique_user(email_verified=True)
        user.profile.storage_quota_bytes = 25 * 1024 * 1024 * 1024  # 25GB
        user.profile.save()

        assert user.profile.storage_quota_mb == 25 * 1024  # 25GB in MB


# =============================================================================
# Default Quota Tests
# =============================================================================


@pytest.mark.django_db
class TestDefaultQuota:
    """Tests for default storage quota value."""

    def test_new_profile_has_25gb_quota(self, db):
        """
        New profiles should have 25GB default quota.

        Why it matters: Spec requirement for default quota.
        """
        user = create_unique_user(email_verified=True)

        expected_quota = 25 * 1024 * 1024 * 1024  # 25GB
        assert user.profile.storage_quota_bytes == expected_quota

    def test_new_profile_has_zero_usage(self, db):
        """
        New profiles should start with zero storage usage.

        Why it matters: New users haven't uploaded anything yet.
        """
        user = create_unique_user(email_verified=True)

        assert user.profile.total_storage_bytes == 0
