"""
MediaFile model for storing user-uploaded files.

Provides:
- UUID primary key for security
- Content-based media type classification
- Soft delete support
- Version tracking with self-referential version groups
- Processing and scan status tracking
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from core.managers import SoftDeleteManager
from core.model_mixins import MetadataMixin, SoftDeleteMixin, UUIDPrimaryKeyMixin
from core.models import BaseModel

if TYPE_CHECKING:
    from django.core.files.uploadedfile import UploadedFile
    from django.db.models import QuerySet


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

    Version Fields:
        version: Version number of this file (starts at 1).
        version_group: Self-referential FK to the original file. Originals point
            to themselves, enabling database constraints for versioning integrity.
        is_current: Whether this is the current (active) version.
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
    # Version Fields
    # =========================================================================

    version = models.PositiveIntegerField(
        default=1,
        help_text="Version number of this file (starts at 1 for originals)",
    )

    # Self-referential FK for version grouping. Originals point to themselves.
    # This pattern enables database constraints like "only one current per group"
    # because NULL values don't participate in unique constraints properly.
    version_group = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        related_name="versions",
        help_text="Reference to the original file in a version chain",
    )

    is_current = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Whether this is the current (active) version",
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
            # Versioning constraints
            # Only one file per version_group can have is_current=True
            models.UniqueConstraint(
                fields=["version_group"],
                condition=models.Q(is_current=True),
                name="media_file_unique_current_per_group",
            ),
            # Version numbers must be unique within a version_group
            models.UniqueConstraint(
                fields=["version_group", "version"],
                name="media_file_unique_version_in_group",
            ),
        ]

    # =========================================================================
    # Methods
    # =========================================================================

    def __str__(self) -> str:
        """Return string representation including version."""
        return f"{self.original_filename} (v{self.version})"

    def save(self, *args: Any, **kwargs: Any) -> None:
        """
        Override save to auto-populate version_group for new records.

        This ensures that even direct MediaFile.objects.create() calls
        result in valid version_group values (originals point to self).
        The self-referential pattern is necessary because NULL values
        don't participate in unique constraints properly.

        For new records without version_group set, we:
        1. Ensure the PK exists (UUID is generated before save)
        2. Set version_group to self
        3. Then save the record

        Since UUIDs are generated client-side (uuid.uuid4() default), the
        PK is available before the first save, allowing us to set the
        self-reference in a single INSERT rather than INSERT + UPDATE.
        """
        is_new = self._state.adding

        # For new records, set version_group to self before saving
        if is_new and self.version_group_id is None:
            # UUID is generated by default, so self.pk is already available
            self.version_group_id = self.pk

        super().save(*args, **kwargs)

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
        - Auto-setting version_group to self (via save override)

        Note: Quota checking should be done by the caller before invoking
        this method. Use serializers or check profile.can_upload() first.

        Args:
            file: The uploaded file object.
            uploader: User uploading the file.
            media_type: Category of the file.
            mime_type: Detected MIME type.
            visibility: Access level (default: private).
            metadata: Optional metadata dict.

        Returns:
            Created and saved MediaFile instance with version=1,
            is_current=True, and version_group pointing to itself.
        """
        media_file = cls(
            file=file,
            original_filename=file.name,
            media_type=media_type,
            mime_type=mime_type,
            file_size=file.size,
            uploader=uploader,
            visibility=visibility,
            version=1,
            is_current=True,
        )

        if metadata:
            media_file.metadata = metadata

        media_file.save()
        # version_group is auto-set in save() override
        return media_file

    def create_new_version(
        self,
        new_file: "UploadedFile",
        requesting_user: Any,
    ) -> "MediaFile":
        """
        Create a new version of this file.

        This method:
        - Verifies the requesting user is the original uploader
        - Checks storage quota before creating
        - Marks all prior versions as not current
        - Creates new version with incremented version number
        - Updates user's storage quota

        Uses transaction with select_for_update to prevent race conditions
        when multiple versions are created simultaneously.

        Args:
            new_file: The uploaded file for the new version.
            requesting_user: User attempting to create the version.

        Returns:
            The newly created MediaFile version.

        Raises:
            PermissionError: If requesting_user is not the uploader.
            ValidationError: If storage quota would be exceeded.
        """
        from django.core.exceptions import ValidationError

        # Ownership check - only the original uploader can create versions
        if self.uploader_id != requesting_user.id:
            raise PermissionError(
                "Only the original uploader can create new versions of this file."
            )

        # Quota check - verify user has enough storage remaining
        if hasattr(requesting_user, "profile"):
            if not requesting_user.profile.can_upload(new_file.size):
                raise ValidationError(
                    "Storage quota exceeded. Please free up space or upgrade your plan."
                )

        with transaction.atomic():
            # Lock the version group root to prevent race conditions
            # when multiple versions are created simultaneously
            group = MediaFile.all_objects.select_for_update().get(
                pk=self.version_group_id
            )

            # Mark all versions in this group as not current
            MediaFile.all_objects.filter(version_group=group).update(is_current=False)

            # Get next version number (max + 1)
            max_version = (
                MediaFile.all_objects.filter(version_group=group).aggregate(
                    max_v=models.Max("version")
                )["max_v"]
                or 0
            )

            # Create new version - explicitly set version_group
            # The save() override only sets version_group when it's None
            new_version = MediaFile(
                file=new_file,
                original_filename=new_file.name,
                version_group=group,
                version=max_version + 1,
                is_current=True,
                uploader=self.uploader,  # Preserve original uploader
                media_type=self.media_type,  # Preserve media type
                mime_type=self.mime_type,  # Preserve MIME type
                file_size=new_file.size,
                visibility=self.visibility,  # Preserve visibility
            )
            new_version.save()

            # Update quota after successful save
            if hasattr(requesting_user, "profile"):
                requesting_user.profile.add_storage_usage(new_file.size)

            return new_version

    def get_version_history(self) -> "QuerySet[MediaFile]":
        """
        Get all versions in this file's version group.

        Returns:
            QuerySet of MediaFile ordered by version number descending
            (most recent first).
        """
        return MediaFile.all_objects.filter(version_group=self.version_group).order_by(
            "-version"
        )

    @property
    def is_original(self) -> bool:
        """
        Check if this is the original (first) version.

        Returns:
            True if version == 1, False otherwise.
        """
        return self.version == 1

    # =========================================================================
    # Soft Delete Hooks (Quota Tracking)
    # =========================================================================

    def on_soft_delete(self) -> None:
        """
        Hook called during soft_delete to decrement user's storage quota.

        Uses the atomic subtract_storage_usage method from Profile to ensure
        thread-safe quota updates even under concurrent deletions.
        """
        if hasattr(self.uploader, "profile"):
            self.uploader.profile.subtract_storage_usage(self.file_size)

    def on_restore(self) -> None:
        """
        Hook called during restore to increment user's storage quota.

        Uses the atomic add_storage_usage method from Profile to ensure
        thread-safe quota updates even under concurrent restores.
        """
        if hasattr(self.uploader, "profile"):
            self.uploader.profile.add_storage_usage(self.file_size)
