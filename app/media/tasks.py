"""
Celery tasks for media file processing and scanning.

This module provides async tasks for:
- Scanning uploaded files for malware
- Processing uploaded media files (thumbnail generation, transcoding)
- Retrying failed processing jobs
- Cleaning up stuck processing jobs

The processing pipeline is designed for reliability:
- Idempotent operations (safe to retry)
- Atomic state transitions (no race conditions)
- Proper error categorization (permanent vs transient)
- Observability through structured logging
- Fail-open malware scanning with circuit breaker

Usage:
    from media.tasks import scan_file_for_malware, process_media_file
    from celery import chain

    # Create the scan -> process chain
    task_chain = chain(
        scan_file_for_malware.s(str(media_file.id)),
        process_media_file.s(),
    )
    task_chain.delay()

    # Or scan only
    scan_file_for_malware.delay(str(media_file.id))

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
# Malware Scanning Task
# =============================================================================


@shared_task(
    bind=True,
    autoretry_for=(ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_kwargs={"max_retries": 3},
    acks_late=True,
)
def scan_file_for_malware(self, media_file_id: str) -> dict:
    """
    Scan a media file for malware using ClamAV.

    This task is designed to be the first step in a Celery chain:
    - CLEAN: Returns dict to continue chain
    - INFECTED: Quarantines file and rejects (stops chain)
    - SKIPPED: Marks for rescan, returns dict to continue (fail-open)
    - ERROR: Allows retry

    Args:
        media_file_id: UUID of the MediaFile to scan.

    Returns:
        Dict with scan status and media_file_id for chain continuation.

    Raises:
        Reject: For infected files or permanent failures.
    """

    from media.models import MediaFile
    from media.services.quarantine import quarantine_infected_file
    from media.services.scanner import MalwareScanner, ScanResult

    # Convert string ID to UUID if needed
    if isinstance(media_file_id, str):
        file_id = UUID(media_file_id)
    else:
        file_id = media_file_id
        media_file_id = str(media_file_id)

    logger.info(
        "Scanning media file for malware",
        extra={"media_file_id": media_file_id},
    )

    # Load the media file
    try:
        media_file = MediaFile.objects.get(id=file_id)
    except MediaFile.DoesNotExist:
        logger.error(
            "MediaFile not found for scanning",
            extra={"media_file_id": media_file_id},
        )
        return {"status": "not_found", "media_file_id": media_file_id}

    # Check idempotency - already scanned and clean?
    if media_file.scan_status == MediaFile.ScanStatus.CLEAN:
        logger.info(
            "MediaFile already scanned and clean, skipping",
            extra={"media_file_id": media_file_id},
        )
        return {
            "status": "already_scanned",
            "media_file_id": media_file_id,
        }

    # Check if already infected (shouldn't happen in normal flow)
    if media_file.scan_status == MediaFile.ScanStatus.INFECTED:
        logger.warning(
            "MediaFile already marked as infected",
            extra={"media_file_id": media_file_id},
        )
        raise Reject("File already quarantined", requeue=False)

    # Perform the scan
    scanner = MalwareScanner()
    result = scanner.scan_media_file(media_file)

    if result.status == ScanResult.CLEAN:
        # File is clean - update status and continue chain
        media_file.scan_status = MediaFile.ScanStatus.CLEAN
        media_file.scanned_at = result.scanned_at
        media_file.save(update_fields=["scan_status", "scanned_at", "updated_at"])

        logger.info(
            "Media file scanned clean",
            extra={
                "media_file_id": media_file_id,
                "scan_method": result.scan_method,
            },
        )

        return {
            "status": "clean",
            "media_file_id": media_file_id,
        }

    elif result.status == ScanResult.INFECTED:
        # File is infected - quarantine and stop chain
        logger.warning(
            "Malware detected in media file",
            extra={
                "media_file_id": media_file_id,
                "threat_name": result.threat_name,
            },
        )

        # Quarantine the infected file
        quarantine_result = quarantine_infected_file(media_file, result.threat_name)

        if not quarantine_result.success:
            logger.error(
                "Failed to quarantine infected file",
                extra={
                    "media_file_id": media_file_id,
                    "error": quarantine_result.error,
                },
            )

        # Reject to stop the chain - infected files should not be processed
        raise Reject(f"Malware detected: {result.threat_name}", requeue=False)

    elif result.status == ScanResult.SKIPPED:
        # Scanner unavailable (circuit open) - fail-open, mark for rescan
        logger.warning(
            "Malware scan skipped due to scanner unavailability",
            extra={
                "media_file_id": media_file_id,
                "reason": result.skipped_reason,
            },
        )

        # Keep scan_status as PENDING - will be picked up by rescan task
        # Allow chain to continue (fail-open)
        return {
            "status": "skipped",
            "media_file_id": media_file_id,
            "reason": result.skipped_reason,
        }

    else:  # ScanResult.ERROR
        # Scan error - allow retry
        error_msg = result.error_message or "Unknown scan error"
        logger.error(
            "Malware scan error",
            extra={
                "media_file_id": media_file_id,
                "error": error_msg,
            },
        )

        # Update status to ERROR
        media_file.scan_status = MediaFile.ScanStatus.ERROR
        media_file.scanned_at = result.scanned_at
        media_file.save(update_fields=["scan_status", "scanned_at", "updated_at"])

        # Check retry count
        if self.request.retries >= 3:
            # Exhausted retries - fail-open, allow processing
            logger.warning(
                "Scan retries exhausted, allowing processing (fail-open)",
                extra={"media_file_id": media_file_id},
            )
            return {
                "status": "error_exhausted",
                "media_file_id": media_file_id,
            }

        # Raise to trigger retry
        raise ConnectionError(f"Scan error: {error_msg}")


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
def process_media_file(self, scan_result: dict | str) -> dict:
    """
    Process an uploaded media file asynchronously with graceful degradation.

    This task handles the complete processing pipeline:
    1. Loads the MediaFile by ID (from chain result or direct call)
    2. Checks idempotency (already processed? skip)
    3. Transitions state to PROCESSING
    4. Extracts metadata (best effort)
    5. Generates derivative assets (each independently, partial failures OK)
    6. Always transitions to READY (graceful degradation)

    Processing is type-specific:
    - Images: thumbnail, preview, web-optimized + metadata
    - Videos: poster frame + metadata
    - Documents: thumbnail, extracted text + metadata
    - Audio: No processing yet (marked ready)

    Graceful Degradation:
    - Each asset is generated independently
    - Partial failures are recorded but don't block READY status
    - Original file is always accessible
    - Metadata extraction failures are logged but ignored

    Args:
        scan_result: Either a dict from scan_file_for_malware chain
                    (with 'media_file_id' key) or a direct string UUID.

    Returns:
        Dict with processing result status.

    Raises:
        Reject: For permanent failures that should not be retried.
        Exception: For transient failures that will trigger retry.
    """
    from media.models import MediaFile
    from media.processors import (
        PermanentProcessingError,
        TransientProcessingError,
        extract_document_metadata,
        extract_document_text,
        extract_image_metadata,
        extract_video_metadata,
        extract_video_poster,
        generate_document_thumbnail,
        generate_image_preview,
        generate_image_thumbnail,
        generate_image_web_optimized,
    )

    # Handle both chain input (dict) and direct call (str)
    if isinstance(scan_result, dict):
        media_file_id = scan_result.get("media_file_id")
        if not media_file_id:
            logger.error("Invalid scan result: missing media_file_id")
            return {"status": "error", "error": "missing media_file_id"}
    else:
        media_file_id = scan_result

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

    # Track errors for graceful degradation
    errors: list[str] = []
    metadata_updates: dict = {}

    try:
        # Dispatch to appropriate processor based on media type
        if media_file.media_type == MediaFile.MediaType.IMAGE:
            # Extract metadata (best effort)
            try:
                meta = extract_image_metadata(media_file)
                metadata_updates.update(meta)
            except Exception as e:
                errors.append(f"metadata: {e}")
                logger.warning(
                    "Failed to extract image metadata",
                    extra={"media_file_id": str(media_file_id), "error": str(e)},
                )

            # Generate assets independently (graceful degradation)
            for generator, asset_name in [
                (generate_image_thumbnail, "thumbnail"),
                (generate_image_preview, "preview"),
                (generate_image_web_optimized, "web_optimized"),
            ]:
                try:
                    generator(media_file)
                except PermanentProcessingError as e:
                    errors.append(f"{asset_name}: {e}")
                    logger.warning(
                        f"Failed to generate {asset_name} (permanent)",
                        extra={"media_file_id": str(media_file_id), "error": str(e)},
                    )
                except TransientProcessingError as e:
                    errors.append(f"{asset_name}: {e}")
                    logger.warning(
                        f"Failed to generate {asset_name} (transient)",
                        extra={"media_file_id": str(media_file_id), "error": str(e)},
                    )
                except Exception as e:
                    errors.append(f"{asset_name}: {e}")
                    logger.warning(
                        f"Unexpected error generating {asset_name}",
                        extra={"media_file_id": str(media_file_id), "error": str(e)},
                    )

        elif media_file.media_type == MediaFile.MediaType.VIDEO:
            # Extract metadata (best effort)
            try:
                meta = extract_video_metadata(media_file)
                metadata_updates.update(meta)
            except Exception as e:
                errors.append(f"metadata: {e}")
                logger.warning(
                    "Failed to extract video metadata",
                    extra={"media_file_id": str(media_file_id), "error": str(e)},
                )

            # Extract poster frame
            try:
                extract_video_poster(media_file)
            except PermanentProcessingError as e:
                errors.append(f"poster: {e}")
                logger.warning(
                    "Failed to extract video poster (permanent)",
                    extra={"media_file_id": str(media_file_id), "error": str(e)},
                )
            except TransientProcessingError as e:
                errors.append(f"poster: {e}")
                logger.warning(
                    "Failed to extract video poster (transient)",
                    extra={"media_file_id": str(media_file_id), "error": str(e)},
                )
            except Exception as e:
                errors.append(f"poster: {e}")
                logger.warning(
                    "Unexpected error extracting video poster",
                    extra={"media_file_id": str(media_file_id), "error": str(e)},
                )

        elif media_file.media_type == MediaFile.MediaType.DOCUMENT:
            # Extract metadata (best effort)
            try:
                meta = extract_document_metadata(media_file)
                metadata_updates.update(meta)
            except Exception as e:
                errors.append(f"metadata: {e}")
                logger.warning(
                    "Failed to extract document metadata",
                    extra={"media_file_id": str(media_file_id), "error": str(e)},
                )

            # Generate assets independently (graceful degradation)
            for generator, asset_name in [
                (generate_document_thumbnail, "thumbnail"),
                (extract_document_text, "text"),
            ]:
                try:
                    generator(media_file)
                except PermanentProcessingError as e:
                    errors.append(f"{asset_name}: {e}")
                    logger.warning(
                        f"Failed to generate {asset_name} (permanent)",
                        extra={"media_file_id": str(media_file_id), "error": str(e)},
                    )
                except TransientProcessingError as e:
                    errors.append(f"{asset_name}: {e}")
                    logger.warning(
                        f"Failed to generate {asset_name} (transient)",
                        extra={"media_file_id": str(media_file_id), "error": str(e)},
                    )
                except Exception as e:
                    errors.append(f"{asset_name}: {e}")
                    logger.warning(
                        f"Unexpected error generating {asset_name}",
                        extra={"media_file_id": str(media_file_id), "error": str(e)},
                    )

        elif media_file.media_type == MediaFile.MediaType.AUDIO:
            # Audio processing not yet implemented
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

        # Update metadata and mark as READY (graceful degradation)
        # Original file is always accessible regardless of asset generation
        with transaction.atomic():
            # Update metadata if we extracted any
            if metadata_updates:
                current_metadata = media_file.metadata or {}
                media_file.metadata = {**current_metadata, **metadata_updates}

            media_file.processing_status = MediaFile.ProcessingStatus.READY
            media_file.processing_completed_at = timezone.now()

            # Store errors if any occurred (partial failure info)
            if errors:
                media_file.processing_error = "; ".join(errors)[:1000]  # Truncate
            else:
                media_file.processing_error = None

            media_file.save(
                update_fields=[
                    "metadata",
                    "processing_status",
                    "processing_completed_at",
                    "processing_error",
                ]
            )

        log_level = logging.WARNING if errors else logging.INFO
        logger.log(
            log_level,
            "Media file processed" + (" with errors" if errors else " successfully"),
            extra={
                "media_file_id": str(media_file_id),
                "media_type": media_file.media_type,
                "error_count": len(errors),
                "errors": errors[:5] if errors else None,  # First 5 errors
            },
        )

        return {
            "status": "processed",
            "media_file_id": str(media_file_id),
            "media_type": media_file.media_type,
            "errors": errors if errors else None,
        }

    except Exception as e:
        # Unexpected error during processing setup (not asset generation)
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


# =============================================================================
# Scanning Tasks
# =============================================================================


@shared_task
def rescan_skipped_files() -> dict:
    """
    Periodic task to rescan files that were skipped due to scanner outage.

    Finds files with scan_status=PENDING (skipped) and re-queues them
    for scanning. Should be scheduled via celery-beat, e.g., every 15 minutes.

    Returns:
        Dict with count of files queued for rescan.
    """

    from media.models import MediaFile

    # Find files that need rescanning (PENDING status with processing complete)
    files_to_scan = MediaFile.objects.filter(
        scan_status=MediaFile.ScanStatus.PENDING,
        processing_status=MediaFile.ProcessingStatus.READY,
    ).order_by("created_at")[:50]  # Process in batches

    queued_count = 0
    for media_file in files_to_scan:
        try:
            # Re-run the scan (just scan, not full chain since already processed)
            scan_file_for_malware.delay(str(media_file.id))
            queued_count += 1

            logger.info(
                "Queued file for rescan",
                extra={"media_file_id": str(media_file.id)},
            )
        except Exception as e:
            logger.error(
                f"Failed to queue file for rescan: {e}",
                extra={"media_file_id": str(media_file.id)},
            )

    if queued_count > 0:
        logger.info(
            f"Queued {queued_count} files for rescan",
            extra={"queued_count": queued_count},
        )

    return {"queued_count": queued_count}


@shared_task
def check_antivirus_health() -> dict:
    """
    Periodic task to check ClamAV connectivity and definition freshness.

    Logs warnings if:
    - Scanner is unavailable
    - Circuit breaker is open
    - Virus definitions are stale

    This task should be scheduled via celery-beat, e.g., every 5 minutes.

    Returns:
        Dict with health status information.
    """
    from datetime import timedelta

    from django.conf import settings

    from media.services.scanner import MalwareScanner

    scanner = MalwareScanner()
    status = {
        "available": False,
        "circuit_state": "unknown",
        "definitions": None,
        "warnings": [],
    }

    # Check circuit breaker state
    circuit_status = scanner.get_circuit_status()
    status["circuit_state"] = circuit_status.get("state", "unknown")

    if circuit_status.get("state") == "open":
        status["warnings"].append("Circuit breaker is open - scanner may be unhealthy")
        logger.warning(
            "ClamAV circuit breaker is open",
            extra={
                "circuit_status": circuit_status,
                "event_type": "antivirus_health_check",
            },
        )

    # Check scanner availability
    status["available"] = scanner.is_available()

    if not status["available"]:
        status["warnings"].append("Scanner is not available")
        logger.warning(
            "ClamAV scanner is not available",
            extra={"event_type": "antivirus_health_check"},
        )
        return status

    # Check virus definitions
    definitions = scanner.check_definitions()
    if definitions:
        status["definitions"] = {
            "version": definitions.version,
            "signature_count": definitions.signature_count,
            "last_update": definitions.last_update.isoformat()
            if definitions.last_update
            else None,
        }

        # Check if definitions are stale
        if definitions.last_update:
            stale_threshold = timedelta(days=settings.CLAMAV_STALE_DEFINITIONS_DAYS)
            if timezone.now() - definitions.last_update > stale_threshold:
                status["warnings"].append(
                    f"Virus definitions are stale (older than {settings.CLAMAV_STALE_DEFINITIONS_DAYS} days)"
                )
                logger.warning(
                    "ClamAV virus definitions are stale",
                    extra={
                        "event_type": "antivirus_health_check",
                        "last_update": definitions.last_update.isoformat(),
                        "threshold_days": settings.CLAMAV_STALE_DEFINITIONS_DAYS,
                    },
                )

    if not status["warnings"]:
        logger.info(
            "ClamAV health check passed",
            extra={
                "event_type": "antivirus_health_check",
                "definitions_version": status.get("definitions", {}).get("version"),
            },
        )

    return status


# =============================================================================
# Chunked Upload Cleanup Tasks
# =============================================================================


@shared_task
def cleanup_expired_upload_sessions() -> dict:
    """
    Periodic task to clean up expired upload sessions.

    Finds IN_PROGRESS sessions that have expired and:
    1. Aborts any associated S3 multipart uploads
    2. Deletes local temp directories
    3. Marks sessions as EXPIRED

    This task should be scheduled via celery-beat, e.g., every hour.

    Returns:
        Dict with count of sessions cleaned up.
    """
    import os
    import shutil

    from media.models import UploadSession

    # Find expired in-progress sessions
    expired_sessions = UploadSession.objects.filter(
        status=UploadSession.Status.IN_PROGRESS,
        expires_at__lt=timezone.now(),
    )

    cleaned_count = 0
    local_cleaned = 0
    s3_cleaned = 0
    errors = []

    for session in expired_sessions:
        try:
            if session.backend == UploadSession.Backend.LOCAL:
                # Clean up local temp directory
                if session.local_temp_dir and os.path.exists(session.local_temp_dir):
                    shutil.rmtree(session.local_temp_dir)
                local_cleaned += 1
            elif session.backend == UploadSession.Backend.S3:
                # Abort S3 multipart upload
                try:
                    import boto3
                    from django.core.files.storage import default_storage

                    bucket_name = getattr(default_storage, "bucket_name", None)
                    if bucket_name and session.s3_key and session.s3_upload_id:
                        s3_client = boto3.client("s3")
                        s3_client.abort_multipart_upload(
                            Bucket=bucket_name,
                            Key=session.s3_key,
                            UploadId=session.s3_upload_id,
                        )
                    s3_cleaned += 1
                except ImportError:
                    # boto3 not installed, skip S3 cleanup
                    pass
                except Exception as e:
                    errors.append(f"S3 cleanup error for {session.id}: {str(e)}")

            # Mark session as expired
            session.status = UploadSession.Status.EXPIRED
            session.save()
            cleaned_count += 1

        except Exception as e:
            errors.append(f"Error cleaning session {session.id}: {str(e)}")
            logger.error(
                "Failed to clean up expired upload session",
                extra={
                    "event_type": "upload_session_cleanup_error",
                    "session_id": str(session.id),
                    "error": str(e),
                },
            )

    logger.info(
        "Expired upload sessions cleaned up",
        extra={
            "event_type": "upload_session_cleanup",
            "cleaned_count": cleaned_count,
            "local_cleaned": local_cleaned,
            "s3_cleaned": s3_cleaned,
            "error_count": len(errors),
        },
    )

    return {
        "cleaned_count": cleaned_count,
        "local_cleaned": local_cleaned,
        "s3_cleaned": s3_cleaned,
        "errors": errors,
    }


@shared_task
def cleanup_orphaned_local_temp_dirs() -> dict:
    """
    Safety net task to clean up local temp directories without matching sessions.

    Scans the chunked upload temp directory for subdirectories that:
    1. Don't have a matching IN_PROGRESS session
    2. Are older than the expiry threshold

    This handles cases where the app crashed before marking a session as failed.
    Schedule via celery-beat, e.g., daily.

    Returns:
        Dict with count of directories removed.
    """
    import os
    import shutil

    from django.conf import settings

    from media.models import UploadSession

    # Get temp base directory
    temp_base = getattr(settings, "CHUNKED_UPLOAD_TEMP_DIR", None)
    if not temp_base:
        temp_base = os.path.join(settings.MEDIA_ROOT, "chunks")

    if not os.path.isdir(temp_base):
        return {"removed_count": 0, "message": "Temp directory does not exist"}

    # Get all active session IDs
    active_session_ids = set(
        str(sid)
        for sid in UploadSession.objects.filter(
            status=UploadSession.Status.IN_PROGRESS
        ).values_list("id", flat=True)
    )

    removed_count = 0
    errors = []

    # Expiry threshold for orphaned directories (same as session expiry)
    expiry_hours = getattr(settings, "CHUNKED_UPLOAD_EXPIRY_HOURS", 24)
    threshold = timezone.now() - timedelta(hours=expiry_hours)

    for entry in os.scandir(temp_base):
        if not entry.is_dir():
            continue

        dir_name = entry.name

        # Skip if there's an active session for this directory
        if dir_name in active_session_ids:
            continue

        # Check directory age
        dir_mtime = timezone.datetime.fromtimestamp(
            entry.stat().st_mtime, tz=timezone.utc
        )
        if dir_mtime > threshold:
            # Directory is too new, might be in-use
            continue

        try:
            shutil.rmtree(entry.path)
            removed_count += 1
            logger.info(
                "Removed orphaned temp directory",
                extra={
                    "event_type": "orphaned_temp_cleanup",
                    "directory": dir_name,
                },
            )
        except Exception as e:
            errors.append(f"Failed to remove {dir_name}: {str(e)}")

    logger.info(
        "Orphaned temp directory cleanup complete",
        extra={
            "event_type": "orphaned_temp_cleanup_complete",
            "removed_count": removed_count,
            "error_count": len(errors),
        },
    )

    return {
        "removed_count": removed_count,
        "errors": errors,
    }


@shared_task
def cleanup_orphaned_s3_multipart_uploads() -> dict:
    """
    Safety net task to clean up S3 multipart uploads without matching sessions.

    Lists incomplete multipart uploads in the bucket that:
    1. Don't have a matching IN_PROGRESS session
    2. Were initiated more than the expiry threshold ago

    This handles cases where the app crashed before aborting an upload.
    Schedule via celery-beat, e.g., daily.

    Returns:
        Dict with count of uploads aborted.
    """
    try:
        import boto3
        from django.core.files.storage import default_storage
    except ImportError:
        return {"aborted_count": 0, "message": "boto3 not installed"}

    # Only run if using S3 storage
    if not hasattr(default_storage, "bucket"):
        return {"aborted_count": 0, "message": "Not using S3 storage"}

    from django.conf import settings as django_settings

    from media.models import UploadSession

    bucket_name = getattr(default_storage, "bucket_name", None)
    if not bucket_name:
        return {"aborted_count": 0, "message": "No bucket name configured"}

    s3_client = boto3.client("s3")

    # Get active S3 upload IDs
    active_upload_ids = set(
        UploadSession.objects.filter(
            status=UploadSession.Status.IN_PROGRESS,
            backend=UploadSession.Backend.S3,
        ).values_list("s3_upload_id", flat=True)
    )

    # Expiry threshold
    expiry_hours = getattr(django_settings, "CHUNKED_UPLOAD_EXPIRY_HOURS", 24)
    threshold = timezone.now() - timedelta(hours=expiry_hours)

    aborted_count = 0
    errors = []

    try:
        # List incomplete multipart uploads
        response = s3_client.list_multipart_uploads(
            Bucket=bucket_name,
            Prefix="uploads/pending/",  # Only scan chunked upload area
        )

        for upload in response.get("Uploads", []):
            upload_id = upload["UploadId"]
            initiated = upload["Initiated"]

            # Skip if there's an active session for this upload
            if upload_id in active_upload_ids:
                continue

            # Skip if too new
            if initiated.replace(tzinfo=timezone.utc) > threshold:
                continue

            try:
                s3_client.abort_multipart_upload(
                    Bucket=bucket_name,
                    Key=upload["Key"],
                    UploadId=upload_id,
                )
                aborted_count += 1
                logger.info(
                    "Aborted orphaned S3 multipart upload",
                    extra={
                        "event_type": "orphaned_s3_cleanup",
                        "upload_id": upload_id,
                        "key": upload["Key"],
                    },
                )
            except Exception as e:
                errors.append(f"Failed to abort {upload_id}: {str(e)}")

    except Exception as e:
        logger.error(
            "Failed to list multipart uploads",
            extra={
                "event_type": "orphaned_s3_cleanup_error",
                "error": str(e),
            },
        )
        return {"aborted_count": 0, "error": str(e)}

    logger.info(
        "Orphaned S3 multipart upload cleanup complete",
        extra={
            "event_type": "orphaned_s3_cleanup_complete",
            "aborted_count": aborted_count,
            "error_count": len(errors),
        },
    )

    return {
        "aborted_count": aborted_count,
        "errors": errors,
    }
