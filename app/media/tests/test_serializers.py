"""
Tests for media serializers.

These tests verify:
- Upload serializer file validation
- MIME type detection in serializer
- Visibility field handling
- Output serializer formatting
- Storage quota enforcement

TDD: Write these tests first, then implement serializers to pass them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from media.serializers import MediaFileSerializer, MediaFileUploadSerializer

if TYPE_CHECKING:
    pass


@pytest.mark.django_db
class TestMediaFileUploadSerializer:
    """Tests for MediaFileUploadSerializer."""

    def test_valid_jpeg_upload(self, user, sample_jpeg_uploaded):
        """
        Valid JPEG should pass validation.

        Why it matters: Core happy path for image uploads.
        """
        serializer = MediaFileUploadSerializer(
            data={"file": sample_jpeg_uploaded},
            context={"request": _mock_request(user)},
        )

        assert serializer.is_valid(), serializer.errors
        assert "file" in serializer.validated_data

    def test_valid_pdf_upload(self, user, sample_pdf_uploaded):
        """
        Valid PDF should pass validation.

        Why it matters: Documents are key media type.
        """
        serializer = MediaFileUploadSerializer(
            data={"file": sample_pdf_uploaded},
            context={"request": _mock_request(user)},
        )

        assert serializer.is_valid(), serializer.errors

    def test_executable_file_rejected(self, user, executable_file_uploaded):
        """
        Executable files should be rejected.

        Why it matters: Security - prevent malware upload.
        """
        serializer = MediaFileUploadSerializer(
            data={"file": executable_file_uploaded},
            context={"request": _mock_request(user)},
        )

        assert not serializer.is_valid()
        assert "file" in serializer.errors

    def test_empty_file_rejected(self, user, empty_file_uploaded):
        """
        Empty files should be rejected.

        Why it matters: Empty files are useless.
        """
        serializer = MediaFileUploadSerializer(
            data={"file": empty_file_uploaded},
            context={"request": _mock_request(user)},
        )

        assert not serializer.is_valid()
        assert "file" in serializer.errors

    def test_oversized_file_rejected(self, user, oversized_image_uploaded):
        """
        Files exceeding size limit should be rejected.

        Why it matters: Prevents storage abuse.
        """
        serializer = MediaFileUploadSerializer(
            data={"file": oversized_image_uploaded},
            context={"request": _mock_request(user)},
        )

        assert not serializer.is_valid()
        assert "file" in serializer.errors
        # Error should mention size limit
        error_msg = str(serializer.errors["file"])
        assert "25" in error_msg or "size" in error_msg.lower()

    def test_visibility_defaults_to_private(self, user, sample_jpeg_uploaded):
        """
        Visibility should default to private.

        Why it matters: Secure by default.
        """
        serializer = MediaFileUploadSerializer(
            data={"file": sample_jpeg_uploaded},
            context={"request": _mock_request(user)},
        )

        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data.get("visibility", "private") == "private"

    def test_visibility_can_be_set(self, user, sample_jpeg_uploaded):
        """
        Visibility can be explicitly set.

        Why it matters: Users can share files on upload.
        """
        serializer = MediaFileUploadSerializer(
            data={
                "file": sample_jpeg_uploaded,
                "visibility": "shared",
            },
            context={"request": _mock_request(user)},
        )

        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["visibility"] == "shared"

    def test_invalid_visibility_rejected(self, user, sample_jpeg_uploaded):
        """
        Invalid visibility values should be rejected.

        Why it matters: Enforce valid enum values.
        """
        serializer = MediaFileUploadSerializer(
            data={
                "file": sample_jpeg_uploaded,
                "visibility": "invalid_value",
            },
            context={"request": _mock_request(user)},
        )

        assert not serializer.is_valid()
        assert "visibility" in serializer.errors


@pytest.mark.django_db
class TestMediaFileUploadSerializerCreate:
    """Tests for MediaFileUploadSerializer.create()."""

    def test_create_returns_media_file(self, user, sample_jpeg_uploaded):
        """
        Create should return saved MediaFile instance.

        Why it matters: Serializer must create database record.
        """
        serializer = MediaFileUploadSerializer(
            data={"file": sample_jpeg_uploaded},
            context={"request": _mock_request(user)},
        )
        serializer.is_valid(raise_exception=True)

        media_file = serializer.save()

        assert media_file.pk is not None
        assert media_file.uploader == user
        assert media_file.media_type == "image"
        assert media_file.mime_type == "image/jpeg"

    def test_create_sets_original_filename(self, user, sample_jpeg_uploaded):
        """
        Create should preserve original filename.

        Why it matters: Users need to see their original filename.
        """
        serializer = MediaFileUploadSerializer(
            data={"file": sample_jpeg_uploaded},
            context={"request": _mock_request(user)},
        )
        serializer.is_valid(raise_exception=True)

        media_file = serializer.save()

        assert media_file.original_filename == "test_image.jpg"

    def test_create_calculates_file_size(self, user, sample_jpeg_uploaded):
        """
        Create should store file size.

        Why it matters: Size tracking for quota enforcement.
        """
        serializer = MediaFileUploadSerializer(
            data={"file": sample_jpeg_uploaded},
            context={"request": _mock_request(user)},
        )
        serializer.is_valid(raise_exception=True)

        media_file = serializer.save()

        assert media_file.file_size > 0
        assert media_file.file_size == sample_jpeg_uploaded.size


@pytest.mark.django_db
class TestStorageQuotaEnforcement:
    """Tests for storage quota checks in serializer."""

    def test_upload_rejected_when_quota_exceeded(
        self,
        authenticated_client_near_quota,
        user_near_quota,
        sample_jpeg_uploaded,
    ):
        """
        Upload should fail when user is near quota limit.

        Why it matters: Prevents exceeding allocated storage.
        """
        # User has only 1MB remaining, but we try to upload ~800 byte image
        # Need to artificially reduce remaining quota for test
        user_near_quota.profile.storage_quota_bytes = (
            user_near_quota.profile.total_storage_bytes + 100
        )
        user_near_quota.profile.save()

        serializer = MediaFileUploadSerializer(
            data={"file": sample_jpeg_uploaded},
            context={"request": _mock_request(user_near_quota)},
        )

        assert not serializer.is_valid()
        assert "file" in serializer.errors
        error_msg = str(serializer.errors["file"])
        assert "quota" in error_msg.lower() or "storage" in error_msg.lower()

    def test_upload_allowed_when_under_quota(self, user, sample_jpeg_uploaded):
        """
        Upload should succeed when user has sufficient quota.

        Why it matters: Normal users can upload files.
        """
        # User has 1GB quota with 0 used
        serializer = MediaFileUploadSerializer(
            data={"file": sample_jpeg_uploaded},
            context={"request": _mock_request(user)},
        )

        assert serializer.is_valid(), serializer.errors


@pytest.mark.django_db
class TestMediaFileSerializer:
    """Tests for MediaFileSerializer (output serializer)."""

    def test_serializes_media_file(self, user, sample_jpeg_uploaded):
        """
        Serializer should return all expected fields.

        Why it matters: API response must include needed data.
        """
        # Create a media file first
        upload_serializer = MediaFileUploadSerializer(
            data={"file": sample_jpeg_uploaded},
            context={"request": _mock_request(user)},
        )
        upload_serializer.is_valid(raise_exception=True)
        media_file = upload_serializer.save()

        # Serialize it
        serializer = MediaFileSerializer(
            media_file,
            context={"request": _mock_request(user)},
        )
        data = serializer.data

        assert "id" in data
        assert "original_filename" in data
        assert "media_type" in data
        assert "mime_type" in data
        assert "file_size" in data
        assert "visibility" in data
        assert "created_at" in data

    def test_includes_file_url(self, user, sample_jpeg_uploaded):
        """
        Serializer should include file URL.

        Why it matters: Clients need URL to access file.
        """
        upload_serializer = MediaFileUploadSerializer(
            data={"file": sample_jpeg_uploaded},
            context={"request": _mock_request(user)},
        )
        upload_serializer.is_valid(raise_exception=True)
        media_file = upload_serializer.save()

        serializer = MediaFileSerializer(
            media_file,
            context={"request": _mock_request(user)},
        )
        data = serializer.data

        assert "file_url" in data
        assert data["file_url"] is not None

    def test_excludes_sensitive_fields(self, user, sample_jpeg_uploaded):
        """
        Serializer should not expose internal fields.

        Why it matters: Don't leak processing internals to clients.
        """
        upload_serializer = MediaFileUploadSerializer(
            data={"file": sample_jpeg_uploaded},
            context={"request": _mock_request(user)},
        )
        upload_serializer.is_valid(raise_exception=True)
        media_file = upload_serializer.save()

        serializer = MediaFileSerializer(
            media_file,
            context={"request": _mock_request(user)},
        )
        data = serializer.data

        # Internal processing fields should not be exposed
        assert "processing_error" not in data
        assert "threat_name" not in data
        assert "is_deleted" not in data


# =============================================================================
# Helper Functions
# =============================================================================


class MockRequest:
    """Simple mock request for serializer context."""

    def __init__(self, user):
        self.user = user

    def build_absolute_uri(self, path):
        return f"http://testserver{path}"


def _mock_request(user):
    """Create a mock request with given user."""
    return MockRequest(user)
