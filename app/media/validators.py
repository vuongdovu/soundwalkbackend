"""
Media file validators.

Provides content-based MIME type detection and validation using python-magic.
This ensures security by verifying file contents rather than trusting extensions.
"""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from typing import BinaryIO

import magic


# =============================================================================
# Configuration
# =============================================================================

ALLOWED_MIME_TYPES: dict[str, set[str]] = {
    "image": {
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
        "image/heic",
        "image/heif",
        "image/svg+xml",
    },
    "video": {
        "video/mp4",
        "video/quicktime",
        "video/x-msvideo",
        "video/webm",
        "video/x-matroska",
    },
    "document": {
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-powerpoint",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "text/plain",
        "text/csv",
        "application/rtf",
    },
    "audio": {
        "audio/mpeg",
        "audio/mp4",
        "audio/wav",
        "audio/x-wav",
        "audio/aac",
        "audio/ogg",
        "audio/flac",
        "audio/x-m4a",
    },
}

SIZE_LIMITS: dict[str, int] = {
    "image": 25 * 1024 * 1024,  # 25MB
    "video": 500 * 1024 * 1024,  # 500MB
    "document": 50 * 1024 * 1024,  # 50MB
    "audio": 100 * 1024 * 1024,  # 100MB
    "other": 25 * 1024 * 1024,  # 25MB default
}

# Map MIME types to common extensions for mismatch detection
MIME_TO_EXTENSIONS: dict[str, set[str]] = {
    "image/jpeg": {".jpg", ".jpeg", ".jpe"},
    "image/png": {".png"},
    "image/gif": {".gif"},
    "image/webp": {".webp"},
    "image/heic": {".heic"},
    "image/heif": {".heif"},
    "image/svg+xml": {".svg"},
    "video/mp4": {".mp4", ".m4v"},
    "video/quicktime": {".mov", ".qt"},
    "video/x-msvideo": {".avi"},
    "video/webm": {".webm"},
    "video/x-matroska": {".mkv"},
    "application/pdf": {".pdf"},
    "application/msword": {".doc"},
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {
        ".docx"
    },
    "application/vnd.ms-excel": {".xls"},
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {".xlsx"},
    "application/vnd.ms-powerpoint": {".ppt"},
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": {
        ".pptx"
    },
    "text/plain": {".txt", ".text", ".log"},
    "text/csv": {".csv"},
    "application/rtf": {".rtf"},
    "audio/mpeg": {".mp3", ".mpga"},
    "audio/mp4": {".m4a", ".mp4a"},
    "audio/wav": {".wav"},
    "audio/x-wav": {".wav"},
    "audio/aac": {".aac"},
    "audio/ogg": {".ogg", ".oga"},
    "audio/flac": {".flac"},
    "audio/x-m4a": {".m4a"},
}


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class ValidationResult:
    """Result of file validation.

    Attributes:
        is_valid: Whether the file passed validation.
        media_type: Category of the file (image, video, document, audio).
        mime_type: Detected MIME type of the file.
        error: Human-readable error message if validation failed.
        error_code: Machine-readable error code if validation failed.
    """

    is_valid: bool
    media_type: str | None = None
    mime_type: str | None = None
    error: str | None = None
    error_code: str | None = None


# =============================================================================
# Validator Class
# =============================================================================


class MediaValidator:
    """Validates media files using content-based MIME detection.

    Uses python-magic (libmagic) to detect file types from content rather than
    trusting file extensions. This prevents malicious files from being uploaded
    with fake extensions.

    Example:
        validator = MediaValidator()
        result = validator.validate(uploaded_file)
        if result.is_valid:
            print(f"File type: {result.media_type}, MIME: {result.mime_type}")
        else:
            print(f"Validation failed: {result.error}")
    """

    def __init__(
        self,
        allowed_mime_types: dict[str, set[str]] | None = None,
        size_limits: dict[str, int] | None = None,
    ) -> None:
        """Initialize validator with optional custom configuration.

        Args:
            allowed_mime_types: Custom mapping of media types to allowed MIME types.
            size_limits: Custom mapping of media types to size limits in bytes.
        """
        self._allowed_mime_types = allowed_mime_types or ALLOWED_MIME_TYPES
        self._size_limits = size_limits or SIZE_LIMITS
        self._magic = magic.Magic(mime=True)

    def validate(self, file: BinaryIO) -> ValidationResult:
        """Validate a file upload.

        Performs the following checks in order:
        1. Empty file check
        2. MIME type detection from content
        3. MIME type allowlist check
        4. File size limit check

        Args:
            file: File-like object to validate. Must support read() and seek().

        Returns:
            ValidationResult with validation outcome and detected file info.
        """
        # Check for empty file
        file.seek(0, 2)  # Seek to end
        file_size = file.tell()
        file.seek(0)  # Reset to beginning

        if file_size == 0:
            return ValidationResult(
                is_valid=False,
                error="File is empty",
                error_code="EMPTY_FILE",
            )

        # Detect MIME type from content
        mime_type = self._detect_mime_type(file)
        if mime_type is None:
            return ValidationResult(
                is_valid=False,
                error="Could not detect file type",
                error_code="MIME_TYPE_NOT_ALLOWED",
            )

        # Get media type category
        media_type = self._get_media_type(mime_type)
        if media_type is None:
            return ValidationResult(
                is_valid=False,
                error=f"File type '{mime_type}' is not allowed",
                error_code="MIME_TYPE_NOT_ALLOWED",
            )

        # Check size limit
        size_limit = self._size_limits.get(
            media_type, self._size_limits.get("other", 0)
        )
        if file_size > size_limit:
            limit_mb = size_limit // (1024 * 1024)
            return ValidationResult(
                is_valid=False,
                error=f"File size exceeds {limit_mb}MB limit for {media_type} files",
                error_code="FILE_TOO_LARGE",
            )

        return ValidationResult(
            is_valid=True,
            media_type=media_type,
            mime_type=mime_type,
        )

    def _detect_mime_type(self, file: BinaryIO) -> str | None:
        """Detect MIME type from file content using libmagic.

        Args:
            file: File-like object to analyze.

        Returns:
            Detected MIME type string, or None if detection failed.
        """
        file.seek(0)
        # Read first 2048 bytes for magic number detection
        header = file.read(2048)
        file.seek(0)

        if not header:
            return None

        try:
            mime_type = self._magic.from_buffer(header)
            return mime_type
        except Exception:
            return None

    def _get_media_type(self, mime_type: str) -> str | None:
        """Map a MIME type to its media category.

        Args:
            mime_type: MIME type string (e.g., 'image/jpeg').

        Returns:
            Media type category (image, video, document, audio), or None if not allowed.
        """
        for media_type, allowed_types in self._allowed_mime_types.items():
            if mime_type in allowed_types:
                return media_type
        return None

    def check_extension_mismatch(
        self,
        file: BinaryIO,
        detected_mime_type: str,
    ) -> bool:
        """Check if file extension matches detected MIME type.

        This is a warning check - files with mismatched extensions are still
        valid if their content passes validation, but the mismatch may indicate
        a renamed file or potential security concern.

        Args:
            file: File-like object with a 'name' attribute.
            detected_mime_type: MIME type detected from content.

        Returns:
            True if there's a mismatch, False if extension matches content.
        """
        filename = getattr(file, "name", None)
        if not filename:
            return False

        # Get the file extension
        ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if not ext:
            return False

        # Get expected extensions for this MIME type
        expected_extensions = MIME_TO_EXTENSIONS.get(detected_mime_type)
        if expected_extensions is None:
            # If we don't have a mapping, fall back to mimetypes module
            guessed_type, _ = mimetypes.guess_type(filename)
            if guessed_type is None:
                return False
            return guessed_type != detected_mime_type

        # Check if the actual extension is in the expected set
        return ext not in expected_extensions


# =============================================================================
# Convenience Function
# =============================================================================


def validate_file_upload(file: BinaryIO) -> ValidationResult:
    """Validate a file upload using default settings.

    This is a convenience function that creates a MediaValidator with default
    settings and validates the file.

    Args:
        file: File-like object to validate.

    Returns:
        ValidationResult with validation outcome.
    """
    validator = MediaValidator()
    return validator.validate(file)
