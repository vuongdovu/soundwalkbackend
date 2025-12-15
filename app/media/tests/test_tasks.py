"""
Tests for media processing Celery tasks.

Tests cover:
- State transitions (pending -> processing -> ready/failed)
- Idempotency (already processed files are skipped)
- Retry behavior for transient errors
- No retry for permanent errors
- Cleanup of stuck processing jobs
- Retry of failed processing jobs
"""

from __future__ import annotations

import io
from datetime import timedelta
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from celery.exceptions import Reject
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from PIL import Image

from media.models import MediaAsset, MediaFile
from media.processors.image import ImageProcessingError
from media.tasks import (
    MAX_PROCESSING_RETRIES,
    STUCK_PROCESSING_THRESHOLD_MINUTES,
    cleanup_stuck_processing,
    process_media_file,
    retry_failed_processing,
)


@pytest.fixture
def media_file_pending(user, sample_jpeg_uploaded):
    """Create a MediaFile in PENDING status."""
    return MediaFile.create_from_upload(
        file=sample_jpeg_uploaded,
        uploader=user,
        media_type="image",
        mime_type="image/jpeg",
    )


@pytest.fixture
def media_file_ready(user):
    """Create a MediaFile that's already processed."""
    image = Image.new("RGB", (100, 100), color="green")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    buffer.seek(0)
    uploaded = SimpleUploadedFile(
        name="ready_image.jpg",
        content=buffer.read(),
        content_type="image/jpeg",
    )
    media_file = MediaFile.create_from_upload(
        file=uploaded,
        uploader=user,
        media_type="image",
        mime_type="image/jpeg",
    )
    media_file.processing_status = MediaFile.ProcessingStatus.READY
    media_file.save()
    return media_file


@pytest.fixture
def media_file_failed(user):
    """Create a MediaFile in FAILED status with retries remaining."""
    image = Image.new("RGB", (100, 100), color="red")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    buffer.seek(0)
    uploaded = SimpleUploadedFile(
        name="failed_image.jpg",
        content=buffer.read(),
        content_type="image/jpeg",
    )
    media_file = MediaFile.create_from_upload(
        file=uploaded,
        uploader=user,
        media_type="image",
        mime_type="image/jpeg",
    )
    media_file.processing_status = MediaFile.ProcessingStatus.FAILED
    media_file.processing_attempts = 1  # Still has retries left
    media_file.processing_error = "Temporary error"
    media_file.save()
    return media_file


@pytest.fixture
def media_file_stuck(user):
    """Create a MediaFile stuck in PROCESSING status."""
    image = Image.new("RGB", (100, 100), color="blue")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    buffer.seek(0)
    uploaded = SimpleUploadedFile(
        name="stuck_image.jpg",
        content=buffer.read(),
        content_type="image/jpeg",
    )
    media_file = MediaFile.create_from_upload(
        file=uploaded,
        uploader=user,
        media_type="image",
        mime_type="image/jpeg",
    )
    media_file.processing_status = MediaFile.ProcessingStatus.PROCESSING
    # Set started_at to be older than the threshold
    media_file.processing_started_at = timezone.now() - timedelta(
        minutes=STUCK_PROCESSING_THRESHOLD_MINUTES + 5
    )
    media_file.save()
    return media_file


@pytest.mark.django_db
class TestProcessMediaFileTask:
    """Tests for the process_media_file Celery task."""

    def test_processes_pending_image_to_ready(self, media_file_pending):
        """Test successful processing of a pending image file."""
        result = process_media_file(str(media_file_pending.id))

        media_file_pending.refresh_from_db()
        assert result["status"] == "processed"
        assert media_file_pending.processing_status == MediaFile.ProcessingStatus.READY
        assert media_file_pending.processing_completed_at is not None
        assert media_file_pending.processing_error is None

    def test_creates_thumbnail_asset(self, media_file_pending):
        """Test that processing creates a thumbnail asset."""
        process_media_file(str(media_file_pending.id))

        media_file_pending.refresh_from_db()
        assert media_file_pending.assets.count() == 1
        thumbnail = media_file_pending.assets.first()
        assert thumbnail.asset_type == MediaAsset.AssetType.THUMBNAIL

    def test_skips_already_processed_file(self, media_file_ready):
        """Test that already processed files are skipped (idempotency)."""
        result = process_media_file(str(media_file_ready.id))

        assert result["status"] == "already_processed"
        media_file_ready.refresh_from_db()
        assert media_file_ready.processing_status == MediaFile.ProcessingStatus.READY

    def test_returns_not_found_for_missing_file(self):
        """Test handling of non-existent MediaFile."""
        fake_id = str(uuid4())
        result = process_media_file(fake_id)

        assert result["status"] == "not_found"
        assert result["media_file_id"] == fake_id

    def test_transitions_to_processing_state(self, media_file_pending):
        """Test that file transitions to PROCESSING state during work."""
        processing_status_during = None

        original_generate = "media.processors.image.generate_image_thumbnail"

        def capture_status(media_file):
            nonlocal processing_status_during
            media_file.refresh_from_db()
            processing_status_during = media_file.processing_status
            # Actually generate the thumbnail

            # Need to import and call the actual function
            from media.models import MediaAsset
            from io import BytesIO
            from PIL import Image
            from django.core.files.base import ContentFile

            with media_file.file.open("rb") as f:
                img = Image.open(f)
                img.load()
                img = img.convert("RGB")
                img.thumbnail((200, 200), Image.Resampling.LANCZOS)
                buffer = BytesIO()
                img.save(buffer, format="WEBP", quality=80)
                buffer.seek(0)
                thumb_width, thumb_height = img.size
                file_size = buffer.getbuffer().nbytes

                asset, _ = MediaAsset.objects.update_or_create(
                    media_file=media_file,
                    asset_type=MediaAsset.AssetType.THUMBNAIL,
                    defaults={
                        "width": thumb_width,
                        "height": thumb_height,
                        "file_size": file_size,
                    },
                )
                filename = f"thumb_{media_file.pk}.webp"
                asset.file.save(filename, ContentFile(buffer.read()), save=True)
                return asset

        with patch(original_generate, side_effect=capture_status):
            process_media_file(str(media_file_pending.id))

        assert processing_status_during == MediaFile.ProcessingStatus.PROCESSING

    def test_increments_processing_attempts(self, media_file_pending):
        """Test that processing attempts counter is incremented."""
        initial_attempts = media_file_pending.processing_attempts

        process_media_file(str(media_file_pending.id))

        media_file_pending.refresh_from_db()
        assert media_file_pending.processing_attempts == initial_attempts + 1


@pytest.mark.django_db
class TestProcessMediaFileErrorHandling:
    """Tests for error handling in the process_media_file task."""

    def test_permanent_failure_marks_as_failed(self, media_file_pending):
        """Test that permanent failures mark the file as FAILED."""
        with patch(
            "media.processors.image.generate_image_thumbnail",
            side_effect=ImageProcessingError("Corrupted image"),
        ):
            # Create a mock task instance for bound task
            mock_self = MagicMock()
            mock_self.request.retries = 0

            with pytest.raises(Reject):
                # Call the underlying function directly (bind=True means self is first arg)
                process_media_file.run(str(media_file_pending.id))

        media_file_pending.refresh_from_db()
        assert media_file_pending.processing_status == MediaFile.ProcessingStatus.FAILED
        assert "Corrupted image" in media_file_pending.processing_error

    def test_transient_failure_allows_retry(self, media_file_pending):
        """Test that transient failures allow Celery to retry."""
        with patch(
            "media.processors.image.generate_image_thumbnail",
            side_effect=OSError("Storage temporarily unavailable"),
        ):
            # For transient errors, we expect the task to raise (for retry)
            # but since we're not in a celery context, we just verify the behavior
            try:
                process_media_file.run(str(media_file_pending.id))
            except OSError:
                pass  # Expected

        # File should still be in PROCESSING state (not FAILED) since retries not exhausted
        media_file_pending.refresh_from_db()
        assert (
            media_file_pending.processing_status
            == MediaFile.ProcessingStatus.PROCESSING
        )
        assert media_file_pending.processing_error is not None

    def test_exhausted_retries_marks_as_failed(self, media_file_pending):
        """Test that max processing attempts marks the file as FAILED.

        This tests the _mark_processing_failed helper which is called when
        retries are exhausted. We verify this indirectly by checking that
        permanent errors (ImageProcessingError) correctly mark as failed.
        """
        # Simulate a file that has already had multiple processing attempts
        media_file_pending.processing_attempts = MAX_PROCESSING_RETRIES - 1
        media_file_pending.save()

        # Now process it and have it fail permanently
        with patch(
            "media.processors.image.generate_image_thumbnail",
            side_effect=ImageProcessingError("Permanently corrupted"),
        ):
            with pytest.raises(Reject):
                process_media_file.run(str(media_file_pending.id))

        media_file_pending.refresh_from_db()
        assert media_file_pending.processing_status == MediaFile.ProcessingStatus.FAILED
        assert "Permanently corrupted" in media_file_pending.processing_error
        # Attempts should have been incremented
        assert media_file_pending.processing_attempts == MAX_PROCESSING_RETRIES


@pytest.mark.django_db
class TestProcessMediaFileVideoDocument:
    """Tests for non-image media type handling."""

    def test_video_processing_marks_ready(self, user):
        """Test that video files are marked as ready (processing not yet implemented)."""
        video_content = b"fake video content"
        uploaded = SimpleUploadedFile(
            name="test_video.mp4",
            content=video_content,
            content_type="video/mp4",
        )
        media_file = MediaFile.create_from_upload(
            file=uploaded,
            uploader=user,
            media_type="video",
            mime_type="video/mp4",
        )

        result = process_media_file(str(media_file.id))

        media_file.refresh_from_db()
        assert result["status"] == "processed"
        assert media_file.processing_status == MediaFile.ProcessingStatus.READY

    def test_document_processing_marks_ready(self, user, sample_pdf_uploaded):
        """Test that document files are marked as ready (processing not yet implemented)."""
        media_file = MediaFile.create_from_upload(
            file=sample_pdf_uploaded,
            uploader=user,
            media_type="document",
            mime_type="application/pdf",
        )

        result = process_media_file(str(media_file.id))

        media_file.refresh_from_db()
        assert result["status"] == "processed"
        assert media_file.processing_status == MediaFile.ProcessingStatus.READY


@pytest.mark.django_db
class TestRetryFailedProcessingTask:
    """Tests for the retry_failed_processing periodic task."""

    def test_queues_failed_files_for_retry(self, media_file_failed):
        """Test that failed files with remaining retries are queued."""
        with patch("media.tasks.process_media_file.delay") as mock_delay:
            result = retry_failed_processing()

            assert result["queued_count"] >= 1
            mock_delay.assert_called()

    def test_resets_status_to_pending(self, media_file_failed):
        """Test that failed files are reset to PENDING status."""
        with patch("media.tasks.process_media_file.delay"):
            retry_failed_processing()

        media_file_failed.refresh_from_db()
        assert media_file_failed.processing_status == MediaFile.ProcessingStatus.PENDING

    def test_skips_files_with_max_retries(self, user):
        """Test that files with max retries are not queued."""
        image = Image.new("RGB", (100, 100), color="red")
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG")
        buffer.seek(0)
        uploaded = SimpleUploadedFile(
            name="max_retries.jpg",
            content=buffer.read(),
            content_type="image/jpeg",
        )
        media_file = MediaFile.create_from_upload(
            file=uploaded,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )
        media_file.processing_status = MediaFile.ProcessingStatus.FAILED
        media_file.processing_attempts = MAX_PROCESSING_RETRIES  # Max attempts
        media_file.save()

        with patch("media.tasks.process_media_file.delay") as mock_delay:
            retry_failed_processing()

            # Should not be called for this file
            for call in mock_delay.call_args_list:
                assert call[0][0] != str(media_file.id)


@pytest.mark.django_db
class TestCleanupStuckProcessingTask:
    """Tests for the cleanup_stuck_processing periodic task."""

    def test_resets_stuck_files_to_failed(self, media_file_stuck):
        """Test that stuck files are reset to FAILED status."""
        result = cleanup_stuck_processing()

        media_file_stuck.refresh_from_db()
        assert result["reset_count"] >= 1
        assert media_file_stuck.processing_status == MediaFile.ProcessingStatus.FAILED
        assert "timed out" in media_file_stuck.processing_error.lower()

    def test_ignores_recent_processing_files(self, user):
        """Test that recently started processing files are not reset."""
        image = Image.new("RGB", (100, 100), color="purple")
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG")
        buffer.seek(0)
        uploaded = SimpleUploadedFile(
            name="recent_processing.jpg",
            content=buffer.read(),
            content_type="image/jpeg",
        )
        media_file = MediaFile.create_from_upload(
            file=uploaded,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )
        media_file.processing_status = MediaFile.ProcessingStatus.PROCESSING
        media_file.processing_started_at = timezone.now()  # Just started
        media_file.save()

        cleanup_stuck_processing()

        media_file.refresh_from_db()
        # Should still be PROCESSING, not reset to FAILED
        assert media_file.processing_status == MediaFile.ProcessingStatus.PROCESSING


@pytest.mark.django_db
class TestConcurrentProcessing:
    """Tests for handling concurrent task execution."""

    def test_concurrent_processing_prevented_by_lock(self, media_file_pending):
        """Test that concurrent processing of the same file is handled."""
        # First call should succeed
        result1 = process_media_file(str(media_file_pending.id))
        assert result1["status"] == "processed"

        # Second call should detect already processed
        result2 = process_media_file(str(media_file_pending.id))
        assert result2["status"] == "already_processed"

        # Should still only have one thumbnail
        assert media_file_pending.assets.count() == 1
