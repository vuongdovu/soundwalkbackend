"""
Tests for FileDeliveryService.

These tests verify:
- URL generation for local and S3 storage
- Content-Disposition handling (attachment vs inline)
- File response creation

TDD: Write these tests first, then implement FileDeliveryService to pass them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings
from django.http import FileResponse, HttpResponse

from media.models import MediaFile
from media.services.delivery import FileDeliveryService

if TYPE_CHECKING:
    from authentication.models import User


@pytest.mark.django_db
class TestFileDeliveryServiceLocalStorage:
    """Tests for local FileSystemStorage delivery."""

    def test_get_download_url_returns_protected_url(
        self,
        media_file_for_processing: MediaFile,
    ):
        """
        get_download_url should return URL to protected endpoint.

        Why it matters: Files must go through access control.
        """
        url = FileDeliveryService.get_download_url(media_file_for_processing)

        # Should point to protected download endpoint
        assert f"/api/v1/media/files/{media_file_for_processing.id}/download/" in url

    def test_get_view_url_returns_protected_url(
        self,
        media_file_for_processing: MediaFile,
    ):
        """
        get_view_url should return URL to protected view endpoint.

        Why it matters: Files must go through access control.
        """
        url = FileDeliveryService.get_view_url(media_file_for_processing)

        # Should point to protected view endpoint
        assert f"/api/v1/media/files/{media_file_for_processing.id}/view/" in url

    def test_serve_file_response_debug_mode(
        self,
        media_file_for_processing: MediaFile,
    ):
        """
        In DEBUG mode, serve_file_response should return FileResponse.

        Why it matters: Django serves files directly in development.
        """
        with patch.object(settings, "DEBUG", True):
            response = FileDeliveryService.serve_file_response(
                media_file_for_processing,
                as_attachment=True,
            )

            assert isinstance(response, FileResponse)
            assert response.get("Content-Disposition") is not None
            assert "attachment" in response.get("Content-Disposition")

    def test_serve_file_response_production_mode(
        self,
        media_file_for_processing: MediaFile,
    ):
        """
        In production mode, serve_file_response should return X-Accel-Redirect.

        Why it matters: nginx serves files for performance.
        """
        with patch.object(settings, "DEBUG", False):
            response = FileDeliveryService.serve_file_response(
                media_file_for_processing,
                as_attachment=True,
            )

            assert isinstance(response, HttpResponse)
            assert response.get("X-Accel-Redirect") is not None
            assert response.get("Content-Disposition") is not None

    def test_serve_file_response_inline(
        self,
        media_file_for_processing: MediaFile,
    ):
        """
        serve_file_response with as_attachment=False should use inline disposition.

        Why it matters: Browser displays file instead of downloading.
        """
        with patch.object(settings, "DEBUG", True):
            response = FileDeliveryService.serve_file_response(
                media_file_for_processing,
                as_attachment=False,
            )

            disposition = response.get("Content-Disposition", "")
            # Either no disposition (browser default) or explicit inline
            assert "attachment" not in disposition

    def test_serve_file_response_uses_original_filename(
        self,
        media_file_for_processing: MediaFile,
    ):
        """
        serve_file_response should use original_filename in Content-Disposition.

        Why it matters: Users see meaningful filenames when downloading.
        """
        with patch.object(settings, "DEBUG", True):
            response = FileDeliveryService.serve_file_response(
                media_file_for_processing,
                as_attachment=True,
            )

            disposition = response.get("Content-Disposition", "")
            assert media_file_for_processing.original_filename in disposition


@pytest.mark.django_db
class TestFileDeliveryServiceS3Storage:
    """Tests for S3 storage delivery."""

    def test_is_s3_storage_false_for_local(self):
        """
        is_s3_storage should return False for FileSystemStorage.

        Why it matters: Service needs to detect storage backend.
        """
        assert not FileDeliveryService.is_s3_storage()

    @patch("media.services.delivery.default_storage")
    def test_is_s3_storage_true_for_s3(self, mock_storage):
        """
        is_s3_storage should return True for S3Boto3Storage.

        Why it matters: Service needs to detect storage backend.
        """
        # Mock S3 storage by checking for 'bucket' attribute
        mock_storage.bucket = MagicMock()

        assert FileDeliveryService.is_s3_storage()

    @patch("media.services.delivery.default_storage")
    def test_get_download_url_s3_returns_presigned(
        self,
        mock_storage,
        media_file_for_processing: MediaFile,
    ):
        """
        For S3 storage, get_download_url should return presigned URL.

        Why it matters: Direct S3 access with expiring URL.
        """
        # Mock S3 storage with full boto3 client chain
        mock_storage.bucket = MagicMock()
        mock_storage.bucket_name = "test-bucket"
        mock_client = MagicMock()
        mock_client.generate_presigned_url.return_value = (
            "https://bucket.s3.amazonaws.com/file.jpg?AWSAccessKeyId=..."
        )
        mock_storage.connection.meta.client = mock_client

        url = FileDeliveryService.get_download_url(
            media_file_for_processing,
            expires_in=3600,
        )

        # Should be S3 URL
        assert "s3.amazonaws.com" in url
        assert mock_client.generate_presigned_url.called

    @patch("media.services.delivery.default_storage")
    def test_get_view_url_s3_returns_presigned_inline(
        self,
        mock_storage,
        media_file_for_processing: MediaFile,
    ):
        """
        For S3 storage, get_view_url should return presigned URL with inline disposition.

        Why it matters: Browser displays file instead of downloading.
        """
        # Mock S3 storage
        mock_storage.bucket = MagicMock()
        mock_storage.url.return_value = "https://bucket.s3.amazonaws.com/file.jpg"

        url = FileDeliveryService.get_view_url(
            media_file_for_processing,
            expires_in=3600,
        )

        # URL should be generated
        assert url is not None


@pytest.mark.django_db
class TestFileDeliveryServiceContentType:
    """Tests for Content-Type handling."""

    def test_serve_file_response_sets_content_type(
        self,
        media_file_for_processing: MediaFile,
    ):
        """
        serve_file_response should set Content-Type from MediaFile.

        Why it matters: Browser needs correct MIME type.
        """
        with patch.object(settings, "DEBUG", True):
            response = FileDeliveryService.serve_file_response(
                media_file_for_processing,
                as_attachment=True,
            )

            # Content-Type should match file's mime_type
            content_type = response.get("Content-Type", "")
            assert media_file_for_processing.mime_type in content_type


@pytest.mark.django_db
class TestFileDeliveryServiceEdgeCases:
    """Tests for edge cases and error handling."""

    def test_serve_file_missing_file_raises_error(
        self,
        user: "User",
        db,
    ):
        """
        serve_file_response should handle missing file gracefully.

        Why it matters: Files might be deleted from storage.
        """
        # Create MediaFile record without actual file
        media_file = MediaFile.objects.create(
            file="nonexistent/path/file.jpg",
            original_filename="test.jpg",
            media_type=MediaFile.MediaType.IMAGE,
            mime_type="image/jpeg",
            file_size=1000,
            uploader=user,
            scan_status=MediaFile.ScanStatus.CLEAN,
        )

        with patch.object(settings, "DEBUG", True):
            with pytest.raises(FileNotFoundError):
                FileDeliveryService.serve_file_response(
                    media_file,
                    as_attachment=True,
                )

    def test_get_download_url_with_special_characters_in_filename(
        self,
        user: "User",
        sample_jpeg_uploaded,
        db,
    ):
        """
        get_download_url should handle special characters in filename.

        Why it matters: Filenames can have spaces, unicode, etc.
        """
        from pathlib import Path
        from django.conf import settings as django_settings

        # Create directory for test file
        media_root = Path(django_settings.MEDIA_ROOT)
        test_dir = media_root / "test_special"
        test_dir.mkdir(parents=True, exist_ok=True)

        # Write file to disk
        file_path = test_dir / "test_file.jpg"
        file_path.write_bytes(sample_jpeg_uploaded.read())
        sample_jpeg_uploaded.seek(0)

        media_file = MediaFile.objects.create(
            file="test_special/test_file.jpg",
            original_filename="my file (1) - résumé.jpg",  # Special characters
            media_type=MediaFile.MediaType.IMAGE,
            mime_type="image/jpeg",
            file_size=sample_jpeg_uploaded.size,
            uploader=user,
            scan_status=MediaFile.ScanStatus.CLEAN,
        )

        try:
            url = FileDeliveryService.get_download_url(media_file)
            assert url is not None
            assert str(media_file.id) in url
        finally:
            # Cleanup
            import shutil

            if test_dir.exists():
                shutil.rmtree(test_dir, ignore_errors=True)
