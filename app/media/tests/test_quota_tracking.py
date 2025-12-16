"""
Tests for MediaFile soft_delete quota tracking.

This module tests:
- soft_delete decrements user's storage quota
- restore increments user's storage quota
- Idempotency (double delete/restore doesn't double count)
- Transaction safety (quota and delete are atomic)

TDD: These tests are written before implementing the MediaFile override.
"""

import uuid

import pytest

from authentication.tests.factories import UserFactory
from media.models import MediaFile


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def user(db):
    """Create a verified user with storage quota."""
    unique_email = f"test_{uuid.uuid4().hex[:8]}@example.com"
    user = UserFactory(email=unique_email, email_verified=True)
    user.profile.storage_quota_bytes = 1024 * 1024 * 1024  # 1GB
    user.profile.total_storage_bytes = 0
    user.profile.save()
    return user


@pytest.fixture
def media_file(user, db):
    """Create a MediaFile for testing quota tracking."""
    file_size = 5000  # 5KB
    media_file = MediaFile.objects.create(
        file="test_files/test.jpg",
        original_filename="test.jpg",
        media_type=MediaFile.MediaType.IMAGE,
        mime_type="image/jpeg",
        file_size=file_size,
        uploader=user,
        visibility=MediaFile.Visibility.PRIVATE,
        scan_status=MediaFile.ScanStatus.CLEAN,
        processing_status=MediaFile.ProcessingStatus.READY,
    )
    # Manually set initial quota as if file was uploaded
    user.profile.total_storage_bytes = file_size
    user.profile.save()
    return media_file


@pytest.fixture
def media_file_large(user, db):
    """Create a large MediaFile (10MB) for testing quota tracking."""
    file_size = 10 * 1024 * 1024  # 10MB
    media_file = MediaFile.objects.create(
        file="test_files/large_file.zip",
        original_filename="large_file.zip",
        media_type=MediaFile.MediaType.OTHER,
        mime_type="application/zip",
        file_size=file_size,
        uploader=user,
        visibility=MediaFile.Visibility.PRIVATE,
        scan_status=MediaFile.ScanStatus.CLEAN,
        processing_status=MediaFile.ProcessingStatus.READY,
    )
    # Manually set initial quota as if file was uploaded
    user.profile.total_storage_bytes = file_size
    user.profile.save()
    return media_file


# =============================================================================
# soft_delete Quota Decrement Tests
# =============================================================================


@pytest.mark.django_db
class TestSoftDeleteQuotaDecrement:
    """Tests for soft_delete decrementing user's storage quota."""

    def test_soft_delete_decrements_quota(self, media_file, user):
        """
        soft_delete should decrement user's storage quota by file size.

        Why it matters: Users shouldn't be charged for deleted files.
        """
        initial_quota = user.profile.total_storage_bytes
        file_size = media_file.file_size

        media_file.soft_delete()

        user.profile.refresh_from_db()
        assert user.profile.total_storage_bytes == initial_quota - file_size

    def test_soft_delete_sets_quota_to_zero_if_only_file(self, media_file, user):
        """
        soft_delete of only file should set quota to 0.

        Why it matters: Edge case - quota should be exactly 0, not negative.
        """
        media_file.soft_delete()

        user.profile.refresh_from_db()
        assert user.profile.total_storage_bytes == 0

    def test_soft_delete_large_file_decrements_correctly(self, media_file_large, user):
        """
        soft_delete of large file should decrement quota correctly.

        Why it matters: Verify correct handling of large file sizes.
        """
        initial_quota = user.profile.total_storage_bytes
        file_size = media_file_large.file_size
        assert file_size == 10 * 1024 * 1024  # 10MB

        media_file_large.soft_delete()

        user.profile.refresh_from_db()
        assert user.profile.total_storage_bytes == initial_quota - file_size

    def test_soft_delete_is_idempotent_for_quota(self, media_file, user):
        """
        Calling soft_delete twice should only decrement quota once.

        Why it matters: Prevents accidental double-decrement.
        """
        media_file.soft_delete()
        quota_after_first_delete = user.profile.total_storage_bytes
        user.profile.refresh_from_db()
        quota_after_first_delete = user.profile.total_storage_bytes

        # Second delete should be no-op
        media_file.soft_delete()

        user.profile.refresh_from_db()
        assert user.profile.total_storage_bytes == quota_after_first_delete

    def test_soft_delete_with_multiple_files(self, user):
        """
        soft_delete should only decrement for the specific file deleted.

        Why it matters: Other files should not be affected.
        """
        # Create two files
        file1 = MediaFile.objects.create(
            file="test_files/file1.jpg",
            original_filename="file1.jpg",
            media_type=MediaFile.MediaType.IMAGE,
            mime_type="image/jpeg",
            file_size=1000,
            uploader=user,
            visibility=MediaFile.Visibility.PRIVATE,
            scan_status=MediaFile.ScanStatus.CLEAN,
            processing_status=MediaFile.ProcessingStatus.READY,
        )
        MediaFile.objects.create(
            file="test_files/file2.jpg",
            original_filename="file2.jpg",
            media_type=MediaFile.MediaType.IMAGE,
            mime_type="image/jpeg",
            file_size=2000,
            uploader=user,
            visibility=MediaFile.Visibility.PRIVATE,
            scan_status=MediaFile.ScanStatus.CLEAN,
            processing_status=MediaFile.ProcessingStatus.READY,
        )

        # Set total quota to sum of both files
        user.profile.total_storage_bytes = 3000
        user.profile.save()

        # Delete first file
        file1.soft_delete()

        # Should only decrement by file1's size
        user.profile.refresh_from_db()
        assert user.profile.total_storage_bytes == 2000


# =============================================================================
# restore Quota Increment Tests
# =============================================================================


@pytest.mark.django_db
class TestRestoreQuotaIncrement:
    """Tests for restore incrementing user's storage quota."""

    def test_restore_increments_quota(self, media_file, user):
        """
        restore should increment user's storage quota by file size.

        Why it matters: Restored files should count against quota again.
        """
        file_size = media_file.file_size

        # Delete first
        media_file.soft_delete()
        user.profile.refresh_from_db()
        quota_after_delete = user.profile.total_storage_bytes

        # Restore
        media_file.restore()

        user.profile.refresh_from_db()
        assert user.profile.total_storage_bytes == quota_after_delete + file_size

    def test_restore_is_idempotent_for_quota(self, media_file, user):
        """
        Calling restore twice should only increment quota once.

        Why it matters: Prevents accidental double-increment.
        """
        media_file.soft_delete()
        media_file.restore()
        user.profile.refresh_from_db()
        quota_after_restore = user.profile.total_storage_bytes

        # Second restore should be no-op
        media_file.restore()

        user.profile.refresh_from_db()
        assert user.profile.total_storage_bytes == quota_after_restore

    def test_restore_on_non_deleted_file_does_nothing(self, media_file, user):
        """
        Calling restore on non-deleted file should not change quota.

        Why it matters: Prevent accidental quota inflation.
        """
        initial_quota = user.profile.total_storage_bytes

        # File is not deleted
        media_file.restore()

        user.profile.refresh_from_db()
        assert user.profile.total_storage_bytes == initial_quota


# =============================================================================
# Full Cycle Tests
# =============================================================================


@pytest.mark.django_db
class TestQuotaTrackingFullCycle:
    """Tests for complete delete/restore quota cycles."""

    def test_delete_restore_cycle_returns_to_original_quota(self, media_file, user):
        """
        Delete then restore should return quota to original value.

        Why it matters: Quota should be consistent after full cycle.
        """
        initial_quota = user.profile.total_storage_bytes

        media_file.soft_delete()
        media_file.restore()

        user.profile.refresh_from_db()
        assert user.profile.total_storage_bytes == initial_quota

    def test_multiple_delete_restore_cycles(self, media_file, user):
        """
        Multiple delete/restore cycles should maintain correct quota.

        Why it matters: Verify no quota drift over time.
        """
        initial_quota = user.profile.total_storage_bytes

        for _ in range(3):
            media_file.soft_delete()
            user.profile.refresh_from_db()
            assert user.profile.total_storage_bytes == 0

            media_file.restore()
            user.profile.refresh_from_db()
            assert user.profile.total_storage_bytes == initial_quota


# =============================================================================
# Consistency Tests
# =============================================================================


@pytest.mark.django_db
class TestQuotaTrackingConsistency:
    """Tests for quota and delete state consistency."""

    def test_soft_delete_and_quota_are_consistent(self, media_file, user):
        """
        After soft_delete, both is_deleted and quota should be updated.

        Why it matters: Ensures state consistency.
        """
        initial_quota = user.profile.total_storage_bytes
        file_size = media_file.file_size

        # Soft delete should succeed
        media_file.soft_delete()

        user.profile.refresh_from_db()
        media_file.refresh_from_db()

        # Both should be consistent
        assert media_file.is_deleted is True
        assert user.profile.total_storage_bytes == initial_quota - file_size


# =============================================================================
# Edge Cases
# =============================================================================


@pytest.mark.django_db
class TestQuotaTrackingEdgeCases:
    """Tests for edge cases in quota tracking."""

    def test_soft_delete_user_without_profile(self, user):
        """
        soft_delete should handle edge case where profile doesn't exist.

        Why it matters: Defensive coding against data inconsistencies.
        Note: This test documents expected behavior - profile should always exist.
        """
        media_file = MediaFile.objects.create(
            file="test_files/edge_case.jpg",
            original_filename="edge_case.jpg",
            media_type=MediaFile.MediaType.IMAGE,
            mime_type="image/jpeg",
            file_size=1000,
            uploader=user,
            visibility=MediaFile.Visibility.PRIVATE,
            scan_status=MediaFile.ScanStatus.CLEAN,
            processing_status=MediaFile.ProcessingStatus.READY,
        )

        # This should not raise, even if somehow profile check fails
        # The hasattr check in on_soft_delete should handle gracefully
        media_file.soft_delete()
        assert media_file.is_deleted is True

    def test_zero_size_file_soft_delete(self, user):
        """
        soft_delete of zero-size file should not change quota.

        Why it matters: Edge case - 0 byte files shouldn't affect quota.

        Note: This test is skipped because file_size > 0 is enforced by
        a database constraint (media_file_size_positive).
        """
        # Can't create 0-size file due to constraint
        # This test documents the expected behavior if such a file existed
        pass
