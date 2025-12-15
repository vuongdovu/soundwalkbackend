"""
Tests for media file validators.

These tests verify:
- Content-based MIME type detection using python-magic
- MIME type to media type mapping
- File size limit enforcement
- Empty file rejection
- Extension mismatch detection

TDD: Write these tests first, then implement validators to pass them.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING


from media.validators import (
    ALLOWED_MIME_TYPES,
    SIZE_LIMITS,
    MediaValidator,
    ValidationResult,
    validate_file_upload,
)

if TYPE_CHECKING:
    pass


class TestMimeTypeDetection:
    """Tests for content-based MIME type detection."""

    def test_detect_jpeg_mime_type(self, sample_jpeg: io.BytesIO):
        """
        JPEG files should be detected as image/jpeg from content.

        Why it matters: Core functionality - must correctly identify images.
        """
        validator = MediaValidator()
        result = validator.validate(sample_jpeg)

        assert result.is_valid is True
        assert result.mime_type == "image/jpeg"
        assert result.media_type == "image"

    def test_detect_png_mime_type(self, sample_png: io.BytesIO):
        """
        PNG files should be detected as image/png from content.

        Why it matters: PNG is a common format with different magic bytes than JPEG.
        """
        validator = MediaValidator()
        result = validator.validate(sample_png)

        assert result.is_valid is True
        assert result.mime_type == "image/png"
        assert result.media_type == "image"

    def test_detect_gif_mime_type(self, sample_gif: io.BytesIO):
        """
        GIF files should be detected as image/gif from content.

        Why it matters: GIF has unique magic bytes (GIF87a or GIF89a).
        """
        validator = MediaValidator()
        result = validator.validate(sample_gif)

        assert result.is_valid is True
        assert result.mime_type == "image/gif"
        assert result.media_type == "image"

    def test_detect_pdf_mime_type(self, sample_pdf: io.BytesIO):
        """
        PDF files should be detected as application/pdf from content.

        Why it matters: Documents are a key media type with unique magic bytes.
        """
        validator = MediaValidator()
        result = validator.validate(sample_pdf)

        assert result.is_valid is True
        assert result.mime_type == "application/pdf"
        assert result.media_type == "document"

    def test_detect_text_mime_type(self, sample_txt: io.BytesIO):
        """
        Plain text files should be detected as text/plain.

        Why it matters: Text files don't have magic bytes, relies on content analysis.
        """
        validator = MediaValidator()
        result = validator.validate(sample_txt)

        assert result.is_valid is True
        assert result.mime_type == "text/plain"
        assert result.media_type == "document"


class TestMimeTypeRejection:
    """Tests for rejecting disallowed MIME types."""

    def test_reject_executable_file(self, executable_file: io.BytesIO):
        """
        Executable files should be rejected regardless of extension.

        Why it matters: Security - prevents malware upload with fake extension.
        """
        validator = MediaValidator()
        result = validator.validate(executable_file)

        assert result.is_valid is False
        assert result.error_code == "MIME_TYPE_NOT_ALLOWED"
        assert "not allowed" in result.error.lower()

    def test_reject_unknown_binary(self):
        """
        Unknown binary content should be rejected.

        Why it matters: Only explicitly allowed types should be accepted.
        """
        # Random binary data that doesn't match any known format
        random_binary = io.BytesIO(b"\x00\x01\x02\x03\x04\x05" * 100)
        random_binary.name = "unknown.bin"

        validator = MediaValidator()
        result = validator.validate(random_binary)

        assert result.is_valid is False
        assert result.error_code == "MIME_TYPE_NOT_ALLOWED"


class TestFileSizeLimits:
    """Tests for file size limit enforcement."""

    def test_accept_image_under_limit(self, sample_jpeg: io.BytesIO):
        """
        Images under 25MB should be accepted.

        Why it matters: Normal images should pass validation.
        """
        validator = MediaValidator()
        result = validator.validate(sample_jpeg)

        assert result.is_valid is True

    def test_reject_oversized_image(self, oversized_image: io.BytesIO):
        """
        Images over 25MB should be rejected.

        Why it matters: Prevents storage abuse and processing issues.
        """
        validator = MediaValidator()
        result = validator.validate(oversized_image)

        assert result.is_valid is False
        assert result.error_code == "FILE_TOO_LARGE"
        assert "25" in result.error  # Should mention the limit

    def test_size_limits_match_spec(self):
        """
        Size limits should match the specification.

        Why it matters: Ensures consistent behavior with documented limits.
        """
        assert SIZE_LIMITS["image"] == 25 * 1024 * 1024  # 25MB
        assert SIZE_LIMITS["video"] == 500 * 1024 * 1024  # 500MB
        assert SIZE_LIMITS["document"] == 50 * 1024 * 1024  # 50MB
        assert SIZE_LIMITS["audio"] == 100 * 1024 * 1024  # 100MB
        assert SIZE_LIMITS["other"] == 25 * 1024 * 1024  # 25MB


class TestEmptyFileRejection:
    """Tests for empty file handling."""

    def test_reject_empty_file(self, empty_file: io.BytesIO):
        """
        Empty files should be rejected.

        Why it matters: Empty files are useless and indicate client errors.
        """
        validator = MediaValidator()
        result = validator.validate(empty_file)

        assert result.is_valid is False
        assert result.error_code == "EMPTY_FILE"
        assert "empty" in result.error.lower()


class TestExtensionMismatch:
    """Tests for extension mismatch detection."""

    def test_detect_png_with_jpg_extension(self, png_with_jpg_extension: io.BytesIO):
        """
        PNG file with .jpg extension should still be validated as PNG.

        Why it matters: Content-based detection ignores misleading extensions.
        """
        validator = MediaValidator()
        result = validator.validate(png_with_jpg_extension)

        # Should be valid (PNG is allowed)
        assert result.is_valid is True
        assert result.mime_type == "image/png"
        assert result.media_type == "image"

        # Should detect mismatch
        has_mismatch = validator.check_extension_mismatch(
            png_with_jpg_extension,
            result.mime_type,
        )
        assert has_mismatch is True

    def test_detect_pdf_with_txt_extension(self, pdf_with_txt_extension: io.BytesIO):
        """
        PDF file with .txt extension should still be validated as PDF.

        Why it matters: Content-based detection catches renamed files.
        """
        validator = MediaValidator()
        result = validator.validate(pdf_with_txt_extension)

        # Should be valid (PDF is allowed)
        assert result.is_valid is True
        assert result.mime_type == "application/pdf"
        assert result.media_type == "document"

        # Should detect mismatch
        has_mismatch = validator.check_extension_mismatch(
            pdf_with_txt_extension,
            result.mime_type,
        )
        assert has_mismatch is True

    def test_no_mismatch_when_extension_matches(self, sample_jpeg: io.BytesIO):
        """
        Files with matching extension should not report mismatch.

        Why it matters: Normal files shouldn't trigger false positives.
        """
        validator = MediaValidator()
        result = validator.validate(sample_jpeg)

        has_mismatch = validator.check_extension_mismatch(
            sample_jpeg,
            result.mime_type,
        )
        assert has_mismatch is False


class TestAllowedMimeTypes:
    """Tests for the ALLOWED_MIME_TYPES configuration."""

    def test_image_types_defined(self):
        """
        Image category should include common image formats.

        Why it matters: Ensures expected image types are supported.
        """
        image_types = ALLOWED_MIME_TYPES["image"]
        assert "image/jpeg" in image_types
        assert "image/png" in image_types
        assert "image/gif" in image_types
        assert "image/webp" in image_types

    def test_video_types_defined(self):
        """
        Video category should include common video formats.

        Why it matters: Ensures expected video types are supported.
        """
        video_types = ALLOWED_MIME_TYPES["video"]
        assert "video/mp4" in video_types
        assert "video/quicktime" in video_types
        assert "video/webm" in video_types

    def test_document_types_defined(self):
        """
        Document category should include common document formats.

        Why it matters: Ensures expected document types are supported.
        """
        doc_types = ALLOWED_MIME_TYPES["document"]
        assert "application/pdf" in doc_types
        assert "text/plain" in doc_types
        assert "text/csv" in doc_types

    def test_audio_types_defined(self):
        """
        Audio category should include common audio formats.

        Why it matters: Ensures expected audio types are supported.
        """
        audio_types = ALLOWED_MIME_TYPES["audio"]
        assert "audio/mpeg" in audio_types
        assert "audio/wav" in audio_types or "audio/x-wav" in audio_types


class TestValidationResult:
    """Tests for ValidationResult dataclass."""

    def test_valid_result_has_media_and_mime_type(self):
        """
        Valid results should include media_type and mime_type.

        Why it matters: Downstream code needs these values for processing.
        """
        result = ValidationResult(
            is_valid=True,
            media_type="image",
            mime_type="image/jpeg",
        )

        assert result.is_valid is True
        assert result.media_type == "image"
        assert result.mime_type == "image/jpeg"
        assert result.error is None
        assert result.error_code is None

    def test_invalid_result_has_error_info(self):
        """
        Invalid results should include error and error_code.

        Why it matters: API needs structured error info for responses.
        """
        result = ValidationResult(
            is_valid=False,
            error="File type not allowed",
            error_code="MIME_TYPE_NOT_ALLOWED",
        )

        assert result.is_valid is False
        assert result.error == "File type not allowed"
        assert result.error_code == "MIME_TYPE_NOT_ALLOWED"
        assert result.media_type is None
        assert result.mime_type is None


class TestConvenienceFunction:
    """Tests for the validate_file_upload convenience function."""

    def test_validate_file_upload_valid(self, sample_jpeg: io.BytesIO):
        """
        Convenience function should work for valid files.

        Why it matters: Provides simple interface for common case.
        """
        result = validate_file_upload(sample_jpeg)

        assert result.is_valid is True
        assert result.mime_type == "image/jpeg"

    def test_validate_file_upload_invalid(self, executable_file: io.BytesIO):
        """
        Convenience function should work for invalid files.

        Why it matters: Provides simple interface for rejection case.
        """
        result = validate_file_upload(executable_file)

        assert result.is_valid is False
        assert result.error_code == "MIME_TYPE_NOT_ALLOWED"


class TestCustomConfiguration:
    """Tests for custom validator configuration."""

    def test_custom_allowed_types(self, sample_jpeg: io.BytesIO):
        """
        Validator should respect custom allowed types.

        Why it matters: Allows restricting uploads in specific contexts.
        """
        # Only allow PDFs
        custom_types = {"document": {"application/pdf"}}
        validator = MediaValidator(allowed_mime_types=custom_types)

        result = validator.validate(sample_jpeg)

        # JPEG should be rejected with custom config
        assert result.is_valid is False
        assert result.error_code == "MIME_TYPE_NOT_ALLOWED"

    def test_custom_size_limits(self, sample_jpeg: io.BytesIO):
        """
        Validator should respect custom size limits.

        Why it matters: Allows setting stricter limits for specific contexts.
        """
        # 500 byte limit (smaller than the ~825 byte test JPEG)
        custom_limits = {"image": 500}
        validator = MediaValidator(size_limits=custom_limits)

        result = validator.validate(sample_jpeg)

        # Should be rejected due to size
        assert result.is_valid is False
        assert result.error_code == "FILE_TOO_LARGE"
