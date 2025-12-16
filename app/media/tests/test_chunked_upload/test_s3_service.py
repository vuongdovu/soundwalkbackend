"""
Tests for S3ChunkedUploadService.

Following TDD: These tests use mocked boto3 to test S3-specific behavior.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from media.models import MediaFile, UploadSession

# Skip all tests in this module if boto3 is not installed
pytest.importorskip("boto3")

from media.services.chunked_upload.s3 import S3ChunkedUploadService

if TYPE_CHECKING:
    from authentication.models import User

pytestmark = pytest.mark.django_db


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_s3_client():
    """Create a mock boto3 S3 client."""
    client = MagicMock()

    # Default successful responses
    client.create_multipart_upload.return_value = {
        "UploadId": "mock-upload-id-12345",
    }

    client.generate_presigned_url.return_value = (
        "https://bucket.s3.amazonaws.com/path/to/file?presigned=yes&expires=3600"
    )

    client.complete_multipart_upload.return_value = {
        "ETag": '"final-etag-hash"',
        "Location": "https://bucket.s3.amazonaws.com/path/to/file",
    }

    client.abort_multipart_upload.return_value = {}

    return client


@pytest.fixture
def mock_storage():
    """Create a mock S3 storage backend."""
    storage = MagicMock()
    storage.bucket_name = "test-bucket"
    storage.location = "media"
    storage.bucket = MagicMock()  # Indicates S3 storage
    return storage


@pytest.fixture
def s3_service(mock_s3_client, mock_storage):
    """Create S3ChunkedUploadService with mocked dependencies."""
    with patch("media.services.chunked_upload.s3.boto3") as mock_boto3:
        mock_boto3.client.return_value = mock_s3_client
        with patch("media.services.chunked_upload.s3.default_storage", mock_storage):
            service = S3ChunkedUploadService(
                bucket_name="test-bucket",
                presigned_url_expiry=3600,
            )
            service._s3_client = mock_s3_client
            yield service


# =============================================================================
# Session Creation Tests
# =============================================================================


class TestS3SessionCreation:
    """Tests for S3ChunkedUploadService.create_session()."""

    def test_create_session_calls_create_multipart_upload(
        self,
        upload_user: "User",
        mock_s3_client,
    ) -> None:
        """S3 multipart upload is initiated."""
        with patch("media.services.chunked_upload.s3.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_s3_client
            with patch(
                "media.services.chunked_upload.s3.default_storage"
            ) as mock_storage:
                mock_storage.bucket_name = "test-bucket"
                mock_storage.location = "media"
                mock_storage.bucket = MagicMock()

                service = S3ChunkedUploadService(bucket_name="test-bucket")
                service._s3_client = mock_s3_client

                result = service.create_session(
                    user=upload_user,
                    filename="test_video.mp4",
                    file_size=15 * 1024 * 1024,
                    mime_type="video/mp4",
                    media_type="video",
                )

                assert result.success
                mock_s3_client.create_multipart_upload.assert_called_once()

    def test_create_session_stores_s3_upload_id(
        self,
        upload_user: "User",
        mock_s3_client,
    ) -> None:
        """Session stores the S3 upload ID for later completion."""
        with patch("media.services.chunked_upload.s3.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_s3_client
            with patch(
                "media.services.chunked_upload.s3.default_storage"
            ) as mock_storage:
                mock_storage.bucket_name = "test-bucket"
                mock_storage.location = "media"
                mock_storage.bucket = MagicMock()

                service = S3ChunkedUploadService(bucket_name="test-bucket")
                service._s3_client = mock_s3_client

                result = service.create_session(
                    user=upload_user,
                    filename="test.mp4",
                    file_size=10 * 1024 * 1024,
                    mime_type="video/mp4",
                    media_type="video",
                )

                assert result.success
                session = result.data
                assert session.s3_upload_id == "mock-upload-id-12345"
                assert session.s3_key is not None
                assert session.backend == UploadSession.Backend.S3

    def test_create_session_generates_s3_key(
        self,
        upload_user: "User",
        mock_s3_client,
    ) -> None:
        """Session generates a proper S3 key for the upload."""
        with patch("media.services.chunked_upload.s3.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_s3_client
            with patch(
                "media.services.chunked_upload.s3.default_storage"
            ) as mock_storage:
                mock_storage.bucket_name = "test-bucket"
                mock_storage.location = "media"
                mock_storage.bucket = MagicMock()

                service = S3ChunkedUploadService(bucket_name="test-bucket")
                service._s3_client = mock_s3_client

                result = service.create_session(
                    user=upload_user,
                    filename="test.mp4",
                    file_size=10 * 1024 * 1024,
                    mime_type="video/mp4",
                    media_type="video",
                )

                assert result.success
                session = result.data
                # S3 key should include session ID and filename
                assert "test.mp4" in session.s3_key

    def test_create_session_checks_quota(
        self,
        user_at_quota: "User",
        mock_s3_client,
    ) -> None:
        """Quota exceeded prevents session creation, no S3 call made."""
        with patch("media.services.chunked_upload.s3.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_s3_client
            with patch(
                "media.services.chunked_upload.s3.default_storage"
            ) as mock_storage:
                mock_storage.bucket_name = "test-bucket"
                mock_storage.location = "media"
                mock_storage.bucket = MagicMock()

                service = S3ChunkedUploadService(bucket_name="test-bucket")
                service._s3_client = mock_s3_client

                result = service.create_session(
                    user=user_at_quota,
                    filename="big.mp4",
                    file_size=50 * 1024 * 1024,
                    mime_type="video/mp4",
                    media_type="video",
                )

                assert not result.success
                assert "quota" in result.error.lower()
                # S3 should NOT be called if quota check fails
                mock_s3_client.create_multipart_upload.assert_not_called()


# =============================================================================
# Chunk Target Tests
# =============================================================================


class TestS3ChunkTarget:
    """Tests for S3ChunkedUploadService.get_chunk_target()."""

    def test_get_target_returns_presigned_url(
        self,
        s3_session: UploadSession,
        mock_s3_client,
    ) -> None:
        """S3 backend returns presigned URL with direct=True."""
        with patch("media.services.chunked_upload.s3.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_s3_client
            with patch(
                "media.services.chunked_upload.s3.default_storage"
            ) as mock_storage:
                mock_storage.bucket_name = "test-bucket"
                mock_storage.bucket = MagicMock()

                service = S3ChunkedUploadService(bucket_name="test-bucket")
                service._s3_client = mock_s3_client

                result = service.get_chunk_target(s3_session, part_number=1)

                assert result.success
                target = result.data
                assert target.direct is True
                assert (
                    "s3" in target.upload_url.lower()
                    or "presigned" in target.upload_url.lower()
                )
                assert target.method == "PUT"
                mock_s3_client.generate_presigned_url.assert_called_once()

    def test_get_target_includes_expiry(
        self,
        s3_session: UploadSession,
        mock_s3_client,
    ) -> None:
        """Target includes expiration time for presigned URL."""
        with patch("media.services.chunked_upload.s3.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_s3_client
            with patch(
                "media.services.chunked_upload.s3.default_storage"
            ) as mock_storage:
                mock_storage.bucket_name = "test-bucket"
                mock_storage.bucket = MagicMock()

                service = S3ChunkedUploadService(
                    bucket_name="test-bucket",
                    presigned_url_expiry=3600,
                )
                service._s3_client = mock_s3_client

                result = service.get_chunk_target(s3_session, part_number=1)

                assert result.success
                assert result.data.expires_in == 3600

    def test_get_target_expired_session(
        self,
        expired_session: UploadSession,
        mock_s3_client,
    ) -> None:
        """Expired session returns error."""
        with patch("media.services.chunked_upload.s3.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_s3_client
            with patch(
                "media.services.chunked_upload.s3.default_storage"
            ) as mock_storage:
                mock_storage.bucket_name = "test-bucket"
                mock_storage.bucket = MagicMock()

                service = S3ChunkedUploadService(bucket_name="test-bucket")
                service._s3_client = mock_s3_client

                # Make sure session has S3 backend
                expired_session.backend = UploadSession.Backend.S3
                expired_session.s3_key = "test/key"
                expired_session.s3_upload_id = "test-upload-id"
                expired_session.save()

                result = service.get_chunk_target(expired_session, part_number=1)

                assert not result.success
                assert "expired" in result.error.lower()


# =============================================================================
# Part Completion Tests
# =============================================================================


class TestS3RecordCompletedPart:
    """Tests for S3ChunkedUploadService.record_completed_part()."""

    def test_record_part_stores_etag(
        self,
        s3_session: UploadSession,
        mock_s3_client,
    ) -> None:
        """Part completion stores ETag for later CompleteMultipartUpload."""
        with patch("media.services.chunked_upload.s3.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_s3_client
            with patch(
                "media.services.chunked_upload.s3.default_storage"
            ) as mock_storage:
                mock_storage.bucket_name = "test-bucket"
                mock_storage.bucket = MagicMock()

                service = S3ChunkedUploadService(bucket_name="test-bucket")
                service._s3_client = mock_s3_client

                result = service.record_completed_part(
                    session=s3_session,
                    part_number=1,
                    etag='"abc123"',
                    size=5 * 1024 * 1024,
                )

                assert result.success
                s3_session.refresh_from_db()
                assert len(s3_session.parts_completed) == 1
                assert s3_session.parts_completed[0]["etag"] == '"abc123"'

    def test_record_part_updates_progress(
        self,
        s3_session: UploadSession,
        mock_s3_client,
    ) -> None:
        """Progress is updated when parts are recorded."""
        with patch("media.services.chunked_upload.s3.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_s3_client
            with patch(
                "media.services.chunked_upload.s3.default_storage"
            ) as mock_storage:
                mock_storage.bucket_name = "test-bucket"
                mock_storage.bucket = MagicMock()

                service = S3ChunkedUploadService(bucket_name="test-bucket")
                service._s3_client = mock_s3_client

                result = service.record_completed_part(
                    session=s3_session,
                    part_number=1,
                    etag='"abc123"',
                    size=5 * 1024 * 1024,
                )

                assert result.success
                assert result.data.bytes_received == 5 * 1024 * 1024
                assert result.data.parts_completed == 1

    def test_record_part_is_complete_when_done(
        self,
        s3_session: UploadSession,
        mock_s3_client,
    ) -> None:
        """is_complete=True when all bytes received."""
        with patch("media.services.chunked_upload.s3.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_s3_client
            with patch(
                "media.services.chunked_upload.s3.default_storage"
            ) as mock_storage:
                mock_storage.bucket_name = "test-bucket"
                mock_storage.bucket = MagicMock()

                service = S3ChunkedUploadService(bucket_name="test-bucket")
                service._s3_client = mock_s3_client

                # s3_session is 15MB = 3 x 5MB parts
                service.record_completed_part(s3_session, 1, '"a"', 5 * 1024 * 1024)
                service.record_completed_part(s3_session, 2, '"b"', 5 * 1024 * 1024)
                result = service.record_completed_part(
                    s3_session, 3, '"c"', 5 * 1024 * 1024
                )

                assert result.success
                assert result.data.is_complete is True


# =============================================================================
# Finalization Tests
# =============================================================================


class TestS3Finalization:
    """Tests for S3ChunkedUploadService.finalize_upload()."""

    def test_finalize_calls_complete_multipart_upload(
        self,
        s3_session: UploadSession,
        mock_s3_client,
    ) -> None:
        """CompleteMultipartUpload is called with all parts."""
        with patch("media.services.chunked_upload.s3.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_s3_client
            with patch(
                "media.services.chunked_upload.s3.default_storage"
            ) as mock_storage:
                mock_storage.bucket_name = "test-bucket"
                mock_storage.bucket = MagicMock()

                service = S3ChunkedUploadService(bucket_name="test-bucket")
                service._s3_client = mock_s3_client

                # Record all parts
                service.record_completed_part(s3_session, 1, '"a"', 5 * 1024 * 1024)
                service.record_completed_part(s3_session, 2, '"b"', 5 * 1024 * 1024)
                service.record_completed_part(s3_session, 3, '"c"', 5 * 1024 * 1024)

                # Mock the chain import
                with patch("media.services.chunked_upload.s3.chain"):
                    result = service.finalize_upload(s3_session)

                    assert result.success
                    mock_s3_client.complete_multipart_upload.assert_called_once()

    def test_finalize_creates_media_file(
        self,
        s3_session: UploadSession,
        mock_s3_client,
    ) -> None:
        """MediaFile is created pointing to S3 location."""
        with patch("media.services.chunked_upload.s3.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_s3_client
            with patch(
                "media.services.chunked_upload.s3.default_storage"
            ) as mock_storage:
                mock_storage.bucket_name = "test-bucket"
                mock_storage.bucket = MagicMock()

                service = S3ChunkedUploadService(bucket_name="test-bucket")
                service._s3_client = mock_s3_client

                # Record all parts
                service.record_completed_part(s3_session, 1, '"a"', 5 * 1024 * 1024)
                service.record_completed_part(s3_session, 2, '"b"', 5 * 1024 * 1024)
                service.record_completed_part(s3_session, 3, '"c"', 5 * 1024 * 1024)

                with patch("media.services.chunked_upload.s3.chain"):
                    result = service.finalize_upload(s3_session)

                    assert result.success
                    media_file = result.data
                    assert isinstance(media_file, MediaFile)
                    assert media_file.original_filename == s3_session.filename

    def test_finalize_requires_all_parts(
        self,
        s3_session: UploadSession,
        mock_s3_client,
    ) -> None:
        """Finalization fails if parts are missing."""
        with patch("media.services.chunked_upload.s3.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_s3_client
            with patch(
                "media.services.chunked_upload.s3.default_storage"
            ) as mock_storage:
                mock_storage.bucket_name = "test-bucket"
                mock_storage.bucket = MagicMock()

                service = S3ChunkedUploadService(bucket_name="test-bucket")
                service._s3_client = mock_s3_client

                # Only record 1 of 3 parts
                service.record_completed_part(s3_session, 1, '"a"', 5 * 1024 * 1024)

                result = service.finalize_upload(s3_session)

                assert not result.success
                assert (
                    "missing" in result.error.lower()
                    or "incomplete" in result.error.lower()
                )

    def test_finalize_updates_quota(
        self,
        s3_session: UploadSession,
        mock_s3_client,
    ) -> None:
        """User storage quota is updated on finalization."""
        with patch("media.services.chunked_upload.s3.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_s3_client
            with patch(
                "media.services.chunked_upload.s3.default_storage"
            ) as mock_storage:
                mock_storage.bucket_name = "test-bucket"
                mock_storage.bucket = MagicMock()

                service = S3ChunkedUploadService(bucket_name="test-bucket")
                service._s3_client = mock_s3_client

                initial_storage = s3_session.uploader.profile.total_storage_bytes

                # Record all parts
                service.record_completed_part(s3_session, 1, '"a"', 5 * 1024 * 1024)
                service.record_completed_part(s3_session, 2, '"b"', 5 * 1024 * 1024)
                service.record_completed_part(s3_session, 3, '"c"', 5 * 1024 * 1024)

                with patch("media.services.chunked_upload.s3.chain"):
                    service.finalize_upload(s3_session)

                s3_session.uploader.profile.refresh_from_db()
                expected = initial_storage + s3_session.file_size
                assert s3_session.uploader.profile.total_storage_bytes == expected


# =============================================================================
# Abort Tests
# =============================================================================


class TestS3Abort:
    """Tests for S3ChunkedUploadService.abort_upload()."""

    def test_abort_calls_abort_multipart_upload(
        self,
        s3_session: UploadSession,
        mock_s3_client,
    ) -> None:
        """AbortMultipartUpload is called to clean up S3."""
        with patch("media.services.chunked_upload.s3.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_s3_client
            with patch(
                "media.services.chunked_upload.s3.default_storage"
            ) as mock_storage:
                mock_storage.bucket_name = "test-bucket"
                mock_storage.bucket = MagicMock()

                service = S3ChunkedUploadService(bucket_name="test-bucket")
                service._s3_client = mock_s3_client

                result = service.abort_upload(s3_session)

                assert result.success
                mock_s3_client.abort_multipart_upload.assert_called_once()

    def test_abort_marks_session_failed(
        self,
        s3_session: UploadSession,
        mock_s3_client,
    ) -> None:
        """Session is marked as FAILED on abort."""
        with patch("media.services.chunked_upload.s3.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_s3_client
            with patch(
                "media.services.chunked_upload.s3.default_storage"
            ) as mock_storage:
                mock_storage.bucket_name = "test-bucket"
                mock_storage.bucket = MagicMock()

                service = S3ChunkedUploadService(bucket_name="test-bucket")
                service._s3_client = mock_s3_client

                result = service.abort_upload(s3_session)

                assert result.success
                s3_session.refresh_from_db()
                assert s3_session.status == UploadSession.Status.FAILED

    def test_abort_completed_session_fails(
        self,
        completed_session: UploadSession,
        mock_s3_client,
    ) -> None:
        """Cannot abort a completed session."""
        with patch("media.services.chunked_upload.s3.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_s3_client
            with patch(
                "media.services.chunked_upload.s3.default_storage"
            ) as mock_storage:
                mock_storage.bucket_name = "test-bucket"
                mock_storage.bucket = MagicMock()

                service = S3ChunkedUploadService(bucket_name="test-bucket")
                service._s3_client = mock_s3_client

                # Make it look like an S3 session
                completed_session.backend = UploadSession.Backend.S3
                completed_session.s3_key = "test/key"
                completed_session.s3_upload_id = "test-id"
                completed_session.save()

                result = service.abort_upload(completed_session)

                assert not result.success
                # S3 abort should not be called
                mock_s3_client.abort_multipart_upload.assert_not_called()


# =============================================================================
# receive_chunk Tests
# =============================================================================


class TestS3ReceiveChunk:
    """Tests for S3ChunkedUploadService.receive_chunk()."""

    def test_receive_chunk_not_supported(
        self,
        s3_session: UploadSession,
        mock_s3_client,
    ) -> None:
        """S3 service doesn't support receive_chunk (clients upload directly)."""
        with patch("media.services.chunked_upload.s3.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_s3_client
            with patch(
                "media.services.chunked_upload.s3.default_storage"
            ) as mock_storage:
                mock_storage.bucket_name = "test-bucket"
                mock_storage.bucket = MagicMock()

                service = S3ChunkedUploadService(bucket_name="test-bucket")
                service._s3_client = mock_s3_client

                result = service.receive_chunk(s3_session, 1, b"chunk data")

                # Should return failure - S3 clients upload directly
                assert not result.success
                assert (
                    "not supported" in result.error.lower()
                    or "s3" in result.error.lower()
                )
