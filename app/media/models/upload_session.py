"""
UploadSession model for tracking chunked/resumable uploads.

Provides:
- Progress tracking for multi-part uploads
- Support for both local filesystem and S3 backends
- Session expiration and cleanup support
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.conf import settings
from django.db import models

from core.model_mixins import UUIDPrimaryKeyMixin
from core.models import BaseModel

if TYPE_CHECKING:
    pass


class UploadSession(UUIDPrimaryKeyMixin, BaseModel):
    """
    Tracks chunked upload progress for both local and S3 backends.

    A session is created when a client initiates a chunked upload and tracks
    the progress of individual chunk uploads until the file is complete.

    Attributes:
        uploader: User who initiated the upload
        filename: Original filename of the file being uploaded
        file_size: Expected total size in bytes
        mime_type: Detected MIME type of the file
        media_type: Category (image, video, document, audio, other)
        backend: Storage backend type ('local' or 's3')
        bytes_received: Total bytes received so far
        parts_completed: List of completed parts with metadata
        chunk_size: Size of each chunk in bytes
        status: Current session state
        expires_at: When this session expires

    Backend-specific fields:
        s3_key: S3 object key (S3 backend only)
        s3_upload_id: S3 multipart upload ID (S3 backend only)
        local_temp_dir: Temporary directory path (local backend only)

    Usage:
        # Create session via ChunkedUploadService
        service = get_chunked_upload_service()
        result = service.create_session(
            user=request.user,
            filename="video.mp4",
            file_size=1024*1024*100,
            mime_type="video/mp4",
            media_type="video",
        )
    """

    # =========================================================================
    # Enums
    # =========================================================================

    class Backend(models.TextChoices):
        """Storage backend types."""

        LOCAL = "local", "Local Filesystem"
        S3 = "s3", "Amazon S3"

    class Status(models.TextChoices):
        """Upload session status."""

        IN_PROGRESS = "in_progress", "In Progress"
        COMPLETED = "completed", "Completed"
        EXPIRED = "expired", "Expired"
        FAILED = "failed", "Failed"

    # =========================================================================
    # Relationships
    # =========================================================================

    uploader = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="upload_sessions",
        help_text="User who initiated the upload",
    )

    # =========================================================================
    # File Metadata
    # =========================================================================

    filename = models.CharField(
        max_length=255,
        help_text="Original filename of the file being uploaded",
    )
    file_size = models.BigIntegerField(
        help_text="Expected total file size in bytes",
    )
    mime_type = models.CharField(
        max_length=100,
        help_text="MIME type of the file",
    )
    media_type = models.CharField(
        max_length=20,
        help_text="Media type category (image, video, document, audio, other)",
    )

    # =========================================================================
    # Backend Configuration
    # =========================================================================

    backend = models.CharField(
        max_length=20,
        choices=Backend.choices,
        help_text="Storage backend type",
    )

    # S3-specific fields (nullable for local backend)
    s3_key = models.CharField(
        max_length=500,
        blank=True,
        null=True,
        help_text="S3 object key for the upload (S3 backend only)",
    )
    s3_upload_id = models.CharField(
        max_length=200,
        blank=True,
        null=True,
        help_text="S3 multipart upload ID (S3 backend only)",
    )

    # Local-specific fields (nullable for S3 backend)
    local_temp_dir = models.CharField(
        max_length=500,
        blank=True,
        null=True,
        help_text="Temporary directory path (local backend only)",
    )

    # =========================================================================
    # Progress Tracking
    # =========================================================================

    bytes_received = models.BigIntegerField(
        default=0,
        help_text="Total bytes received so far",
    )
    parts_completed = models.JSONField(
        default=list,
        blank=True,
        help_text="List of completed parts: [{'part_number': 1, 'etag': '...', 'size': ...}]",
    )
    chunk_size = models.PositiveIntegerField(
        default=5 * 1024 * 1024,  # 5MB default (S3 minimum)
        help_text="Size of each chunk in bytes",
    )

    # =========================================================================
    # Status & Expiration
    # =========================================================================

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.IN_PROGRESS,
        help_text="Current session status",
    )
    expires_at = models.DateTimeField(
        help_text="When this session expires",
    )

    # =========================================================================
    # Meta
    # =========================================================================

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["uploader", "status"],
                name="idx_upload_session_user_status",
            ),
            models.Index(
                fields=["expires_at"],
                name="idx_upload_session_expires",
            ),
            models.Index(
                fields=["status", "expires_at"],
                name="idx_upload_session_status_exp",
            ),
        ]

    def __str__(self) -> str:
        return f"UploadSession({self.filename}, {self.status})"

    # =========================================================================
    # Computed Properties
    # =========================================================================

    @property
    def total_parts(self) -> int:
        """Calculate total number of parts needed for the upload."""
        if self.file_size <= 0 or self.chunk_size <= 0:
            return 0
        return (self.file_size + self.chunk_size - 1) // self.chunk_size

    @property
    def parts_completed_count(self) -> int:
        """Number of parts that have been completed."""
        return len(self.parts_completed) if self.parts_completed else 0

    @property
    def progress_percent(self) -> float:
        """Upload progress as a percentage (0-100)."""
        if self.file_size <= 0:
            return 0.0
        return min(100.0, (self.bytes_received / self.file_size) * 100)

    @property
    def is_upload_complete(self) -> bool:
        """Check if all bytes have been received."""
        return self.bytes_received >= self.file_size

    @property
    def is_local(self) -> bool:
        """Check if this session uses local storage backend."""
        return self.backend == self.Backend.LOCAL

    @property
    def is_s3(self) -> bool:
        """Check if this session uses S3 storage backend."""
        return self.backend == self.Backend.S3

    @property
    def is_expired(self) -> bool:
        """Check if this session has expired."""
        from django.utils import timezone

        return self.expires_at <= timezone.now()

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def get_completed_part_numbers(self) -> set[int]:
        """Get set of completed part numbers."""
        if not self.parts_completed:
            return set()
        return {p["part_number"] for p in self.parts_completed}

    def get_missing_part_numbers(self) -> list[int]:
        """Get list of part numbers that haven't been uploaded yet."""
        completed = self.get_completed_part_numbers()
        all_parts = set(range(1, self.total_parts + 1))
        return sorted(all_parts - completed)

    def is_part_completed(self, part_number: int) -> bool:
        """Check if a specific part has been completed."""
        return part_number in self.get_completed_part_numbers()

    def add_completed_part(
        self,
        part_number: int,
        etag: str,
        size: int,
    ) -> None:
        """
        Add a completed part to the tracking list.

        Note: This method modifies the instance but does NOT save.
        Caller should save after calling this method.

        Args:
            part_number: The part number (1-indexed)
            etag: ETag returned by storage (for S3 completion)
            size: Size of this part in bytes
        """
        # Ensure parts_completed is a list
        if self.parts_completed is None:
            self.parts_completed = []

        # Check if part already exists (idempotency)
        existing = next(
            (p for p in self.parts_completed if p["part_number"] == part_number),
            None,
        )
        if existing:
            # Already recorded - update if different (shouldn't happen normally)
            existing["etag"] = etag
            existing["size"] = size
        else:
            # Add new part
            self.parts_completed.append(
                {
                    "part_number": part_number,
                    "etag": etag,
                    "size": size,
                }
            )
            # Keep sorted by part number for consistency
            self.parts_completed.sort(key=lambda p: p["part_number"])

        # Update bytes received
        self.bytes_received = sum(p["size"] for p in self.parts_completed)
