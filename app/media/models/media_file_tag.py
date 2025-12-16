"""
MediaFileTag model - through table for MediaFile-Tag relationships.

Provides an explicit many-to-many relationship with audit fields:
- Who applied the tag and when
- Confidence score for auto-generated tags
- Audit trail for tag management
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.db import models

from core.model_mixins import UUIDPrimaryKeyMixin
from core.models import BaseModel

if TYPE_CHECKING:
    from media.models import MediaFile, Tag


class MediaFileTag(UUIDPrimaryKeyMixin, BaseModel):
    """
    Association between MediaFile and Tag with audit metadata.

    This explicit through table provides:
    - Tracking of who applied each tag
    - Confidence scores for auto-detected tags
    - Audit trail via created_at/updated_at

    Attributes:
        media_file: The file being tagged.
        tag: The tag applied to the file.
        applied_by: User who applied the tag. NULL for auto tags.
        confidence: Confidence score (0.0-1.0) for auto-detected tags.

    Usage:
        # Apply a tag to a file
        MediaFileTag.objects.create(
            media_file=file,
            tag=tag,
            applied_by=user,
        )

        # Apply auto-detected tag with confidence
        MediaFileTag.objects.create(
            media_file=file,
            tag=auto_tag,
            confidence=0.95,
        )

        # Get all tags for a file
        tags = file.file_tags.select_related('tag').all()

        # Get all files with a specific tag
        files = tag.tagged_files.select_related('media_file').all()
    """

    media_file = models.ForeignKey(
        "media.MediaFile",
        on_delete=models.CASCADE,
        related_name="file_tags",
        help_text="The media file being tagged",
    )

    tag = models.ForeignKey(
        "media.Tag",
        on_delete=models.CASCADE,
        related_name="tagged_files",
        help_text="The tag applied to the file",
    )

    applied_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="applied_tags",
        help_text="User who applied this tag. NULL for auto-detected tags.",
    )

    confidence = models.FloatField(
        null=True,
        blank=True,
        help_text="Confidence score (0.0-1.0) for auto-detected tags",
    )

    class Meta:
        verbose_name = "Media File Tag"
        verbose_name_plural = "Media File Tags"
        ordering = ["-created_at"]

        indexes = [
            models.Index(fields=["media_file", "tag"]),
            models.Index(fields=["tag", "confidence"]),
            models.Index(fields=["applied_by", "created_at"]),
        ]

        constraints = [
            # Each tag can only be applied once per file
            models.UniqueConstraint(
                fields=["media_file", "tag"],
                name="media_file_tag_unique",
            ),
            # Confidence must be between 0 and 1 when provided
            models.CheckConstraint(
                check=(
                    models.Q(confidence__isnull=True)
                    | models.Q(confidence__gte=0.0, confidence__lte=1.0)
                ),
                name="media_file_tag_confidence_range",
            ),
        ]

    def __str__(self) -> str:
        """Return description of the tag application."""
        confidence_str = f" ({self.confidence:.0%})" if self.confidence else ""
        return f"{self.media_file.original_filename} - {self.tag.name}{confidence_str}"

    @classmethod
    def apply_tag(
        cls,
        media_file: "MediaFile",
        tag: "Tag",
        applied_by: Any = None,
        confidence: float | None = None,
    ) -> tuple["MediaFileTag", bool]:
        """
        Apply a tag to a media file (idempotent).

        If the tag is already applied, returns the existing association.
        This makes the operation safe to call multiple times.

        Args:
            media_file: The file to tag.
            tag: The tag to apply.
            applied_by: User applying the tag. Should be NULL for auto tags.
            confidence: Confidence score for auto-detected tags.

        Returns:
            Tuple of (MediaFileTag, created) where created is True if new.
        """
        return cls.objects.get_or_create(
            media_file=media_file,
            tag=tag,
            defaults={
                "applied_by": applied_by,
                "confidence": confidence,
            },
        )

    @classmethod
    def remove_tag(cls, media_file: "MediaFile", tag: "Tag") -> int:
        """
        Remove a tag from a media file.

        Args:
            media_file: The file to untag.
            tag: The tag to remove.

        Returns:
            Number of associations deleted (0 or 1).
        """
        deleted, _ = cls.objects.filter(
            media_file=media_file,
            tag=tag,
        ).delete()
        return deleted
