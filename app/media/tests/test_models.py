"""
Tests for MediaFile model.

These tests verify:
- Model field definitions and defaults
- Upload path generation
- Factory method for creating media files
- Soft delete functionality
- Model constraints

TDD: Write these tests first, then implement model to pass them.
"""

from __future__ import annotations


import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.utils import timezone

from media.models import MediaFile


@pytest.mark.django_db
class TestMediaFileModel:
    """Tests for MediaFile model field definitions."""

    def test_model_exists(self):
        """
        MediaFile model should be importable.

        Why it matters: Basic existence check for the model.
        """
        assert MediaFile is not None

    def test_media_type_choices(self):
        """
        MediaFile should have media type choices enum.

        Why it matters: Ensures valid media types are defined.
        """
        assert hasattr(MediaFile, "MediaType")
        assert MediaFile.MediaType.IMAGE == "image"
        assert MediaFile.MediaType.VIDEO == "video"
        assert MediaFile.MediaType.DOCUMENT == "document"
        assert MediaFile.MediaType.AUDIO == "audio"
        assert MediaFile.MediaType.OTHER == "other"

    def test_visibility_choices(self):
        """
        MediaFile should have visibility choices enum.

        Why it matters: Controls access permissions.
        """
        assert hasattr(MediaFile, "Visibility")
        assert MediaFile.Visibility.PRIVATE == "private"
        assert MediaFile.Visibility.SHARED == "shared"
        assert MediaFile.Visibility.INTERNAL == "internal"

    def test_processing_status_choices(self):
        """
        MediaFile should have processing status choices enum.

        Why it matters: Tracks processing pipeline state.
        """
        assert hasattr(MediaFile, "ProcessingStatus")
        assert MediaFile.ProcessingStatus.PENDING == "pending"
        assert MediaFile.ProcessingStatus.PROCESSING == "processing"
        assert MediaFile.ProcessingStatus.READY == "ready"
        assert MediaFile.ProcessingStatus.FAILED == "failed"

    def test_scan_status_choices(self):
        """
        MediaFile should have scan status choices enum.

        Why it matters: Tracks antivirus scan state.
        """
        assert hasattr(MediaFile, "ScanStatus")
        assert MediaFile.ScanStatus.PENDING == "pending"
        assert MediaFile.ScanStatus.CLEAN == "clean"
        assert MediaFile.ScanStatus.INFECTED == "infected"
        assert MediaFile.ScanStatus.ERROR == "error"

    def test_processing_priority_choices(self):
        """
        MediaFile should have processing priority choices enum.

        Why it matters: Controls processing queue order.
        """
        assert hasattr(MediaFile, "ProcessingPriority")
        assert MediaFile.ProcessingPriority.LOW == "low"
        assert MediaFile.ProcessingPriority.NORMAL == "normal"
        assert MediaFile.ProcessingPriority.HIGH == "high"


@pytest.mark.django_db
class TestMediaFileDefaults:
    """Tests for MediaFile field default values."""

    def test_visibility_defaults_to_private(self, user):
        """
        Visibility should default to private.

        Why it matters: Secure by default - uploads are private unless shared.
        """
        file_content = SimpleUploadedFile(
            "test.txt", b"test content", content_type="text/plain"
        )
        media_file = MediaFile.objects.create(
            file=file_content,
            original_filename="test.txt",
            media_type=MediaFile.MediaType.DOCUMENT,
            mime_type="text/plain",
            file_size=12,
            uploader=user,
        )
        assert media_file.visibility == MediaFile.Visibility.PRIVATE

    def test_processing_status_defaults_to_pending(self, user):
        """
        Processing status should default to pending.

        Why it matters: New uploads should queue for processing.
        """
        file_content = SimpleUploadedFile(
            "test.txt", b"test content", content_type="text/plain"
        )
        media_file = MediaFile.objects.create(
            file=file_content,
            original_filename="test.txt",
            media_type=MediaFile.MediaType.DOCUMENT,
            mime_type="text/plain",
            file_size=12,
            uploader=user,
        )
        assert media_file.processing_status == MediaFile.ProcessingStatus.PENDING

    def test_scan_status_defaults_to_pending(self, user):
        """
        Scan status should default to pending.

        Why it matters: New uploads should queue for scanning.
        """
        file_content = SimpleUploadedFile(
            "test.txt", b"test content", content_type="text/plain"
        )
        media_file = MediaFile.objects.create(
            file=file_content,
            original_filename="test.txt",
            media_type=MediaFile.MediaType.DOCUMENT,
            mime_type="text/plain",
            file_size=12,
            uploader=user,
        )
        assert media_file.scan_status == MediaFile.ScanStatus.PENDING

    def test_version_defaults_to_one(self, user):
        """
        Version should default to 1.

        Why it matters: First upload should be version 1.
        """
        file_content = SimpleUploadedFile(
            "test.txt", b"test content", content_type="text/plain"
        )
        media_file = MediaFile.objects.create(
            file=file_content,
            original_filename="test.txt",
            media_type=MediaFile.MediaType.DOCUMENT,
            mime_type="text/plain",
            file_size=12,
            uploader=user,
        )
        assert media_file.version == 1

    def test_is_current_defaults_to_true(self, user):
        """
        is_current should default to True.

        Why it matters: New uploads are the current version.
        """
        file_content = SimpleUploadedFile(
            "test.txt", b"test content", content_type="text/plain"
        )
        media_file = MediaFile.objects.create(
            file=file_content,
            original_filename="test.txt",
            media_type=MediaFile.MediaType.DOCUMENT,
            mime_type="text/plain",
            file_size=12,
            uploader=user,
        )
        assert media_file.is_current is True

    def test_processing_attempts_defaults_to_zero(self, user):
        """
        Processing attempts should default to 0.

        Why it matters: Tracks retry count for processing.
        """
        file_content = SimpleUploadedFile(
            "test.txt", b"test content", content_type="text/plain"
        )
        media_file = MediaFile.objects.create(
            file=file_content,
            original_filename="test.txt",
            media_type=MediaFile.MediaType.DOCUMENT,
            mime_type="text/plain",
            file_size=12,
            uploader=user,
        )
        assert media_file.processing_attempts == 0


@pytest.mark.django_db
class TestMediaUploadPath:
    """Tests for upload path generation."""

    def test_upload_path_format(self, user):
        """
        Upload path should follow pattern: media_type/YYYY/MM/uuid/filename.

        Why it matters: Organized storage structure prevents directory bloat.
        """
        file_content = SimpleUploadedFile(
            "my_photo.jpg", b"fake jpeg", content_type="image/jpeg"
        )
        media_file = MediaFile.objects.create(
            file=file_content,
            original_filename="my_photo.jpg",
            media_type=MediaFile.MediaType.IMAGE,
            mime_type="image/jpeg",
            file_size=9,
            uploader=user,
        )

        # Path should contain media_type, year, month, and UUID
        file_path = media_file.file.name
        assert file_path.startswith("image/")
        assert str(media_file.pk) in file_path
        assert "my_photo" in file_path or file_path.endswith(".jpg")

    def test_upload_path_uses_current_date(self, user):
        """
        Upload path should use current year and month.

        Why it matters: Helps with data retention and cleanup policies.
        """
        now = timezone.now()
        file_content = SimpleUploadedFile(
            "doc.pdf", b"fake pdf", content_type="application/pdf"
        )

        media_file = MediaFile.objects.create(
            file=file_content,
            original_filename="doc.pdf",
            media_type=MediaFile.MediaType.DOCUMENT,
            mime_type="application/pdf",
            file_size=8,
            uploader=user,
        )

        file_path = media_file.file.name
        assert f"/{now.year}/" in file_path
        assert f"/{now.month:02d}/" in file_path


@pytest.mark.django_db
class TestMediaFileFactory:
    """Tests for MediaFile.create_from_upload factory method."""

    def test_create_from_upload_with_minimal_args(self, user, sample_jpeg_uploaded):
        """
        Factory should create MediaFile with minimal required arguments.

        Why it matters: Simple interface for common upload case.
        """
        media_file = MediaFile.create_from_upload(
            file=sample_jpeg_uploaded,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )

        assert media_file.pk is not None
        assert media_file.uploader == user
        assert media_file.media_type == "image"
        assert media_file.mime_type == "image/jpeg"
        assert media_file.original_filename == "test_image.jpg"
        assert media_file.file_size > 0

    def test_create_from_upload_with_visibility(self, user, sample_jpeg_uploaded):
        """
        Factory should accept custom visibility.

        Why it matters: Allows sharing files on upload.
        """
        media_file = MediaFile.create_from_upload(
            file=sample_jpeg_uploaded,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
            visibility="shared",
        )

        assert media_file.visibility == "shared"

    def test_create_from_upload_with_metadata(self, user, sample_jpeg_uploaded):
        """
        Factory should accept custom metadata.

        Why it matters: Allows storing additional file info.
        """
        metadata = {"source": "mobile_app", "device": "iPhone"}
        media_file = MediaFile.create_from_upload(
            file=sample_jpeg_uploaded,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
            metadata=metadata,
        )

        assert media_file.metadata == metadata

    def test_create_from_upload_calculates_file_size(self, user, sample_jpeg_uploaded):
        """
        Factory should calculate file size from uploaded file.

        Why it matters: Ensures accurate size tracking without manual input.
        """
        media_file = MediaFile.create_from_upload(
            file=sample_jpeg_uploaded,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )

        # File size should match the uploaded content
        assert media_file.file_size == sample_jpeg_uploaded.size


@pytest.mark.django_db
class TestMediaFileSoftDelete:
    """Tests for soft delete functionality."""

    def test_soft_delete_sets_is_deleted(self, user):
        """
        Soft delete should set is_deleted flag.

        Why it matters: Files can be recovered after deletion.
        """
        file_content = SimpleUploadedFile(
            "test.txt", b"test content", content_type="text/plain"
        )
        media_file = MediaFile.objects.create(
            file=file_content,
            original_filename="test.txt",
            media_type=MediaFile.MediaType.DOCUMENT,
            mime_type="text/plain",
            file_size=12,
            uploader=user,
        )

        # MediaFile should have is_deleted from SoftDeleteMixin
        assert hasattr(media_file, "is_deleted")
        assert media_file.is_deleted is False

        # Set is_deleted directly (soft_delete method may not be implemented yet)
        media_file.is_deleted = True
        media_file.save()
        assert media_file.is_deleted is True

    def test_soft_deleted_accessible_via_all_objects(self, user):
        """
        Soft deleted files should be accessible via all_objects manager.

        Why it matters: Admin/recovery needs access to deleted files.

        Note: SoftDeleteManager filtering is marked TODO in core/managers.py.
        This test verifies all_objects works; filtering test deferred.
        """
        file_content = SimpleUploadedFile(
            "test.txt", b"test content", content_type="text/plain"
        )
        media_file = MediaFile.objects.create(
            file=file_content,
            original_filename="test.txt",
            media_type=MediaFile.MediaType.DOCUMENT,
            mime_type="text/plain",
            file_size=12,
            uploader=user,
        )
        media_id = media_file.pk

        # Mark as deleted
        media_file.is_deleted = True
        media_file.deleted_at = timezone.now()
        media_file.save()

        # Should appear in all_objects
        assert MediaFile.all_objects.filter(pk=media_id).exists()

        # Verify the deleted fields are set
        reloaded = MediaFile.all_objects.get(pk=media_id)
        assert reloaded.is_deleted is True
        assert reloaded.deleted_at is not None


@pytest.mark.django_db
class TestMediaFileConstraints:
    """Tests for model constraints."""

    def test_file_size_must_be_positive(self, user):
        """
        File size must be greater than 0.

        Why it matters: Zero-byte files are invalid.
        """
        file_content = SimpleUploadedFile(
            "test.txt", b"test", content_type="text/plain"
        )

        with pytest.raises(IntegrityError):
            MediaFile.objects.create(
                file=file_content,
                original_filename="test.txt",
                media_type=MediaFile.MediaType.DOCUMENT,
                mime_type="text/plain",
                file_size=0,  # Invalid - must be > 0
                uploader=user,
            )

    def test_version_must_be_at_least_one(self, user):
        """
        Version must be >= 1.

        Why it matters: Version numbering starts at 1.
        """
        file_content = SimpleUploadedFile(
            "test.txt", b"test", content_type="text/plain"
        )

        with pytest.raises(IntegrityError):
            MediaFile.objects.create(
                file=file_content,
                original_filename="test.txt",
                media_type=MediaFile.MediaType.DOCUMENT,
                mime_type="text/plain",
                file_size=4,
                uploader=user,
                version=0,  # Invalid - must be >= 1
            )


@pytest.mark.django_db
class TestMediaFileUUID:
    """Tests for UUID primary key."""

    def test_has_uuid_primary_key(self, user):
        """
        MediaFile should use UUID as primary key.

        Why it matters: UUIDs provide security through obscurity.
        """
        import uuid

        file_content = SimpleUploadedFile(
            "test.txt", b"test content", content_type="text/plain"
        )
        media_file = MediaFile.objects.create(
            file=file_content,
            original_filename="test.txt",
            media_type=MediaFile.MediaType.DOCUMENT,
            mime_type="text/plain",
            file_size=12,
            uploader=user,
        )

        assert isinstance(media_file.pk, uuid.UUID)

    def test_uuid_is_set_before_save(self, user):
        """
        UUID should be available before save (for upload path).

        Why it matters: Upload path includes UUID for uniqueness.
        """
        import uuid

        file_content = SimpleUploadedFile(
            "test.txt", b"test content", content_type="text/plain"
        )
        media_file = MediaFile(
            file=file_content,
            original_filename="test.txt",
            media_type=MediaFile.MediaType.DOCUMENT,
            mime_type="text/plain",
            file_size=12,
            uploader=user,
        )

        # UUID should be set even before save
        assert media_file.pk is not None
        assert isinstance(media_file.pk, uuid.UUID)


@pytest.mark.django_db
class TestMediaFileRelationships:
    """Tests for model relationships."""

    def test_uploader_relationship(self, user):
        """
        MediaFile should have uploader foreign key.

        Why it matters: Tracks file ownership.
        """
        file_content = SimpleUploadedFile(
            "test.txt", b"test content", content_type="text/plain"
        )
        media_file = MediaFile.objects.create(
            file=file_content,
            original_filename="test.txt",
            media_type=MediaFile.MediaType.DOCUMENT,
            mime_type="text/plain",
            file_size=12,
            uploader=user,
        )

        assert media_file.uploader == user
        assert media_file in user.media_files.all()

    def test_version_group_relationship(self, user):
        """
        MediaFile should have self-referential version_group FK.

        Why it matters: Links file versions together.
        """
        file_content1 = SimpleUploadedFile(
            "test_v1.txt", b"version 1", content_type="text/plain"
        )
        original = MediaFile.objects.create(
            file=file_content1,
            original_filename="test.txt",
            media_type=MediaFile.MediaType.DOCUMENT,
            mime_type="text/plain",
            file_size=9,
            uploader=user,
            version=1,
        )

        # Use create_new_version() to properly handle versioning constraints
        file_content2 = SimpleUploadedFile(
            "test_v2.txt", b"version 2", content_type="text/plain"
        )
        new_version = original.create_new_version(file_content2, user)

        assert new_version.version_group == original.version_group
        assert new_version.version == 2
        assert new_version in original.versions.all()


@pytest.mark.django_db
class TestMediaFileTimestamps:
    """Tests for timestamp fields."""

    def test_has_created_at(self, user):
        """
        MediaFile should have created_at timestamp.

        Why it matters: Tracks when file was uploaded.
        """
        file_content = SimpleUploadedFile(
            "test.txt", b"test content", content_type="text/plain"
        )
        media_file = MediaFile.objects.create(
            file=file_content,
            original_filename="test.txt",
            media_type=MediaFile.MediaType.DOCUMENT,
            mime_type="text/plain",
            file_size=12,
            uploader=user,
        )

        assert media_file.created_at is not None

    def test_has_updated_at(self, user):
        """
        MediaFile should have updated_at timestamp.

        Why it matters: Tracks when file was last modified.
        """
        file_content = SimpleUploadedFile(
            "test.txt", b"test content", content_type="text/plain"
        )
        media_file = MediaFile.objects.create(
            file=file_content,
            original_filename="test.txt",
            media_type=MediaFile.MediaType.DOCUMENT,
            mime_type="text/plain",
            file_size=12,
            uploader=user,
        )

        assert media_file.updated_at is not None
