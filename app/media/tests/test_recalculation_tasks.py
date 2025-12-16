"""
Tests for storage quota recalculation Celery tasks.

This module tests:
- recalculate_user_storage_quota: Single user recalculation
- recalculate_all_storage_quotas: Batch recalculation for all users

TDD: These tests are written before implementing the tasks.
"""

import uuid

import pytest

from authentication.tests.factories import UserFactory
from media.models import MediaFile


# =============================================================================
# Test Fixtures
# =============================================================================


def create_unique_user(**kwargs):
    """Create a user with a unique email to avoid factory sequence collisions."""
    unique_email = f"test_{uuid.uuid4().hex[:8]}@example.com"
    return UserFactory(email=unique_email, **kwargs)


@pytest.fixture
def user_with_files(db):
    """Create a user with multiple files and track total size."""
    user = create_unique_user(email_verified=True)
    user.profile.storage_quota_bytes = 1024 * 1024 * 1024  # 1GB

    # Create multiple files with known sizes
    file_sizes = [1000, 2000, 3000, 5000]  # Total: 11000 bytes

    for i, size in enumerate(file_sizes):
        MediaFile.objects.create(
            file=f"test_files/file_{i}.jpg",
            original_filename=f"file_{i}.jpg",
            media_type=MediaFile.MediaType.IMAGE,
            mime_type="image/jpeg",
            file_size=size,
            uploader=user,
            visibility=MediaFile.Visibility.PRIVATE,
            scan_status=MediaFile.ScanStatus.CLEAN,
            processing_status=MediaFile.ProcessingStatus.READY,
        )

    # Set initial quota to correct value
    user.profile.total_storage_bytes = sum(file_sizes)
    user.profile.save()

    return user


@pytest.fixture
def user_with_drifted_quota(db):
    """Create a user whose stored quota doesn't match actual files."""
    user = create_unique_user(email_verified=True)
    user.profile.storage_quota_bytes = 1024 * 1024 * 1024  # 1GB

    # Create files totaling 10000 bytes
    for i in range(5):
        MediaFile.objects.create(
            file=f"test_files/drift_{i}.jpg",
            original_filename=f"drift_{i}.jpg",
            media_type=MediaFile.MediaType.IMAGE,
            mime_type="image/jpeg",
            file_size=2000,
            uploader=user,
            visibility=MediaFile.Visibility.PRIVATE,
            scan_status=MediaFile.ScanStatus.CLEAN,
            processing_status=MediaFile.ProcessingStatus.READY,
        )

    # Set quota to wrong value (drifted)
    user.profile.total_storage_bytes = 50000  # Way off from actual 10000
    user.profile.save()

    return user


@pytest.fixture
def user_with_deleted_files(db):
    """Create a user with some soft-deleted files."""
    user = create_unique_user(email_verified=True)
    user.profile.storage_quota_bytes = 1024 * 1024 * 1024

    # Create 3 active files (total: 6000)
    for i in range(3):
        MediaFile.objects.create(
            file=f"test_files/active_{i}.jpg",
            original_filename=f"active_{i}.jpg",
            media_type=MediaFile.MediaType.IMAGE,
            mime_type="image/jpeg",
            file_size=2000,
            uploader=user,
            visibility=MediaFile.Visibility.PRIVATE,
            scan_status=MediaFile.ScanStatus.CLEAN,
            processing_status=MediaFile.ProcessingStatus.READY,
        )

    # Create 2 deleted files (should not count)
    for i in range(2):
        deleted_file = MediaFile.objects.create(
            file=f"test_files/deleted_{i}.jpg",
            original_filename=f"deleted_{i}.jpg",
            media_type=MediaFile.MediaType.IMAGE,
            mime_type="image/jpeg",
            file_size=5000,
            uploader=user,
            visibility=MediaFile.Visibility.PRIVATE,
            scan_status=MediaFile.ScanStatus.CLEAN,
            processing_status=MediaFile.ProcessingStatus.READY,
        )
        deleted_file.soft_delete()  # This calls subtract_storage_usage

    # After deletes, quota should be correct (6000), but let's drift it
    user.profile.total_storage_bytes = 16000  # Includes deleted files wrongly
    user.profile.save()

    return user


# =============================================================================
# recalculate_user_storage_quota Tests
# =============================================================================


@pytest.mark.django_db
class TestRecalculateUserStorageQuota:
    """Tests for recalculate_user_storage_quota task."""

    def test_recalculation_returns_correct_values(self, user_with_files):
        """
        Task should return old, new, and difference values.

        Why it matters: Allows monitoring of drift.
        """
        from media.tasks import recalculate_user_storage_quota

        result = recalculate_user_storage_quota(str(user_with_files.id))

        assert "old_bytes" in result
        assert "new_bytes" in result
        assert "difference" in result

    def test_recalculation_corrects_positive_drift(self, user_with_drifted_quota):
        """
        Task should correct quota when stored value is too high.

        Why it matters: Prevents users being blocked when they have space.
        """
        from media.tasks import recalculate_user_storage_quota

        old_quota = user_with_drifted_quota.profile.total_storage_bytes
        assert old_quota == 50000  # Drifted value

        result = recalculate_user_storage_quota(str(user_with_drifted_quota.id))

        user_with_drifted_quota.profile.refresh_from_db()
        assert user_with_drifted_quota.profile.total_storage_bytes == 10000  # Actual
        assert result["old_bytes"] == 50000
        assert result["new_bytes"] == 10000
        assert result["difference"] == -40000

    def test_recalculation_corrects_negative_drift(self, user_with_files):
        """
        Task should correct quota when stored value is too low.

        Why it matters: Prevents users from exceeding actual quota.
        """
        from media.tasks import recalculate_user_storage_quota

        # Set quota to artificially low value
        user_with_files.profile.total_storage_bytes = 1000  # Should be 11000
        user_with_files.profile.save()

        result = recalculate_user_storage_quota(str(user_with_files.id))

        user_with_files.profile.refresh_from_db()
        assert user_with_files.profile.total_storage_bytes == 11000  # Corrected
        assert result["old_bytes"] == 1000
        assert result["new_bytes"] == 11000
        assert result["difference"] == 10000

    def test_recalculation_excludes_deleted_files(self, user_with_deleted_files):
        """
        Task should only count non-deleted files.

        Why it matters: Deleted files shouldn't count against quota.
        """
        from media.tasks import recalculate_user_storage_quota

        result = recalculate_user_storage_quota(str(user_with_deleted_files.id))

        user_with_deleted_files.profile.refresh_from_db()
        # Only active files count: 3 * 2000 = 6000
        assert user_with_deleted_files.profile.total_storage_bytes == 6000
        assert result["new_bytes"] == 6000

    def test_recalculation_handles_user_with_no_files(self, db):
        """
        Task should set quota to 0 for users with no files.

        Why it matters: New users or users who deleted everything.
        """
        from media.tasks import recalculate_user_storage_quota

        user = create_unique_user(email_verified=True)
        user.profile.total_storage_bytes = 5000  # Wrong value
        user.profile.save()

        result = recalculate_user_storage_quota(str(user.id))

        user.profile.refresh_from_db()
        assert user.profile.total_storage_bytes == 0
        assert result["new_bytes"] == 0

    def test_recalculation_handles_nonexistent_user(self):
        """
        Task should handle nonexistent user gracefully.

        Why it matters: Defensive coding against stale task queues.
        """
        from media.tasks import recalculate_user_storage_quota

        fake_id = str(uuid.uuid4())
        result = recalculate_user_storage_quota(fake_id)

        assert result["status"] == "not_found"

    def test_recalculation_noop_when_correct(self, user_with_files):
        """
        Task should report zero difference when quota is correct.

        Why it matters: Verify correct state isn't modified.
        """
        from media.tasks import recalculate_user_storage_quota

        # Quota is already correct (11000)
        result = recalculate_user_storage_quota(str(user_with_files.id))

        assert result["difference"] == 0
        user_with_files.profile.refresh_from_db()
        assert user_with_files.profile.total_storage_bytes == 11000


# =============================================================================
# recalculate_all_storage_quotas Tests
# =============================================================================


@pytest.mark.django_db
class TestRecalculateAllStorageQuotas:
    """Tests for recalculate_all_storage_quotas batch task."""

    def test_batch_recalculation_processes_all_users(self, db):
        """
        Task should process all active users.

        Why it matters: Weekly maintenance task for quota integrity.
        """
        from media.tasks import recalculate_all_storage_quotas

        # Create multiple users with drifted quotas
        for i in range(3):
            user = create_unique_user(email_verified=True)
            user.profile.total_storage_bytes = 99999  # Wrong
            user.profile.save()

        result = recalculate_all_storage_quotas()

        assert result["users_processed"] >= 3
        assert "corrections_made" in result
        assert "total_drift_bytes" in result

    def test_batch_recalculation_reports_corrections(self, user_with_drifted_quota):
        """
        Task should count how many users needed correction.

        Why it matters: Monitoring for systematic drift issues.
        """
        from media.tasks import recalculate_all_storage_quotas

        result = recalculate_all_storage_quotas()

        assert result["corrections_made"] >= 1

    def test_batch_recalculation_skips_inactive_users(self, db):
        """
        Task should skip deactivated users.

        Why it matters: Don't waste resources on inactive accounts.
        """
        from media.tasks import recalculate_all_storage_quotas

        # Create inactive user with drifted quota
        inactive_user = create_unique_user(email_verified=True, is_active=False)
        inactive_user.profile.total_storage_bytes = 99999
        inactive_user.profile.save()

        recalculate_all_storage_quotas()

        # Inactive user should still have wrong quota (not processed)
        inactive_user.profile.refresh_from_db()
        assert inactive_user.profile.total_storage_bytes == 99999

    def test_batch_recalculation_returns_total_drift(self, db):
        """
        Task should report total drift across all users.

        Why it matters: Detect systematic issues in quota tracking.
        """
        from media.tasks import recalculate_all_storage_quotas

        # Create users with known drift
        for i in range(2):
            user = create_unique_user(email_verified=True)
            # Create one file
            MediaFile.objects.create(
                file=f"test_files/batch_{i}.jpg",
                original_filename=f"batch_{i}.jpg",
                media_type=MediaFile.MediaType.IMAGE,
                mime_type="image/jpeg",
                file_size=1000,
                uploader=user,
                visibility=MediaFile.Visibility.PRIVATE,
                scan_status=MediaFile.ScanStatus.CLEAN,
                processing_status=MediaFile.ProcessingStatus.READY,
            )
            # Set wrong quota (drift of 5000 each)
            user.profile.total_storage_bytes = 6000  # Should be 1000
            user.profile.save()

        result = recalculate_all_storage_quotas()

        # Total drift should be at least 10000 (2 users * 5000 each)
        assert result["total_drift_bytes"] >= 10000
