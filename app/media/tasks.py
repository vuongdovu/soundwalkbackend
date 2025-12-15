"""
Celery tasks for media file processing.

This module provides async tasks for:
- Processing uploaded media files (thumbnail generation, transcoding)
- Retrying failed processing jobs
- Cleaning up stuck processing jobs

The processing pipeline is designed for reliability:
- Idempotent operations (safe to retry)
- Atomic state transitions (no race conditions)
- Proper error categorization (permanent vs transient)
- Observability through structured logging

Usage:
    from media.tasks import process_media_file

    # Queue a file for processing (typically called after upload)
    process_media_file.delay(str(media_file.id))

    # Retry all failed files (typically via celery-beat)
    retry_failed_processing.delay()
"""

from __future__ import annotations

import logging
from datetime import timedelta
from uuid import UUID

from celery import shared_task
from celery.exceptions import Reject
from django.db import transaction
from django.db.models import F
from django.utils import timezone

logger = logging.getLogger(__name__)

# =============================================================================
# Constants
# =============================================================================

MAX_PROCESSING_RETRIES = 3
STUCK_PROCESSING_THRESHOLD_MINUTES = 30


# =============================================================================
# Main Processing Task
# =============================================================================


@shared_task(
    bind=True,
    autoretry_for=(IOError, OSError),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_kwargs={"max_retries": MAX_PROCESSING_RETRIES},
    acks_late=True,
)
def process_media_file(self, media_file_id: str) -> dict:
    """
    Process an uploaded media file asynchronously.

    This task handles the complete processing pipeline:
    1. Loads the MediaFile by ID
    2. Checks idempotency (already processed? skip)
    3. Transitions state to PROCESSING
    4. Dispatches to appropriate processor based on media type
    5. Transitions state to READY or FAILED

    For images, this generates a thumbnail. Video and document
    processing will be added in future phases.

    Args:
        media_file_id: UUID of the MediaFile to process.

    Returns:
        Dict with processing result status.

    Raises:
        Reject: For permanent failures that should not be retried.
        Exception: For transient failures that will trigger retry.
    """
    from media.models import MediaFile
    from media.processors.image import ImageProcessingError, generate_image_thumbnail

    # Convert string ID to UUID if needed
    if isinstance(media_file_id, str):
        media_file_id = UUID(media_file_id)

    logger.info(
        "Processing media file",
        extra={"media_file_id": str(media_file_id)},
    )

    # Load the media file
    try:
        media_file = MediaFile.objects.get(id=media_file_id)
    except MediaFile.DoesNotExist:
        logger.error(
            "MediaFile not found",
            extra={"media_file_id": str(media_file_id)},
        )
        return {"status": "not_found", "media_file_id": str(media_file_id)}

    # Check idempotency - already processed?
    if media_file.processing_status == MediaFile.ProcessingStatus.READY:
        logger.info(
            "MediaFile already processed, skipping",
            extra={"media_file_id": str(media_file_id)},
        )
        return {
            "status": "already_processed",
            "media_file_id": str(media_file_id),
        }

    # Transition to PROCESSING state atomically
    with transaction.atomic():
        # Use select_for_update to prevent race conditions
        media_file = MediaFile.objects.select_for_update().get(id=media_file_id)

        # Double-check after acquiring lock
        if media_file.processing_status == MediaFile.ProcessingStatus.READY:
            return {
                "status": "already_processed",
                "media_file_id": str(media_file_id),
            }

        media_file.processing_status = MediaFile.ProcessingStatus.PROCESSING
        media_file.processing_started_at = timezone.now()
        media_file.processing_attempts = F("processing_attempts") + 1
        media_file.save(
            update_fields=[
                "processing_status",
                "processing_started_at",
                "processing_attempts",
            ]
        )

    # Refresh to get the actual processing_attempts value
    media_file.refresh_from_db()

    logger.info(
        f"Processing {media_file.media_type} file",
        extra={
            "media_file_id": str(media_file_id),
            "media_type": media_file.media_type,
            "attempt": media_file.processing_attempts,
        },
    )

    try:
        # Dispatch to appropriate processor based on media type
        if media_file.media_type == MediaFile.MediaType.IMAGE:
            generate_image_thumbnail(media_file)
        elif media_file.media_type == MediaFile.MediaType.VIDEO:
            # TODO: Implement video processing in Phase 3
            logger.info(
                "Video processing not yet implemented, marking as ready",
                extra={"media_file_id": str(media_file_id)},
            )
        elif media_file.media_type == MediaFile.MediaType.DOCUMENT:
            # TODO: Implement document processing in Phase 3
            logger.info(
                "Document processing not yet implemented, marking as ready",
                extra={"media_file_id": str(media_file_id)},
            )
        elif media_file.media_type == MediaFile.MediaType.AUDIO:
            # TODO: Implement audio processing in Phase 3
            logger.info(
                "Audio processing not yet implemented, marking as ready",
                extra={"media_file_id": str(media_file_id)},
            )
        else:
            logger.info(
                "Unknown media type, marking as ready",
                extra={
                    "media_file_id": str(media_file_id),
                    "media_type": media_file.media_type,
                },
            )

        # Mark as successfully processed
        with transaction.atomic():
            media_file.processing_status = MediaFile.ProcessingStatus.READY
            media_file.processing_completed_at = timezone.now()
            media_file.processing_error = None
            media_file.save(
                update_fields=[
                    "processing_status",
                    "processing_completed_at",
                    "processing_error",
                ]
            )

        logger.info(
            "Media file processed successfully",
            extra={
                "media_file_id": str(media_file_id),
                "media_type": media_file.media_type,
            },
        )

        return {
            "status": "processed",
            "media_file_id": str(media_file_id),
            "media_type": media_file.media_type,
        }

    except ImageProcessingError as e:
        # Permanent failure - don't retry
        error_msg = str(e)
        _mark_processing_failed(media_file, error_msg)

        logger.warning(
            "Media file processing failed permanently",
            extra={
                "media_file_id": str(media_file_id),
                "error": error_msg,
            },
        )

        # Reject prevents retrying this task
        raise Reject(error_msg, requeue=False)

    except Exception as e:
        # Check if we've exhausted retries
        error_msg = f"{type(e).__name__}: {str(e)}"

        if self.request.retries >= MAX_PROCESSING_RETRIES:
            # Exhausted retries - mark as failed
            _mark_processing_failed(media_file, error_msg)

            logger.error(
                "Media file processing failed after max retries",
                extra={
                    "media_file_id": str(media_file_id),
                    "error": error_msg,
                    "retries": self.request.retries,
                },
            )

            raise Reject(error_msg, requeue=False)

        # Transient error - will be retried by Celery
        logger.warning(
            "Media file processing failed, will retry",
            extra={
                "media_file_id": str(media_file_id),
                "error": error_msg,
                "retry": self.request.retries + 1,
                "max_retries": MAX_PROCESSING_RETRIES,
            },
        )

        # Store error but keep status as PROCESSING
        media_file.processing_error = error_msg
        media_file.save(update_fields=["processing_error"])

        # Re-raise to trigger retry
        raise


def _mark_processing_failed(media_file, error_msg: str) -> None:
    """
    Mark a media file as failed processing.

    Args:
        media_file: MediaFile instance to mark as failed.
        error_msg: Error message to store.
    """
    with transaction.atomic():
        media_file.processing_status = media_file.ProcessingStatus.FAILED
        media_file.processing_error = error_msg[:1000]  # Truncate long errors
        media_file.processing_completed_at = timezone.now()
        media_file.save(
            update_fields=[
                "processing_status",
                "processing_error",
                "processing_completed_at",
            ]
        )


# =============================================================================
# Retry and Cleanup Tasks
# =============================================================================


@shared_task
def retry_failed_processing() -> dict:
    """
    Periodic task to retry failed media file processing.

    Finds failed files that haven't exceeded max retries and
    re-queues them for processing.

    This task should be scheduled via celery-beat, e.g., every 5 minutes.

    Returns:
        Dict with count of files queued for retry.
    """
    from media.models import MediaFile

    # Find failed files that can be retried
    failed_files = MediaFile.objects.filter(
        processing_status=MediaFile.ProcessingStatus.FAILED,
        processing_attempts__lt=MAX_PROCESSING_RETRIES,
    ).order_by("created_at")[:100]  # Process in batches

    queued_count = 0
    for media_file in failed_files:
        try:
            # Reset status to PENDING before requeueing
            media_file.processing_status = MediaFile.ProcessingStatus.PENDING
            media_file.save(update_fields=["processing_status"])

            process_media_file.delay(str(media_file.id))
            queued_count += 1

            logger.info(
                "Queued failed media file for retry",
                extra={
                    "media_file_id": str(media_file.id),
                    "attempts": media_file.processing_attempts,
                },
            )
        except Exception as e:
            logger.error(
                f"Failed to queue media file for retry: {e}",
                extra={"media_file_id": str(media_file.id)},
            )

    logger.info(
        f"Queued {queued_count} failed media files for retry",
        extra={"queued_count": queued_count},
    )

    return {"queued_count": queued_count}


@shared_task
def cleanup_stuck_processing() -> dict:
    """
    Periodic task to reset stuck processing jobs.

    Finds media files that have been in PROCESSING status for too long
    and resets them to FAILED so they can be retried.

    This handles cases where the worker crashed during processing.

    Returns:
        Dict with count of files reset.
    """
    from media.models import MediaFile

    threshold = timezone.now() - timedelta(minutes=STUCK_PROCESSING_THRESHOLD_MINUTES)

    # Find files stuck in PROCESSING state
    stuck_files = MediaFile.objects.filter(
        processing_status=MediaFile.ProcessingStatus.PROCESSING,
        processing_started_at__lt=threshold,
    )

    reset_count = 0
    for media_file in stuck_files:
        media_file.processing_status = MediaFile.ProcessingStatus.FAILED
        media_file.processing_error = "Processing timed out - worker may have crashed"
        media_file.save(update_fields=["processing_status", "processing_error"])
        reset_count += 1

        logger.warning(
            "Reset stuck media file",
            extra={
                "media_file_id": str(media_file.id),
                "stuck_since": media_file.processing_started_at.isoformat()
                if media_file.processing_started_at
                else None,
            },
        )

    if reset_count > 0:
        logger.info(
            f"Reset {reset_count} stuck media files",
            extra={"reset_count": reset_count},
        )

    return {"reset_count": reset_count}
