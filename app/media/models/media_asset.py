"""
MediaAsset model for storing generated assets from media files.

Provides:
- Thumbnail storage for images
- Preview images for videos and documents
- Transcoded versions of videos
- Web-optimized versions of media files

Generated assets mirror the parent file's path structure for organization.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import models
from django.utils import timezone

from core.model_mixins import UUIDPrimaryKeyMixin
from core.models import BaseModel

if TYPE_CHECKING:
    pass


def get_asset_upload_path(instance: "MediaAsset", filename: str) -> str:
    """
    Generate upload path for media assets mirroring parent structure.

    Pattern: assets/{media_type}/{year}/{month}/{parent_uuid}/{asset_type}/{filename}

    This structure:
    - Mirrors the parent MediaFile organization
    - Groups assets by type for easy management
    - Uses parent UUID for clear association

    Args:
        instance: MediaAsset instance being saved.
        filename: Original filename (usually generated).

    Returns:
        Upload path string.
    """
    parent = instance.media_file
    parent_path = parent.file.name if parent.file else ""

    # Parse parent path: media_type/YYYY/MM/uuid/filename
    parts = parent_path.split("/")

    if len(parts) >= 4:
        media_type, year, month, parent_uuid = parts[:4]
    else:
        # Fallback if parent path structure differs
        now = timezone.now()
        media_type = parent.media_type
        year = str(now.year)
        month = f"{now.month:02d}"
        parent_uuid = str(parent.pk)

    return f"assets/{media_type}/{year}/{month}/{parent_uuid}/{instance.asset_type}/{filename}"


class MediaAsset(UUIDPrimaryKeyMixin, BaseModel):
    """
    Generated asset for a media file (thumbnails, previews, etc.).

    MediaAssets are created by the processing pipeline after a file
    is uploaded. Each MediaFile can have multiple assets of different
    types, but only one asset per type (enforced by unique_together).

    Attributes:
        media_file: Parent MediaFile this asset belongs to.
        asset_type: Type of asset (thumbnail, preview, etc.).
        file: The generated asset file.
        width: Width in pixels (for images/videos).
        height: Height in pixels (for images/videos).
        file_size: Size of the asset file in bytes.

    Example:
        >>> media_file = MediaFile.objects.get(id=uuid)
        >>> thumbnail = MediaAsset.objects.create(
        ...     media_file=media_file,
        ...     asset_type=MediaAsset.AssetType.THUMBNAIL,
        ...     file=thumbnail_file,
        ...     width=200,
        ...     height=150,
        ...     file_size=12345,
        ... )
    """

    # =========================================================================
    # Enums
    # =========================================================================

    class AssetType(models.TextChoices):
        """Types of generated assets."""

        # Image assets
        THUMBNAIL = "thumbnail", "Thumbnail"
        PREVIEW = "preview", "Preview"
        WEB_OPTIMIZED = "web_optimized", "Web Optimized"

        # Video assets
        POSTER = "poster", "Video Poster"
        TRANSCODED = "transcoded", "Transcoded Video"
        LOW_RES = "low_res", "Low Resolution"

        # Document assets
        PDF_PREVIEW = "pdf_preview", "PDF Preview"
        EXTRACTED_TEXT = "extracted_text", "Extracted Text"

    # =========================================================================
    # Fields
    # =========================================================================

    media_file = models.ForeignKey(
        "media.MediaFile",
        on_delete=models.CASCADE,
        related_name="assets",
        help_text="Parent media file this asset belongs to",
    )

    asset_type = models.CharField(
        max_length=30,
        choices=AssetType.choices,
        help_text="Type of generated asset",
    )

    file = models.FileField(
        upload_to=get_asset_upload_path,
        help_text="The generated asset file",
    )

    width = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Width in pixels (for images/videos)",
    )

    height = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Height in pixels (for images/videos)",
    )

    file_size = models.BigIntegerField(
        help_text="Size of the asset file in bytes",
    )

    # =========================================================================
    # Meta
    # =========================================================================

    class Meta:
        """Model metadata."""

        verbose_name = "Media Asset"
        verbose_name_plural = "Media Assets"
        ordering = ["asset_type", "-created_at"]

        # Ensure only one asset per type per media file
        constraints = [
            models.UniqueConstraint(
                fields=["media_file", "asset_type"],
                name="unique_asset_type_per_media_file",
            ),
        ]

        indexes = [
            models.Index(
                fields=["media_file", "asset_type"],
                name="idx_asset_by_type",
            ),
        ]

    # =========================================================================
    # Methods
    # =========================================================================

    def __str__(self) -> str:
        """Return string representation."""
        return f"{self.asset_type} for {self.media_file_id}"

    @property
    def dimensions(self) -> str | None:
        """
        Return dimensions as a formatted string.

        Returns:
            String like "200x150" or None if dimensions not set.
        """
        if self.width and self.height:
            return f"{self.width}x{self.height}"
        return None
