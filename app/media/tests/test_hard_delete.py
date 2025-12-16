"""
Tests for hard delete expired files task.

Tests the permanent deletion of soft-deleted files after retention period.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from authentication.tests.factories import UserFactory
from media.models import MediaFile, MediaFileTag, Tag


@pytest.fixture
def user_for_hard_delete(db):
    """Create user for hard delete tests."""
    user = UserFactory(email_verified=True)
    user.profile.storage_quota_bytes = 1024 * 1024 * 1024  # 1GB
    user.profile.total_storage_bytes = 0
    user.profile.save()
    return user


@pytest.fixture
def sample_file_for_delete():
    """Create a simple file for deletion tests."""
    return SimpleUploadedFile(
        name="test_delete.txt",
        content=b"Test file content for deletion",
        content_type="text/plain",
    )


@pytest.fixture
def soft_deleted_file_past_retention(user_for_hard_delete, sample_file_for_delete, db):
    """Create a soft-deleted file that has passed the retention period."""
    media_file = MediaFile.objects.create(
        file=sample_file_for_delete,
        original_filename="test_delete.txt",
        media_type=MediaFile.MediaType.OTHER,
        mime_type="text/plain",
        file_size=len(b"Test file content for deletion"),
        uploader=user_for_hard_delete,
        visibility=MediaFile.Visibility.PRIVATE,
    )

    # Soft delete and backdate the deletion
    media_file.soft_delete()
    # Set deleted_at to 31 days ago (past 30 day retention)
    MediaFile.all_objects.filter(pk=media_file.pk).update(
        deleted_at=timezone.now() - timedelta(days=31)
    )
    media_file.refresh_from_db()

    return media_file


@pytest.fixture
def soft_deleted_file_within_retention(user_for_hard_delete, db):
    """Create a soft-deleted file that is within the retention period."""
    file_content = b"Recent deleted file content"
    uploaded_file = SimpleUploadedFile(
        name="recent_delete.txt",
        content=file_content,
        content_type="text/plain",
    )
    media_file = MediaFile.objects.create(
        file=uploaded_file,
        original_filename="recent_delete.txt",
        media_type=MediaFile.MediaType.OTHER,
        mime_type="text/plain",
        file_size=len(file_content),
        uploader=user_for_hard_delete,
        visibility=MediaFile.Visibility.PRIVATE,
    )

    # Soft delete but keep deleted_at as now (within retention)
    media_file.soft_delete()
    return media_file


@pytest.fixture
def active_file(user_for_hard_delete, db):
    """Create an active (non-deleted) file."""
    file_content = b"Active file content"
    uploaded_file = SimpleUploadedFile(
        name="active_file.txt",
        content=file_content,
        content_type="text/plain",
    )
    media_file = MediaFile.objects.create(
        file=uploaded_file,
        original_filename="active_file.txt",
        media_type=MediaFile.MediaType.OTHER,
        mime_type="text/plain",
        file_size=len(file_content),
        uploader=user_for_hard_delete,
        visibility=MediaFile.Visibility.PRIVATE,
    )
    return media_file


class TestHardDeleteExpiredFiles:
    """Tests for hard_delete_expired_files task."""

    @pytest.mark.django_db
    def test_deletes_expired_soft_deleted_files(self, soft_deleted_file_past_retention):
        """Files past retention period are permanently deleted."""
        from media.tasks import hard_delete_expired_files

        file_id = soft_deleted_file_past_retention.pk

        # Run the task
        result = hard_delete_expired_files()

        # Verify file is permanently deleted
        assert result["deleted_count"] == 1
        assert not MediaFile.all_objects.filter(pk=file_id).exists()

    @pytest.mark.django_db
    def test_preserves_files_within_retention(self, soft_deleted_file_within_retention):
        """Files within retention period are not deleted."""
        from media.tasks import hard_delete_expired_files

        file_id = soft_deleted_file_within_retention.pk

        # Run the task
        result = hard_delete_expired_files()

        # Verify file still exists
        assert result["deleted_count"] == 0
        assert MediaFile.all_objects.filter(pk=file_id).exists()

    @pytest.mark.django_db
    def test_preserves_active_files(self, active_file):
        """Active (non-deleted) files are not affected."""
        from media.tasks import hard_delete_expired_files

        file_id = active_file.pk

        # Run the task
        result = hard_delete_expired_files()

        # Verify file still exists
        assert result["deleted_count"] == 0
        assert MediaFile.objects.filter(pk=file_id).exists()

    @pytest.mark.django_db
    def test_deletes_associated_tags(
        self, soft_deleted_file_past_retention, user_for_hard_delete
    ):
        """Tags associated with deleted files are also deleted."""
        from media.tasks import hard_delete_expired_files

        # Add a tag to the file
        tag = Tag.objects.create(
            name="Test Tag",
            slug="test-tag",
            tag_type=Tag.TagType.USER,
            owner=user_for_hard_delete,
        )
        MediaFileTag.objects.create(
            media_file=soft_deleted_file_past_retention,
            tag=tag,
            applied_by=user_for_hard_delete,
        )

        file_id = soft_deleted_file_past_retention.pk
        tag_id = tag.pk

        # Run the task
        result = hard_delete_expired_files()

        # Verify file and tag association are deleted
        assert result["deleted_count"] == 1
        assert not MediaFile.all_objects.filter(pk=file_id).exists()
        # Tag itself should still exist, but the association should be gone
        assert Tag.objects.filter(pk=tag_id).exists()
        assert not MediaFileTag.objects.filter(tag=tag).exists()

    @pytest.mark.django_db
    def test_tracks_storage_freed(self, soft_deleted_file_past_retention):
        """Task tracks amount of storage freed."""
        from media.tasks import hard_delete_expired_files

        expected_size = soft_deleted_file_past_retention.file_size

        result = hard_delete_expired_files()

        assert result["storage_freed_bytes"] == expected_size

    @pytest.mark.django_db
    def test_handles_multiple_expired_files(self, user_for_hard_delete, db):
        """Task handles multiple expired files correctly."""
        from media.tasks import hard_delete_expired_files

        # Create multiple expired files
        file_ids = []
        total_size = 0
        for i in range(3):
            file_content = f"Content for file {i}".encode()
            uploaded_file = SimpleUploadedFile(
                name=f"expired_{i}.txt",
                content=file_content,
                content_type="text/plain",
            )
            media_file = MediaFile.objects.create(
                file=uploaded_file,
                original_filename=f"expired_{i}.txt",
                media_type=MediaFile.MediaType.OTHER,
                mime_type="text/plain",
                file_size=len(file_content),
                uploader=user_for_hard_delete,
                visibility=MediaFile.Visibility.PRIVATE,
            )
            media_file.soft_delete()
            MediaFile.all_objects.filter(pk=media_file.pk).update(
                deleted_at=timezone.now() - timedelta(days=31)
            )
            file_ids.append(media_file.pk)
            total_size += len(file_content)

        result = hard_delete_expired_files()

        assert result["deleted_count"] == 3
        assert result["storage_freed_bytes"] == total_size
        for file_id in file_ids:
            assert not MediaFile.all_objects.filter(pk=file_id).exists()

    @pytest.mark.django_db
    def test_returns_empty_result_when_no_expired_files(self, db):
        """Task returns zero counts when no files are expired."""
        from media.tasks import hard_delete_expired_files

        result = hard_delete_expired_files()

        assert result["deleted_count"] == 0
        assert result["storage_freed_bytes"] == 0
        assert result["errors"] == []


class TestHardDeleteWithShares:
    """Tests for hard delete with file shares."""

    @pytest.mark.django_db
    def test_deletes_associated_shares(
        self, soft_deleted_file_past_retention, user_for_hard_delete
    ):
        """Shares associated with deleted files are also deleted."""
        from media.models import MediaFileShare
        from media.tasks import hard_delete_expired_files

        # Create another user to share with
        other_user = UserFactory(email_verified=True)

        # Create a share
        share = MediaFileShare.objects.create(
            media_file=soft_deleted_file_past_retention,
            shared_with=other_user,
            shared_by=user_for_hard_delete,
        )

        file_id = soft_deleted_file_past_retention.pk
        share_id = share.pk

        # Run the task
        result = hard_delete_expired_files()

        # Verify file and share are deleted
        assert result["deleted_count"] == 1
        assert not MediaFile.all_objects.filter(pk=file_id).exists()
        assert not MediaFileShare.objects.filter(pk=share_id).exists()


class TestRetentionPeriodConfiguration:
    """Tests for retention period configuration."""

    @pytest.mark.django_db
    def test_uses_configured_retention_days(self, user_for_hard_delete, settings, db):
        """Task uses SOFT_DELETE_RETENTION_DAYS from settings."""
        from media.tasks import hard_delete_expired_files

        # Set a shorter retention period
        settings.SOFT_DELETE_RETENTION_DAYS = 7

        # Create a file deleted 10 days ago
        file_content = b"Seven day retention test"
        uploaded_file = SimpleUploadedFile(
            name="short_retention.txt",
            content=file_content,
            content_type="text/plain",
        )
        media_file = MediaFile.objects.create(
            file=uploaded_file,
            original_filename="short_retention.txt",
            media_type=MediaFile.MediaType.OTHER,
            mime_type="text/plain",
            file_size=len(file_content),
            uploader=user_for_hard_delete,
            visibility=MediaFile.Visibility.PRIVATE,
        )
        media_file.soft_delete()
        MediaFile.all_objects.filter(pk=media_file.pk).update(
            deleted_at=timezone.now() - timedelta(days=10)
        )

        file_id = media_file.pk

        result = hard_delete_expired_files()

        # File should be deleted because 10 > 7 days
        assert result["deleted_count"] == 1
        assert not MediaFile.all_objects.filter(pk=file_id).exists()

    @pytest.mark.django_db
    def test_file_just_under_retention_boundary_not_deleted(
        self, user_for_hard_delete, settings, db
    ):
        """File just under retention boundary is not deleted."""
        from media.tasks import hard_delete_expired_files

        settings.SOFT_DELETE_RETENTION_DAYS = 30

        # Create a file deleted 29 days ago (within retention)
        file_content = b"Boundary test content"
        uploaded_file = SimpleUploadedFile(
            name="boundary_test.txt",
            content=file_content,
            content_type="text/plain",
        )
        media_file = MediaFile.objects.create(
            file=uploaded_file,
            original_filename="boundary_test.txt",
            media_type=MediaFile.MediaType.OTHER,
            mime_type="text/plain",
            file_size=len(file_content),
            uploader=user_for_hard_delete,
            visibility=MediaFile.Visibility.PRIVATE,
        )
        media_file.soft_delete()
        # Set to 29 days ago (within 30 day retention)
        MediaFile.all_objects.filter(pk=media_file.pk).update(
            deleted_at=timezone.now() - timedelta(days=29)
        )

        file_id = media_file.pk

        result = hard_delete_expired_files()

        # File should NOT be deleted (29 < 30 days)
        assert result["deleted_count"] == 0
        assert MediaFile.all_objects.filter(pk=file_id).exists()
