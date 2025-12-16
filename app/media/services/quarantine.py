"""
Quarantine service for infected files.

This module provides hard quarantine functionality:
- Moves infected files to a secure quarantine directory
- Creates metadata files for audit and potential restoration
- Updates MediaFile record with infected status
- Reclaims user storage quota
- Logs structured alerts for ops monitoring

Usage:
    from media.services.quarantine import quarantine_infected_file, restore_from_quarantine

    # Quarantine an infected file
    result = quarantine_infected_file(media_file, "Eicar-Signature")

    # Restore a file from quarantine (admin use for false positives)
    result = restore_from_quarantine(media_file_id)
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.services import ServiceResult

if TYPE_CHECKING:
    from media.models import MediaFile

logger = logging.getLogger(__name__)


@dataclass
class QuarantineMetadata:
    """Metadata stored with quarantined files."""

    media_file_id: str
    original_path: str
    original_filename: str
    file_size: int
    mime_type: str
    media_type: str
    uploader_id: str
    uploader_email: str
    threat_name: str
    scanned_at: str
    quarantined_at: str
    quarantine_reason: str


def _get_quarantine_dir() -> Path:
    """Get the quarantine directory path."""
    quarantine_path = Path(settings.MEDIA_ROOT) / settings.CLAMAV_QUARANTINE_DIR
    return quarantine_path


def _get_quarantine_file_dir(media_file_id: str) -> Path:
    """
    Get the quarantine directory for a specific file.

    Pattern: quarantine/YYYY-MM-DD/{file_id}/

    This structure:
    - Organizes by date for retention management
    - Uses file ID for uniqueness
    """
    today = date.today()
    quarantine_base = _get_quarantine_dir()
    return quarantine_base / today.isoformat() / media_file_id


def quarantine_infected_file(
    media_file: MediaFile,
    threat_name: str,
) -> ServiceResult[str]:
    """
    Move an infected file to quarantine.

    This performs a hard quarantine:
    1. Moves file to quarantine directory
    2. Creates metadata.json with scan info
    3. Updates MediaFile: scan_status=infected, threat_name, scanned_at
    4. Reclaims user storage quota
    5. Logs structured alert for ops

    Args:
        media_file: The infected MediaFile instance.
        threat_name: Name of the detected threat.

    Returns:
        ServiceResult with quarantine path on success, or error details.
    """
    from media.models import MediaFile

    try:
        # Get file info before moving
        original_path = Path(media_file.file.path)
        file_size = media_file.file_size

        if not original_path.exists():
            logger.error(
                "Cannot quarantine: file not found",
                extra={
                    "media_file_id": str(media_file.id),
                    "path": str(original_path),
                },
            )
            return ServiceResult.failure(
                error=f"File not found: {original_path}",
                error_code="file_not_found",
            )

        # Create quarantine directory
        quarantine_dir = _get_quarantine_file_dir(str(media_file.id))
        quarantine_dir.mkdir(parents=True, exist_ok=True)

        # Move file to quarantine
        quarantine_file_path = quarantine_dir / original_path.name
        shutil.move(str(original_path), str(quarantine_file_path))

        # Create metadata file
        metadata = QuarantineMetadata(
            media_file_id=str(media_file.id),
            original_path=str(original_path),
            original_filename=media_file.original_filename,
            file_size=file_size,
            mime_type=media_file.mime_type,
            media_type=media_file.media_type,
            uploader_id=str(media_file.uploader_id),
            uploader_email=media_file.uploader.email,
            threat_name=threat_name,
            scanned_at=timezone.now().isoformat(),
            quarantined_at=timezone.now().isoformat(),
            quarantine_reason=f"Malware detected: {threat_name}",
        )

        metadata_path = quarantine_dir / "metadata.json"
        with open(metadata_path, "w") as f:
            json.dump(asdict(metadata), f, indent=2)

        # Update MediaFile record and reclaim quota
        with transaction.atomic():
            media_file.scan_status = MediaFile.ScanStatus.INFECTED
            media_file.threat_name = threat_name
            media_file.scanned_at = timezone.now()
            # Clear the file field since file is moved
            media_file.file = ""
            media_file.save(
                update_fields=[
                    "scan_status",
                    "threat_name",
                    "scanned_at",
                    "file",
                    "updated_at",
                ]
            )

            # Reclaim user storage quota
            if hasattr(media_file.uploader, "profile"):
                media_file.uploader.profile.subtract_storage_usage(file_size)

        # Log structured alert for ops
        logger.warning(
            "File quarantined due to malware detection",
            extra={
                "event_type": "malware_quarantine",
                "media_file_id": str(media_file.id),
                "original_filename": media_file.original_filename,
                "threat_name": threat_name,
                "file_size": file_size,
                "uploader_id": str(media_file.uploader_id),
                "uploader_email": media_file.uploader.email,
                "quarantine_path": str(quarantine_file_path),
            },
        )

        return ServiceResult.success(str(quarantine_file_path))

    except OSError as e:
        logger.exception(
            f"Failed to move file to quarantine: {e}",
            extra={"media_file_id": str(media_file.id)},
        )
        return ServiceResult.failure(
            error=f"Failed to move file: {e}",
            error_code="quarantine_io_error",
        )
    except Exception as e:
        logger.exception(
            f"Unexpected error during quarantine: {e}",
            extra={"media_file_id": str(media_file.id)},
        )
        return ServiceResult.failure(
            error=str(e),
            error_code="quarantine_error",
        )


def restore_from_quarantine(media_file_id: str) -> ServiceResult[str]:
    """
    Restore a file from quarantine (admin use for false positives).

    This reverses the quarantine:
    1. Finds the quarantined file using metadata
    2. Moves file back to original location
    3. Updates MediaFile: scan_status=clean, clears threat_name
    4. Restores user storage quota
    5. Logs restoration event

    Args:
        media_file_id: The UUID of the quarantined MediaFile.

    Returns:
        ServiceResult with restored path on success, or error details.
    """
    from media.models import MediaFile

    try:
        # Get MediaFile
        try:
            media_file = MediaFile.all_objects.get(id=media_file_id)
        except (MediaFile.DoesNotExist, ValueError, Exception) as e:
            # ValueError/ValidationError is raised for invalid UUID format
            if isinstance(e, MediaFile.DoesNotExist) or "UUID" in str(e):
                return ServiceResult.failure(
                    error=f"MediaFile not found: {media_file_id}",
                    error_code="media_file_not_found",
                )
            raise

        if media_file.scan_status != MediaFile.ScanStatus.INFECTED:
            return ServiceResult.failure(
                error=f"MediaFile is not quarantined (status: {media_file.scan_status})",
                error_code="not_quarantined",
            )

        # Find quarantine directory - search by media_file_id
        quarantine_base = _get_quarantine_dir()
        metadata_path = None
        quarantine_dir = None

        # Search through date directories
        for date_dir in quarantine_base.iterdir():
            if date_dir.is_dir():
                potential_dir = date_dir / media_file_id
                potential_metadata = potential_dir / "metadata.json"
                if potential_metadata.exists():
                    metadata_path = potential_metadata
                    quarantine_dir = potential_dir
                    break

        if not metadata_path or not quarantine_dir:
            return ServiceResult.failure(
                error=f"Quarantine directory not found for: {media_file_id}",
                error_code="quarantine_not_found",
            )

        # Read metadata
        with open(metadata_path) as f:
            metadata = json.load(f)

        original_path = Path(metadata["original_path"])
        file_size = metadata["file_size"]

        # Find the quarantined file
        quarantine_files = list(quarantine_dir.glob("*"))
        quarantine_file = None
        for f in quarantine_files:
            if f.name != "metadata.json":
                quarantine_file = f
                break

        if not quarantine_file:
            return ServiceResult.failure(
                error="Quarantined file not found in directory",
                error_code="quarantine_file_missing",
            )

        # Ensure original directory exists
        original_path.parent.mkdir(parents=True, exist_ok=True)

        # Move file back
        shutil.move(str(quarantine_file), str(original_path))

        # Update MediaFile and restore quota
        with transaction.atomic():
            media_file.scan_status = MediaFile.ScanStatus.CLEAN
            media_file.threat_name = None
            media_file.scanned_at = timezone.now()
            media_file.file = str(original_path.relative_to(settings.MEDIA_ROOT))
            media_file.save(
                update_fields=[
                    "scan_status",
                    "threat_name",
                    "scanned_at",
                    "file",
                    "updated_at",
                ]
            )

            # Restore user storage quota
            if hasattr(media_file.uploader, "profile"):
                media_file.uploader.profile.add_storage_usage(file_size)

        # Clean up quarantine directory
        shutil.rmtree(quarantine_dir)

        # Log restoration
        logger.info(
            "File restored from quarantine",
            extra={
                "event_type": "quarantine_restore",
                "media_file_id": media_file_id,
                "original_filename": metadata.get("original_filename"),
                "original_threat_name": metadata.get("threat_name"),
                "restored_path": str(original_path),
            },
        )

        return ServiceResult.success(str(original_path))

    except OSError as e:
        logger.exception(
            f"Failed to restore file from quarantine: {e}",
            extra={"media_file_id": media_file_id},
        )
        return ServiceResult.failure(
            error=f"Failed to restore file: {e}",
            error_code="restore_io_error",
        )
    except Exception as e:
        logger.exception(
            f"Unexpected error during restoration: {e}",
            extra={"media_file_id": media_file_id},
        )
        return ServiceResult.failure(
            error=str(e),
            error_code="restore_error",
        )


def list_quarantined_files(days: int | None = None) -> list[dict]:
    """
    List all quarantined files with their metadata.

    Args:
        days: If provided, only list files quarantined within this many days.

    Returns:
        List of dictionaries containing quarantine metadata.
    """
    quarantine_base = _get_quarantine_dir()
    quarantined = []

    if not quarantine_base.exists():
        return quarantined

    cutoff_date = None
    if days is not None:
        from datetime import timedelta

        cutoff_date = date.today() - timedelta(days=days)

    for date_dir in quarantine_base.iterdir():
        if not date_dir.is_dir():
            continue

        try:
            dir_date = date.fromisoformat(date_dir.name)
            if cutoff_date and dir_date < cutoff_date:
                continue
        except ValueError:
            continue

        for file_dir in date_dir.iterdir():
            if not file_dir.is_dir():
                continue

            metadata_path = file_dir / "metadata.json"
            if metadata_path.exists():
                with open(metadata_path) as f:
                    metadata = json.load(f)
                    metadata["quarantine_dir"] = str(file_dir)
                    quarantined.append(metadata)

    return quarantined


def cleanup_old_quarantine(days: int = 90) -> int:
    """
    Remove quarantine directories older than specified days.

    Args:
        days: Remove quarantine entries older than this many days.

    Returns:
        Number of entries removed.
    """
    from datetime import timedelta

    quarantine_base = _get_quarantine_dir()
    cutoff_date = date.today() - timedelta(days=days)
    removed_count = 0

    if not quarantine_base.exists():
        return 0

    for date_dir in quarantine_base.iterdir():
        if not date_dir.is_dir():
            continue

        try:
            dir_date = date.fromisoformat(date_dir.name)
            if dir_date < cutoff_date:
                shutil.rmtree(date_dir)
                removed_count += 1
                logger.info(
                    f"Removed old quarantine directory: {date_dir}",
                    extra={"date": dir_date.isoformat()},
                )
        except (ValueError, OSError) as e:
            logger.warning(f"Error processing quarantine directory {date_dir}: {e}")

    return removed_count
