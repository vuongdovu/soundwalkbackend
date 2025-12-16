"""
MediaFileShare model for explicit file sharing between users.

Provides:
- Explicit permission grants from owners to specific users
- Expiration support for time-limited access
- Access level control (view vs download)
- Shares apply to version groups, not individual versions
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.model_mixins import UUIDPrimaryKeyMixin
from core.models import BaseModel

if TYPE_CHECKING:
    pass


class MediaFileShare(UUIDPrimaryKeyMixin, BaseModel):
    """
    Model representing an explicit share grant for a media file.

    Shares are created when a file owner wants to give another user
    access to their file. The share grants access to the entire
    version group - recipients can see all versions of the file.

    Attributes:
        media_file: The file being shared (always the version_group root).
        shared_by: User who created the share (must be file owner).
        shared_with: User receiving access.
        can_download: If True, recipient can download; if False, view only.
        expires_at: Optional expiration datetime.
        message: Optional message from the sharer.
    """

    media_file = models.ForeignKey(
        "media.MediaFile",
        on_delete=models.CASCADE,
        related_name="shares",
        help_text="The file being shared (version group root)",
    )

    shared_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="shares_created",
        help_text="User who created this share",
    )

    shared_with = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="shares_received",
        help_text="User receiving access to the file",
    )

    can_download = models.BooleanField(
        default=True,
        help_text="Whether recipient can download (vs view-only)",
    )

    expires_at = models.DateTimeField(
        blank=True,
        null=True,
        db_index=True,
        help_text="When this share expires (null = never)",
    )

    message = models.TextField(
        blank=True,
        null=True,
        max_length=500,
        help_text="Optional message from the sharer",
    )

    class Meta:
        """Model metadata."""

        verbose_name = "Media File Share"
        verbose_name_plural = "Media File Shares"
        ordering = ["-created_at"]

        indexes = [
            models.Index(fields=["shared_with", "expires_at"]),
            models.Index(fields=["media_file", "shared_with"]),
        ]

        constraints = [
            # Prevent duplicate shares for the same file/user combination
            models.UniqueConstraint(
                fields=["media_file", "shared_with"],
                name="media_share_unique_file_user",
            ),
        ]

    def __str__(self) -> str:
        """Return string representation."""
        return f"Share: {self.media_file} -> {self.shared_with}"

    @property
    def is_expired(self) -> bool:
        """
        Check if this share has expired.

        Returns:
            True if expires_at is set and in the past, False otherwise.
        """
        if self.expires_at is None:
            return False
        return timezone.now() > self.expires_at

    @property
    def is_valid(self) -> bool:
        """
        Check if this share is currently valid (not expired).

        Returns:
            True if share is valid, False if expired.
        """
        return not self.is_expired
