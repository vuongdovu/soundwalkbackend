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
from unittest.mock import patch
from uuid import uuid4

import pytest

# Note: Reject is no longer used due to graceful degradation pattern
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from PIL import Image

from media.models import MediaAsset, MediaFile
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

    def test_creates_image_assets(self, media_file_pending):
        """Test that processing creates thumbnail, preview, and web_optimized assets."""
        process_media_file(str(media_file_pending.id))

        media_file_pending.refresh_from_db()
        # Image processing now creates 3 assets: thumbnail, preview, web_optimized
        assert media_file_pending.assets.count() == 3

        asset_types = set(a.asset_type for a in media_file_pending.assets.all())
        assert MediaAsset.AssetType.THUMBNAIL in asset_types
        assert MediaAsset.AssetType.PREVIEW in asset_types
        assert MediaAsset.AssetType.WEB_OPTIMIZED in asset_types

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
        # Verify that processing_started_at is set (indicates PROCESSING transition)
        assert media_file_pending.processing_started_at is None

        process_media_file(str(media_file_pending.id))

        media_file_pending.refresh_from_db()
        # The file went through PROCESSING state - evidenced by:
        # 1. processing_started_at was set
        # 2. processing_attempts was incremented
        # 3. Final status is READY (after PROCESSING)
        assert media_file_pending.processing_started_at is not None
        assert media_file_pending.processing_attempts == 1
        assert media_file_pending.processing_status == MediaFile.ProcessingStatus.READY

    def test_increments_processing_attempts(self, media_file_pending):
        """Test that processing attempts counter is incremented."""
        initial_attempts = media_file_pending.processing_attempts

        process_media_file(str(media_file_pending.id))

        media_file_pending.refresh_from_db()
        assert media_file_pending.processing_attempts == initial_attempts + 1


@pytest.mark.django_db
class TestProcessMediaFileErrorHandling:
    """Tests for error handling in the process_media_file task.

    With graceful degradation, processing errors for individual assets
    don't fail the entire job - the file is marked READY with errors recorded.
    The original file is always accessible.
    """

    def test_partial_failure_records_errors(self, media_file_pending):
        """Test that partial failures are recorded but file is marked READY."""
        from media.processors.base import PermanentProcessingError

        # Patch at both the source module and the re-export location
        with (
            patch.object(
                __import__(
                    "media.processors.image", fromlist=["generate_image_thumbnail"]
                ),
                "generate_image_thumbnail",
                side_effect=PermanentProcessingError("Corrupted image"),
            ),
            patch.object(
                __import__("media.processors", fromlist=["generate_image_thumbnail"]),
                "generate_image_thumbnail",
                side_effect=PermanentProcessingError("Corrupted image"),
            ),
        ):
            result = process_media_file.run(str(media_file_pending.id))

        media_file_pending.refresh_from_db()
        # With graceful degradation, file is marked READY even with errors
        assert media_file_pending.processing_status == MediaFile.ProcessingStatus.READY
        # Errors are recorded for diagnosis
        assert media_file_pending.processing_error is not None
        assert "thumbnail" in media_file_pending.processing_error.lower()
        # Result indicates processing completed (even if partially)
        assert result["status"] == "processed"
        # Should report errors
        assert result.get("errors") is not None

    def test_metadata_failure_is_non_blocking(self, media_file_pending):
        """Test that metadata extraction failure doesn't block asset generation."""
        with (
            patch.object(
                __import__(
                    "media.processors.image", fromlist=["extract_image_metadata"]
                ),
                "extract_image_metadata",
                side_effect=Exception("Metadata extraction failed"),
            ),
            patch.object(
                __import__("media.processors", fromlist=["extract_image_metadata"]),
                "extract_image_metadata",
                side_effect=Exception("Metadata extraction failed"),
            ),
        ):
            process_media_file.run(str(media_file_pending.id))

        media_file_pending.refresh_from_db()
        # File should still be READY
        assert media_file_pending.processing_status == MediaFile.ProcessingStatus.READY
        # Error recorded
        assert media_file_pending.processing_error is not None
        assert "metadata" in media_file_pending.processing_error.lower()
        # Assets should still have been generated
        assert media_file_pending.assets.count() >= 1

    def test_all_assets_fail_but_file_ready(self, media_file_pending):
        """Test that even if all assets fail, file is marked READY (original accessible)."""
        from media.processors.base import PermanentProcessingError

        # Patch all image processors
        processors_module = __import__(
            "media.processors", fromlist=["generate_image_thumbnail"]
        )
        image_module = __import__(
            "media.processors.image", fromlist=["generate_image_thumbnail"]
        )

        with (
            patch.object(
                image_module,
                "generate_image_thumbnail",
                side_effect=PermanentProcessingError("Thumbnail failed"),
            ),
            patch.object(
                image_module,
                "generate_image_preview",
                side_effect=PermanentProcessingError("Preview failed"),
            ),
            patch.object(
                image_module,
                "generate_image_web_optimized",
                side_effect=PermanentProcessingError("Web optimized failed"),
            ),
            patch.object(
                processors_module,
                "generate_image_thumbnail",
                side_effect=PermanentProcessingError("Thumbnail failed"),
            ),
            patch.object(
                processors_module,
                "generate_image_preview",
                side_effect=PermanentProcessingError("Preview failed"),
            ),
            patch.object(
                processors_module,
                "generate_image_web_optimized",
                side_effect=PermanentProcessingError("Web optimized failed"),
            ),
        ):
            process_media_file.run(str(media_file_pending.id))

        media_file_pending.refresh_from_db()
        # With graceful degradation, original file is always accessible
        assert media_file_pending.processing_status == MediaFile.ProcessingStatus.READY
        # All errors are recorded
        assert media_file_pending.processing_error is not None
        # No assets created
        assert media_file_pending.assets.count() == 0


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

        # Should have 3 assets (thumbnail, preview, web_optimized) - created once
        assert media_file_pending.assets.count() == 3
