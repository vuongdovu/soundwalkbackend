"""
Base module for media processors.

Provides shared exceptions, utilities, and constants used across
all media type processors (image, video, document).

Exception Hierarchy:
    ProcessingError (base)
    ├── PermanentProcessingError (don't retry - corrupted, unsupported)
    └── TransientProcessingError (retry - timeout, I/O, temporary failure)

Usage:
    from media.processors.base import (
        PermanentProcessingError,
        TransientProcessingError,
        PROCESSING_TIMEOUT,
    )

    try:
        process_file(media_file)
    except PermanentProcessingError:
        # Mark as failed, don't retry
        pass
    except TransientProcessingError:
        # Allow Celery to retry
        raise
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from media.models import MediaAsset


# =============================================================================
# Constants
# =============================================================================

# Default processing timeout in seconds (30 minutes)
PROCESSING_TIMEOUT = 1800

# Video-specific timeouts
VIDEO_METADATA_TIMEOUT = 60  # 1 minute for metadata extraction
VIDEO_POSTER_TIMEOUT = 120  # 2 minutes for poster frame extraction

# Document-specific timeouts
DOCUMENT_CONVERSION_TIMEOUT = 300  # 5 minutes for LibreOffice conversion
DOCUMENT_TEXT_EXTRACTION_TIMEOUT = 60  # 1 minute for text extraction

# Image processing constants
THUMBNAIL_SIZE = (200, 200)
PREVIEW_SIZE = (800, 800)
WEB_OPTIMIZED_MAX_SIZE = (2048, 2048)
WEBP_QUALITY = 80
WEBP_QUALITY_WEB_OPTIMIZED = 75  # Slightly more compressed for web

# Document thumbnail settings
DOCUMENT_THUMBNAIL_DPI = 72
DOCUMENT_THUMBNAIL_SIZE = (200, 200)

# Video poster settings
POSTER_MAX_WIDTH = 1280
POSTER_FRAME_POSITIONS = [0.1, 0.25, 0.5]  # Try 10%, 25%, 50% of duration

# Text extraction limits
MAX_TEXT_EXTRACTION_PAGES = 50  # Limit for very large documents


# =============================================================================
# Exceptions
# =============================================================================


class ProcessingError(Exception):
    """
    Base exception for all media processing errors.

    This is the parent class for both permanent and transient errors.
    Catching this class will catch all processing-related exceptions.
    """

    pass


class PermanentProcessingError(ProcessingError):
    """
    Error that should not be retried.

    Raised when processing fails due to:
    - Corrupted or invalid file content
    - Unsupported file format/codec
    - Password-protected/encrypted files
    - Files exceeding size/complexity limits

    Tasks should NOT retry when this exception is raised.
    The file should be marked as FAILED and require manual intervention.

    Example:
        >>> if is_corrupted(file):
        ...     raise PermanentProcessingError("File is corrupted")
    """

    pass


class TransientProcessingError(ProcessingError):
    """
    Error that may succeed on retry.

    Raised when processing fails due to:
    - Timeout during processing
    - Temporary I/O errors (storage unavailable)
    - External service unavailable (LibreOffice, FFmpeg)
    - Memory pressure (may succeed with fewer concurrent tasks)

    Tasks SHOULD retry when this exception is raised.
    Celery's default retry mechanism will handle these.

    Example:
        >>> if timeout_exceeded:
        ...     raise TransientProcessingError("Processing timed out")
    """

    pass


# =============================================================================
# Result Classes
# =============================================================================


@dataclass
class ProcessingResult:
    """
    Result of a processing operation.

    Used to track success/failure of individual asset generation
    without stopping the entire processing pipeline. Supports
    graceful degradation where some assets may fail while others succeed.

    Attributes:
        success: Whether the operation succeeded.
        asset: The generated MediaAsset, if successful.
        error: Error message if operation failed.
        metadata: Extracted metadata dictionary, if applicable.

    Example:
        >>> result = generate_thumbnail(media_file)
        >>> if result.success:
        ...     print(f"Created asset: {result.asset.id}")
        ... else:
        ...     print(f"Failed: {result.error}")
    """

    success: bool
    asset: "MediaAsset | None" = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(
        cls,
        asset: "MediaAsset | None" = None,
        metadata: dict[str, Any] | None = None,
    ) -> "ProcessingResult":
        """
        Create a successful result.

        Args:
            asset: The generated MediaAsset.
            metadata: Optional extracted metadata.

        Returns:
            ProcessingResult with success=True.
        """
        return cls(
            success=True,
            asset=asset,
            metadata=metadata or {},
        )

    @classmethod
    def fail(cls, error: str) -> "ProcessingResult":
        """
        Create a failed result.

        Args:
            error: Description of what went wrong.

        Returns:
            ProcessingResult with success=False.
        """
        return cls(
            success=False,
            error=error,
        )


@dataclass
class MetadataResult:
    """
    Result of metadata extraction.

    Separate from ProcessingResult because metadata extraction
    doesn't produce an asset, only extracted information.

    Attributes:
        success: Whether extraction succeeded.
        metadata: Extracted metadata dictionary.
        error: Error message if extraction failed.

    Example:
        >>> result = extract_image_metadata(media_file)
        >>> if result.success:
        ...     media_file.metadata.update(result.metadata)
    """

    success: bool
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @classmethod
    def ok(cls, metadata: dict[str, Any]) -> "MetadataResult":
        """Create a successful metadata result."""
        return cls(success=True, metadata=metadata)

    @classmethod
    def fail(cls, error: str) -> "MetadataResult":
        """Create a failed metadata result."""
        return cls(success=False, error=error)
