"""
MediaFile model for storing user-uploaded files.

Provides:
- UUID primary key for security
- Content-based media type classification
- Soft delete support
- Version tracking fields (logic deferred)
- Processing and scan status tracking
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.managers import SoftDeleteManager
from core.model_mixins import MetadataMixin, SoftDeleteMixin, UUIDPrimaryKeyMixin
from core.models import BaseModel

if TYPE_CHECKING:
    from django.core.files.uploadedfile import UploadedFile


def media_upload_path(instance: "MediaFile", filename: str) -> str:
    """
    Generate upload path for media files.

    Pattern: media_type/YYYY/MM/uuid/filename

    This structure:
    - Organizes by media type for easy management
    - Partitions by date for retention policies
    - Uses UUID for uniqueness and security
    """
    now = timezone.now()
    return f"{instance.media_type}/{now.year}/{now.month:02d}/{instance.pk}/{filename}"


class MediaFile(UUIDPrimaryKeyMixin, SoftDeleteMixin, MetadataMixin, BaseModel):
    """
    Model for storing user-uploaded media files.

    Attributes:
        file: The actual uploaded file.
        original_filename: The original name of the uploaded file.
        media_type: Category (image, video, document, audio, other).
        mime_type: Detected MIME type of the file.
        file_size: Size of the file in bytes.
        uploader: User who uploaded the file.
        visibility: Access level (private, shared, internal).

    Processing Fields (columns only, logic deferred):
        processing_status: Current processing state.
        processing_attempts: Number of processing retries.
        processing_error: Error message if processing failed.
        processing_priority: Priority in processing queue.
        processing_started_at: When processing began.
        processing_completed_at: When processing finished.

    Scan Fields (columns only, logic deferred):
        scan_status: Antivirus scan state.
        threat_name: Name of detected threat if infected.
        scanned_at: When the file was scanned.

    Version Fields (columns only, logic deferred):
        version: Version number of this file.
        version_group: Reference to the original file in a version chain.
        is_current: Whether this is the current version.
    """

    # =========================================================================
    # Enums
    # =========================================================================

    class MediaType(models.TextChoices):
        """Media type categories."""

        IMAGE = "image", "Image"
        VIDEO = "video", "Video"
        DOCUMENT = "document", "Document"
        AUDIO = "audio", "Audio"
        OTHER = "other", "Other"

    class Visibility(models.TextChoices):
        """Access level for files."""

        PRIVATE = "private", "Private"
        SHARED = "shared", "Shared"
        INTERNAL = "internal", "Internal"

    class ProcessingStatus(models.TextChoices):
        """Processing pipeline status."""

        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    class ProcessingPriority(models.TextChoices):
        """Processing queue priority."""

        LOW = "low", "Low"
        NORMAL = "normal", "Normal"
        HIGH = "high", "High"

    class ScanStatus(models.TextChoices):
        """Antivirus scan status."""

        PENDING = "pending", "Pending"
        CLEAN = "clean", "Clean"
        INFECTED = "infected", "Infected"
        ERROR = "error", "Error"

    # =========================================================================
    # Core Fields
    # =========================================================================

    file = models.FileField(
        upload_to=media_upload_path,
        help_text="The uploaded media file",
    )

    original_filename = models.CharField(
        max_length=255,
        help_text="Original filename from the upload",
    )

    media_type = models.CharField(
        max_length=20,
        choices=MediaType.choices,
        db_index=True,
        help_text="Category of media (image, video, document, audio, other)",
    )

    mime_type = models.CharField(
        max_length=127,
        help_text="Detected MIME type (e.g., image/jpeg, application/pdf)",
    )

    file_size = models.BigIntegerField(
        help_text="File size in bytes",
    )

    uploader = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="media_files",
        help_text="User who uploaded the file",
    )

    visibility = models.CharField(
        max_length=20,
        choices=Visibility.choices,
        default=Visibility.PRIVATE,
        db_index=True,
        help_text="Access level for this file",
    )

    # =========================================================================
    # Processing Fields (columns only, logic deferred)
    # =========================================================================

    processing_status = models.CharField(
        max_length=20,
        choices=ProcessingStatus.choices,
        default=ProcessingStatus.PENDING,
        db_index=True,
        help_text="Current processing pipeline status",
    )

    processing_attempts = models.PositiveIntegerField(
        default=0,
        help_text="Number of processing attempts",
    )

    processing_error = models.TextField(
        blank=True,
        null=True,
        help_text="Error message if processing failed",
    )

    processing_priority = models.CharField(
        max_length=20,
        choices=ProcessingPriority.choices,
        default=ProcessingPriority.NORMAL,
        help_text="Priority in processing queue",
    )

    processing_started_at = models.DateTimeField(
        blank=True,
        null=True,
        help_text="When processing started",
    )

    processing_completed_at = models.DateTimeField(
        blank=True,
        null=True,
        help_text="When processing completed",
    )

    # =========================================================================
    # Scan Fields (columns only, logic deferred)
    # =========================================================================

    scan_status = models.CharField(
        max_length=20,
        choices=ScanStatus.choices,
        default=ScanStatus.PENDING,
        db_index=True,
        help_text="Antivirus scan status",
    )

    threat_name = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Name of detected threat if infected",
    )

    scanned_at = models.DateTimeField(
        blank=True,
        null=True,
        help_text="When the file was scanned",
    )

    # =========================================================================
    # Version Fields (columns only, logic deferred)
    # =========================================================================

    version = models.PositiveIntegerField(
        default=1,
        help_text="Version number of this file",
    )

    version_group = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="versions",
        help_text="Reference to the original file in a version chain",
    )

    is_current = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Whether this is the current version",
    )

    # =========================================================================
    # Managers
    # =========================================================================

    objects = SoftDeleteManager()
    all_objects = models.Manager()

    # =========================================================================
    # Meta
    # =========================================================================

    class Meta:
        """Model metadata."""

        verbose_name = "Media File"
        verbose_name_plural = "Media Files"
        ordering = ["-created_at"]

        indexes = [
            models.Index(fields=["uploader", "media_type"]),
            models.Index(fields=["uploader", "created_at"]),
            models.Index(fields=["visibility", "is_current"]),
            models.Index(fields=["version_group", "version"]),
        ]

        constraints = [
            models.CheckConstraint(
                check=models.Q(file_size__gt=0),
                name="media_file_size_positive",
            ),
            models.CheckConstraint(
                check=models.Q(version__gte=1),
                name="media_file_version_at_least_one",
            ),
        ]

    # =========================================================================
    # Methods
    # =========================================================================

    def __str__(self) -> str:
        """Return string representation."""
        return f"{self.original_filename} ({self.media_type})"

    @classmethod
    def create_from_upload(
        cls,
        file: "UploadedFile",
        uploader: Any,
        media_type: str,
        mime_type: str,
        visibility: str = "private",
        metadata: dict[str, Any] | None = None,
    ) -> "MediaFile":
        """
        Factory method to create a MediaFile from an uploaded file.

        This method handles:
        - Extracting original filename from the upload
        - Calculating file size
        - Setting default values

        Args:
            file: The uploaded file object.
            uploader: User uploading the file.
            media_type: Category of the file.
            mime_type: Detected MIME type.
            visibility: Access level (default: private).
            metadata: Optional metadata dict.

        Returns:
            Created and saved MediaFile instance.
        """
        media_file = cls(
            file=file,
            original_filename=file.name,
            media_type=media_type,
            mime_type=mime_type,
            file_size=file.size,
            uploader=uploader,
            visibility=visibility,
        )

        if metadata:
            media_file.metadata = metadata

        media_file.save()
        return media_file
