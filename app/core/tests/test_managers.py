"""
Tests for SoftDeleteManager and SoftDeleteQuerySet.

These tests verify that:
- SoftDeleteManager filters out soft-deleted records by default
- QuerySet operations (delete, hard_delete, restore) work correctly
- Hooks (on_soft_delete, on_restore) are called for quota tracking
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from PIL import Image

from authentication.tests.factories import UserFactory
from media.models import MediaFile

if TYPE_CHECKING:
    from authentication.models import User


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def user(db) -> "User":
    """Create a verified user with default storage quota."""
    user = UserFactory(email_verified=True)
    user.profile.storage_quota_bytes = 1024 * 1024 * 1024  # 1GB
    user.profile.total_storage_bytes = 0
    user.profile.save()
    return user


@pytest.fixture
def sample_jpeg_file() -> SimpleUploadedFile:
    """Generate a valid JPEG image as SimpleUploadedFile."""
    image = Image.new("RGB", (100, 100), color="red")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    buffer.seek(0)
    return SimpleUploadedFile(
        name="test_image.jpg",
        content=buffer.read(),
        content_type="image/jpeg",
    )


@pytest.fixture
def sample_png_file() -> SimpleUploadedFile:
    """Generate a valid PNG image as SimpleUploadedFile."""
    image = Image.new("RGBA", (100, 100), color=(0, 0, 255, 128))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return SimpleUploadedFile(
        name="test_image.png",
        content=buffer.read(),
        content_type="image/png",
    )


# =============================================================================
# Tests
# =============================================================================


@pytest.mark.django_db
class TestSoftDeleteManagerFiltering:
    """Tests for SoftDeleteManager default filtering behavior."""

    def test_objects_excludes_deleted_by_default(self, user, sample_jpeg_file):
        """Default manager should filter out soft-deleted records."""
        media_file = MediaFile.create_from_upload(
            file=sample_jpeg_file,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )
        pk = media_file.pk

        # Before deletion - should be visible
        assert MediaFile.objects.filter(pk=pk).exists()

        # Soft delete
        media_file.soft_delete()

        # Should NOT appear in objects (default manager)
        assert not MediaFile.objects.filter(pk=pk).exists()

        # Should appear in all_objects
        assert MediaFile.all_objects.filter(pk=pk).exists()

    def test_objects_all_excludes_deleted(self, user, sample_jpeg_file):
        """MediaFile.objects.all() should exclude soft-deleted records."""
        media_file = MediaFile.create_from_upload(
            file=sample_jpeg_file,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )

        initial_count = MediaFile.objects.count()
        media_file.soft_delete()

        assert MediaFile.objects.count() == initial_count - 1

    def test_objects_get_raises_for_deleted(self, user, sample_jpeg_file):
        """MediaFile.objects.get() should raise DoesNotExist for deleted records."""
        media_file = MediaFile.create_from_upload(
            file=sample_jpeg_file,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )
        pk = media_file.pk
        media_file.soft_delete()

        with pytest.raises(MediaFile.DoesNotExist):
            MediaFile.objects.get(pk=pk)

    def test_all_objects_includes_deleted(self, user, sample_jpeg_file):
        """all_objects manager should include soft-deleted records."""
        media_file = MediaFile.create_from_upload(
            file=sample_jpeg_file,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )
        pk = media_file.pk
        media_file.soft_delete()

        # Should still be retrievable via all_objects
        retrieved = MediaFile.all_objects.get(pk=pk)
        assert retrieved.is_deleted is True


@pytest.mark.django_db
class TestSoftDeleteManagerMethods:
    """Tests for SoftDeleteManager convenience methods."""

    def test_deleted_returns_only_deleted_records(self, user, sample_jpeg_file):
        """deleted() should return only soft-deleted records."""
        media_file = MediaFile.create_from_upload(
            file=sample_jpeg_file,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )
        pk = media_file.pk

        # Not deleted yet - should not appear in deleted()
        assert not MediaFile.objects.deleted().filter(pk=pk).exists()

        media_file.soft_delete()

        # Now appears in deleted()
        assert MediaFile.objects.deleted().filter(pk=pk).exists()

    def test_with_deleted_returns_all_records(self, user, sample_jpeg_file):
        """with_deleted() should include both active and deleted records."""
        media_file = MediaFile.create_from_upload(
            file=sample_jpeg_file,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )
        pk = media_file.pk

        # Before deletion
        assert MediaFile.objects.with_deleted().filter(pk=pk).exists()

        media_file.soft_delete()

        # After deletion - still accessible via with_deleted
        assert MediaFile.objects.with_deleted().filter(pk=pk).exists()


@pytest.mark.django_db
class TestSoftDeleteQuerySetDelete:
    """Tests for SoftDeleteQuerySet.delete() method."""

    def test_queryset_delete_soft_deletes(self, user, sample_jpeg_file):
        """QuerySet.delete() should soft delete, not hard delete."""
        media_file = MediaFile.create_from_upload(
            file=sample_jpeg_file,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )
        pk = media_file.pk

        count, details = MediaFile.objects.filter(pk=pk).delete()

        assert count == 1
        assert "media.MediaFile" in details

        # Record still exists in database
        assert MediaFile.all_objects.filter(pk=pk).exists()

        # But is marked as deleted
        media_file.refresh_from_db()
        assert media_file.is_deleted is True
        assert media_file.deleted_at is not None

    def test_queryset_delete_sets_deleted_at_timestamp(self, user, sample_jpeg_file):
        """delete() should set deleted_at to current time."""
        media_file = MediaFile.create_from_upload(
            file=sample_jpeg_file,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )

        before_delete = timezone.now()
        MediaFile.objects.filter(pk=media_file.pk).delete()
        after_delete = timezone.now()

        media_file.refresh_from_db()
        assert before_delete <= media_file.deleted_at <= after_delete

    def test_queryset_delete_multiple_records(
        self, user, sample_jpeg_file, sample_png_file
    ):
        """delete() should work on multiple records."""
        file1 = MediaFile.create_from_upload(
            file=sample_jpeg_file,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )
        file2 = MediaFile.create_from_upload(
            file=sample_png_file,
            uploader=user,
            media_type="image",
            mime_type="image/png",
        )

        count, _ = MediaFile.objects.filter(pk__in=[file1.pk, file2.pk]).delete()

        assert count == 2

        file1.refresh_from_db()
        file2.refresh_from_db()
        assert file1.is_deleted is True
        assert file2.is_deleted is True

    def test_delete_calls_on_soft_delete_hook(self, user, sample_jpeg_file):
        """delete() should call on_soft_delete hook for quota tracking."""
        # Set up user profile with some storage
        user.profile.total_storage_bytes = 5000
        user.profile.save()

        media_file = MediaFile.create_from_upload(
            file=sample_jpeg_file,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )
        file_size = media_file.file_size

        # Record storage after upload
        user.profile.refresh_from_db()
        storage_after_upload = user.profile.total_storage_bytes

        # Soft delete via queryset
        MediaFile.objects.filter(pk=media_file.pk).delete()

        # Quota should be decremented
        user.profile.refresh_from_db()
        assert user.profile.total_storage_bytes == storage_after_upload - file_size


@pytest.mark.django_db
class TestSoftDeleteQuerySetHardDelete:
    """Tests for SoftDeleteQuerySet.hard_delete() method."""

    def test_hard_delete_permanently_removes_records(self, user, sample_jpeg_file):
        """hard_delete() should permanently remove records from database."""
        media_file = MediaFile.create_from_upload(
            file=sample_jpeg_file,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )
        pk = media_file.pk

        # Use with_deleted() to access SoftDeleteQuerySet which has hard_delete
        count, _ = MediaFile.objects.with_deleted().filter(pk=pk).hard_delete()

        assert count == 1
        # Record is completely gone
        assert not MediaFile.all_objects.filter(pk=pk).exists()

    def test_hard_delete_on_deleted_records(self, user, sample_jpeg_file):
        """hard_delete() should work on already soft-deleted records."""
        media_file = MediaFile.create_from_upload(
            file=sample_jpeg_file,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )
        pk = media_file.pk
        media_file.soft_delete()

        # Hard delete the soft-deleted record
        count, _ = MediaFile.objects.deleted().filter(pk=pk).hard_delete()

        assert count == 1
        assert not MediaFile.all_objects.filter(pk=pk).exists()


@pytest.mark.django_db
class TestSoftDeleteQuerySetRestore:
    """Tests for SoftDeleteQuerySet.restore() method."""

    def test_restore_makes_records_visible(self, user, sample_jpeg_file):
        """restore() should make soft-deleted records visible again."""
        media_file = MediaFile.create_from_upload(
            file=sample_jpeg_file,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )
        pk = media_file.pk
        media_file.soft_delete()

        # Should not be visible
        assert not MediaFile.objects.filter(pk=pk).exists()

        # Restore via queryset
        count = MediaFile.objects.deleted().filter(pk=pk).restore()

        assert count == 1

        # Now visible in default manager
        assert MediaFile.objects.filter(pk=pk).exists()

        media_file.refresh_from_db()
        assert media_file.is_deleted is False
        assert media_file.deleted_at is None

    def test_restore_multiple_records(self, user, sample_jpeg_file, sample_png_file):
        """restore() should work on multiple records."""
        file1 = MediaFile.create_from_upload(
            file=sample_jpeg_file,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )
        file2 = MediaFile.create_from_upload(
            file=sample_png_file,
            uploader=user,
            media_type="image",
            mime_type="image/png",
        )

        file1.soft_delete()
        file2.soft_delete()

        count = (
            MediaFile.objects.deleted().filter(pk__in=[file1.pk, file2.pk]).restore()
        )

        assert count == 2

        file1.refresh_from_db()
        file2.refresh_from_db()
        assert file1.is_deleted is False
        assert file2.is_deleted is False

    def test_restore_calls_on_restore_hook(self, user, sample_jpeg_file):
        """restore() should call on_restore hook for quota tracking."""
        media_file = MediaFile.create_from_upload(
            file=sample_jpeg_file,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )
        file_size = media_file.file_size
        media_file.soft_delete()

        user.profile.refresh_from_db()
        storage_after_delete = user.profile.total_storage_bytes

        # Restore via queryset
        MediaFile.objects.deleted().filter(pk=media_file.pk).restore()

        # Quota should be incremented
        user.profile.refresh_from_db()
        assert user.profile.total_storage_bytes == storage_after_delete + file_size


@pytest.mark.django_db
class TestSoftDeleteQuerySetFilters:
    """Tests for SoftDeleteQuerySet.deleted() and active() methods."""

    def test_deleted_filter_returns_only_deleted(
        self, user, sample_jpeg_file, sample_png_file
    ):
        """deleted() should return only soft-deleted records."""
        active_file = MediaFile.create_from_upload(
            file=sample_jpeg_file,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )
        deleted_file = MediaFile.create_from_upload(
            file=sample_png_file,
            uploader=user,
            media_type="image",
            mime_type="image/png",
        )
        deleted_file.soft_delete()

        deleted_qs = MediaFile.objects.with_deleted().deleted()

        assert deleted_file.pk in deleted_qs.values_list("pk", flat=True)
        assert active_file.pk not in deleted_qs.values_list("pk", flat=True)

    def test_active_filter_returns_only_active(
        self, user, sample_jpeg_file, sample_png_file
    ):
        """active() should return only non-deleted records."""
        active_file = MediaFile.create_from_upload(
            file=sample_jpeg_file,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )
        deleted_file = MediaFile.create_from_upload(
            file=sample_png_file,
            uploader=user,
            media_type="image",
            mime_type="image/png",
        )
        deleted_file.soft_delete()

        active_qs = MediaFile.objects.with_deleted().active()

        assert active_file.pk in active_qs.values_list("pk", flat=True)
        assert deleted_file.pk not in active_qs.values_list("pk", flat=True)
