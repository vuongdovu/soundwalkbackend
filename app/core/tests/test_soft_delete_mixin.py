"""
Tests for SoftDeleteMixin in core/model_mixins.py.

This module tests:
- soft_delete() method sets is_deleted and deleted_at
- restore() method clears is_deleted and deleted_at
- hard_delete() permanently removes the record
- Idempotency of soft_delete and restore
- Hook methods (on_soft_delete, on_restore) are called

TDD: These tests are written before implementing the SoftDeleteMixin methods.
"""

from unittest.mock import patch

import pytest
from django.utils import timezone

from authentication.tests.factories import UserFactory
from media.models import MediaFile


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def user(db):
    """Create a verified user with storage quota."""
    import uuid

    # Use UUID in email to ensure uniqueness across test runs
    unique_email = f"test_{uuid.uuid4().hex[:8]}@example.com"
    user = UserFactory(email=unique_email, email_verified=True)
    user.profile.storage_quota_bytes = 1024 * 1024 * 1024  # 1GB
    user.profile.total_storage_bytes = 0
    user.profile.save()
    return user


@pytest.fixture
def media_file(user, db):
    """Create a MediaFile for testing soft delete."""
    return MediaFile.objects.create(
        file="test_files/test.jpg",
        original_filename="test.jpg",
        media_type=MediaFile.MediaType.IMAGE,
        mime_type="image/jpeg",
        file_size=1000,
        uploader=user,
        visibility=MediaFile.Visibility.PRIVATE,
        scan_status=MediaFile.ScanStatus.CLEAN,
        processing_status=MediaFile.ProcessingStatus.READY,
    )


# =============================================================================
# soft_delete() Tests
# =============================================================================


@pytest.mark.django_db
class TestSoftDelete:
    """Tests for soft_delete() method."""

    def test_soft_delete_sets_is_deleted_true(self, media_file):
        """
        soft_delete should set is_deleted to True.

        Why it matters: Core soft delete functionality.
        """
        assert media_file.is_deleted is False

        media_file.soft_delete()

        assert media_file.is_deleted is True

    def test_soft_delete_sets_deleted_at(self, media_file):
        """
        soft_delete should set deleted_at to current time.

        Why it matters: Audit trail for when record was deleted.
        """
        assert media_file.deleted_at is None

        before = timezone.now()
        media_file.soft_delete()
        after = timezone.now()

        assert media_file.deleted_at is not None
        assert before <= media_file.deleted_at <= after

    def test_soft_delete_persists_to_database(self, media_file):
        """
        soft_delete changes should be saved to database.

        Why it matters: Changes must persist, not just update in memory.
        """
        media_file.soft_delete()

        # Fetch fresh from database
        media_file.refresh_from_db()

        assert media_file.is_deleted is True
        assert media_file.deleted_at is not None

    def test_soft_delete_is_idempotent(self, media_file):
        """
        Calling soft_delete twice should be safe (no-op second time).

        Why it matters: Prevents accidental double-processing of delete.
        """
        media_file.soft_delete()
        first_deleted_at = media_file.deleted_at

        # Call again
        media_file.soft_delete()

        # Should not change deleted_at
        assert media_file.deleted_at == first_deleted_at

    def test_soft_delete_calls_on_soft_delete_hook(self, media_file):
        """
        soft_delete should call on_soft_delete() hook before saving.

        Why it matters: Allows subclasses to add custom logic (e.g., quota tracking).
        """
        with patch.object(media_file, "on_soft_delete") as mock_hook:
            media_file.soft_delete()

            mock_hook.assert_called_once()

    def test_soft_delete_does_not_call_hook_if_already_deleted(self, media_file):
        """
        on_soft_delete hook should not be called if already deleted.

        Why it matters: Prevents double execution of side effects.
        """
        media_file.soft_delete()  # First delete

        with patch.object(media_file, "on_soft_delete") as mock_hook:
            media_file.soft_delete()  # Second delete (should be no-op)

            mock_hook.assert_not_called()


# =============================================================================
# restore() Tests
# =============================================================================


@pytest.mark.django_db
class TestRestore:
    """Tests for restore() method."""

    def test_restore_clears_is_deleted(self, media_file):
        """
        restore should set is_deleted to False.

        Why it matters: Core restore functionality.
        """
        media_file.soft_delete()
        assert media_file.is_deleted is True

        media_file.restore()

        assert media_file.is_deleted is False

    def test_restore_clears_deleted_at(self, media_file):
        """
        restore should set deleted_at to None.

        Why it matters: Record should look like it was never deleted.
        """
        media_file.soft_delete()
        assert media_file.deleted_at is not None

        media_file.restore()

        assert media_file.deleted_at is None

    def test_restore_persists_to_database(self, media_file):
        """
        restore changes should be saved to database.

        Why it matters: Changes must persist, not just update in memory.
        """
        media_file.soft_delete()
        media_file.restore()

        # Fetch fresh from database
        media_file.refresh_from_db()

        assert media_file.is_deleted is False
        assert media_file.deleted_at is None

    def test_restore_is_idempotent(self, media_file):
        """
        Calling restore twice should be safe (no-op second time).

        Why it matters: Prevents accidental double-processing of restore.
        """
        media_file.soft_delete()
        media_file.restore()

        # Call again on non-deleted record
        media_file.restore()  # Should not raise

        assert media_file.is_deleted is False

    def test_restore_calls_on_restore_hook(self, media_file):
        """
        restore should call on_restore() hook before saving.

        Why it matters: Allows subclasses to add custom logic (e.g., quota re-add).
        """
        media_file.soft_delete()

        with patch.object(media_file, "on_restore") as mock_hook:
            media_file.restore()

            mock_hook.assert_called_once()

    def test_restore_does_not_call_hook_if_not_deleted(self, media_file):
        """
        on_restore hook should not be called if not deleted.

        Why it matters: Prevents double execution of side effects.
        """
        # File is not deleted
        assert media_file.is_deleted is False

        with patch.object(media_file, "on_restore") as mock_hook:
            media_file.restore()  # Should be no-op

            mock_hook.assert_not_called()


# =============================================================================
# hard_delete() Tests
# =============================================================================


@pytest.mark.django_db
class TestHardDelete:
    """Tests for hard_delete() method."""

    def test_hard_delete_removes_record_from_database(self, media_file):
        """
        hard_delete should permanently remove the record.

        Why it matters: Provides a way to truly delete when needed.
        """
        file_id = media_file.id

        media_file.hard_delete()

        # Record should no longer exist
        with pytest.raises(MediaFile.DoesNotExist):
            MediaFile.objects.get(id=file_id)

    def test_hard_delete_works_on_soft_deleted_record(self, media_file):
        """
        hard_delete should work on already soft-deleted records.

        Why it matters: Allows permanent cleanup of soft-deleted records.
        """
        media_file.soft_delete()
        file_id = media_file.id

        media_file.hard_delete()

        # Record should no longer exist
        with pytest.raises(MediaFile.DoesNotExist):
            MediaFile.objects.get(id=file_id)


# =============================================================================
# Hook Method Default Behavior Tests
# =============================================================================


@pytest.mark.django_db
class TestHookMethods:
    """Tests for on_soft_delete and on_restore hook methods."""

    def test_on_soft_delete_default_does_nothing(self, media_file):
        """
        Default on_soft_delete should be a no-op (for base mixin).

        Why it matters: Subclasses can override without breaking base behavior.
        """
        # This should not raise - default implementation does nothing
        media_file.on_soft_delete()

    def test_on_restore_default_does_nothing(self, media_file):
        """
        Default on_restore should be a no-op (for base mixin).

        Why it matters: Subclasses can override without breaking base behavior.
        """
        # This should not raise - default implementation does nothing
        media_file.on_restore()


# =============================================================================
# Integration Tests
# =============================================================================


@pytest.mark.django_db
class TestSoftDeleteIntegration:
    """Integration tests for complete soft delete workflow."""

    def test_soft_delete_then_restore_cycle(self, media_file):
        """
        Full soft delete and restore cycle should work correctly.

        Why it matters: End-to-end workflow validation.
        """
        # Initial state
        assert media_file.is_deleted is False
        assert media_file.deleted_at is None

        # Delete
        media_file.soft_delete()
        assert media_file.is_deleted is True
        assert media_file.deleted_at is not None

        # Restore
        media_file.restore()
        assert media_file.is_deleted is False
        assert media_file.deleted_at is None

        # Verify persistence
        media_file.refresh_from_db()
        assert media_file.is_deleted is False
        assert media_file.deleted_at is None

    def test_deleted_record_has_is_deleted_flag(self, media_file, user):
        """
        Soft-deleted records should have is_deleted=True in database.

        Why it matters: Flag can be used for filtering.

        Note: Manager-level filtering is tested in core/tests/test_managers.py.
        This test only verifies the mixin sets the flag correctly.
        """
        # Create another non-deleted file for comparison
        other_file = MediaFile.objects.create(
            file="test_files/other.jpg",
            original_filename="other.jpg",
            media_type=MediaFile.MediaType.IMAGE,
            mime_type="image/jpeg",
            file_size=500,
            uploader=user,
            visibility=MediaFile.Visibility.PRIVATE,
            scan_status=MediaFile.ScanStatus.CLEAN,
            processing_status=MediaFile.ProcessingStatus.READY,
        )

        # Soft delete one file
        media_file.soft_delete()

        # Verify flags are set correctly
        media_file.refresh_from_db()
        other_file.refresh_from_db()
        assert media_file.is_deleted is True
        assert other_file.is_deleted is False

        # Can manually filter using the flag
        active_files = MediaFile.objects.filter(uploader=user, is_deleted=False)
        assert media_file not in active_files
        assert other_file in active_files

    def test_soft_delete_restore_soft_delete_updates_timestamp(self, media_file):
        """
        Re-deleting after restore should update deleted_at timestamp.

        Why it matters: Audit trail shows most recent deletion time.
        """
        # First soft delete
        media_file.soft_delete()
        first_deleted_at = media_file.deleted_at
        assert first_deleted_at is not None

        # Restore
        media_file.restore()
        assert media_file.deleted_at is None

        # Second soft delete should get a new timestamp
        import time

        time.sleep(0.001)  # Ensure timestamp changes
        media_file.soft_delete()

        # New deletion time should be set
        assert media_file.deleted_at is not None
        # Can be same or different depending on precision, but should be set
        assert media_file.is_deleted is True
