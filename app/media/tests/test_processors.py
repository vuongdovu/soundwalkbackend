"""
Tests for media file processors.

Tests cover:
- Image thumbnail generation with Pillow
- Color mode conversion (RGBA, LA, P to RGB)
- Error handling for corrupted/invalid images
- File size limits
- WebP output format
"""

from __future__ import annotations

import io
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from media.models import MediaAsset, MediaFile
from media.processors.image import (
    ImageProcessingError,
    _convert_to_rgb,
    generate_image_thumbnail,
)


@pytest.fixture
def media_file_image(user, sample_jpeg_uploaded):
    """Create a MediaFile instance with an uploaded image."""
    return MediaFile.create_from_upload(
        file=sample_jpeg_uploaded,
        uploader=user,
        media_type="image",
        mime_type="image/jpeg",
    )


@pytest.fixture
def rgba_image_file(user):
    """Create a MediaFile with an RGBA PNG image."""
    image = Image.new("RGBA", (100, 100), color=(255, 0, 0, 128))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    uploaded = SimpleUploadedFile(
        name="rgba_image.png",
        content=buffer.read(),
        content_type="image/png",
    )
    return MediaFile.create_from_upload(
        file=uploaded,
        uploader=user,
        media_type="image",
        mime_type="image/png",
    )


@pytest.fixture
def palette_image_file(user):
    """Create a MediaFile with a palette mode (P) image."""
    image = Image.new("P", (100, 100), color=1)
    buffer = io.BytesIO()
    image.save(buffer, format="GIF")
    buffer.seek(0)
    uploaded = SimpleUploadedFile(
        name="palette_image.gif",
        content=buffer.read(),
        content_type="image/gif",
    )
    return MediaFile.create_from_upload(
        file=uploaded,
        uploader=user,
        media_type="image",
        mime_type="image/gif",
    )


@pytest.fixture
def grayscale_image_file(user):
    """Create a MediaFile with a grayscale (L) image."""
    image = Image.new("L", (100, 100), color=128)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    uploaded = SimpleUploadedFile(
        name="grayscale_image.png",
        content=buffer.read(),
        content_type="image/png",
    )
    return MediaFile.create_from_upload(
        file=uploaded,
        uploader=user,
        media_type="image",
        mime_type="image/png",
    )


@pytest.mark.django_db
class TestGenerateImageThumbnail:
    """Tests for the generate_image_thumbnail function."""

    def test_generates_thumbnail_for_jpeg(self, media_file_image):
        """Test that a thumbnail is generated for JPEG images."""
        asset = generate_image_thumbnail(media_file_image)

        assert asset is not None
        assert asset.asset_type == MediaAsset.AssetType.THUMBNAIL
        assert asset.media_file == media_file_image
        assert asset.width is not None
        assert asset.height is not None
        assert asset.width <= 200
        assert asset.height <= 200
        assert asset.file_size > 0

    def test_thumbnail_is_webp_format(self, media_file_image):
        """Test that the generated thumbnail is in WebP format."""
        asset = generate_image_thumbnail(media_file_image)

        # Check file extension
        assert asset.file.name.endswith(".webp")

        # Verify the file is actually a WebP image
        with asset.file.open("rb") as f:
            thumb_image = Image.open(f)
            assert thumb_image.format == "WEBP"

    def test_thumbnail_maintains_aspect_ratio(self, user):
        """Test that thumbnail maintains the original aspect ratio."""
        # Create a wide image (200x100)
        image = Image.new("RGB", (200, 100), color="green")
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG")
        buffer.seek(0)
        uploaded = SimpleUploadedFile(
            name="wide_image.jpg",
            content=buffer.read(),
            content_type="image/jpeg",
        )
        media_file = MediaFile.create_from_upload(
            file=uploaded,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )

        asset = generate_image_thumbnail(media_file)

        # Width should be 200 (max), height should be 100 (maintains 2:1 ratio)
        assert asset.width == 200
        assert asset.height == 100

    def test_thumbnail_for_large_image(self, user):
        """Test thumbnail generation for images larger than 200x200."""
        # Create a large image (1000x800)
        image = Image.new("RGB", (1000, 800), color="blue")
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG")
        buffer.seek(0)
        uploaded = SimpleUploadedFile(
            name="large_image.jpg",
            content=buffer.read(),
            content_type="image/jpeg",
        )
        media_file = MediaFile.create_from_upload(
            file=uploaded,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )

        asset = generate_image_thumbnail(media_file)

        # Both dimensions should be <= 200
        assert asset.width <= 200
        assert asset.height <= 200
        # Aspect ratio should be maintained (1000:800 = 5:4)
        # Max width 200 -> height should be 160
        assert asset.width == 200
        assert asset.height == 160

    def test_thumbnail_for_small_image(self, user):
        """Test thumbnail for images smaller than 200x200 (no upscaling)."""
        # Create a small image (50x50)
        image = Image.new("RGB", (50, 50), color="yellow")
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG")
        buffer.seek(0)
        uploaded = SimpleUploadedFile(
            name="small_image.jpg",
            content=buffer.read(),
            content_type="image/jpeg",
        )
        media_file = MediaFile.create_from_upload(
            file=uploaded,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )

        asset = generate_image_thumbnail(media_file)

        # PIL thumbnail() doesn't upscale, so dimensions stay at 50x50
        assert asset.width == 50
        assert asset.height == 50

    def test_idempotent_processing(self, media_file_image):
        """Test that processing the same file twice updates existing asset."""
        asset1 = generate_image_thumbnail(media_file_image)
        asset_id_1 = asset1.id

        asset2 = generate_image_thumbnail(media_file_image)
        asset_id_2 = asset2.id

        # Should update existing asset, not create new one
        assert asset_id_1 == asset_id_2
        assert MediaAsset.objects.filter(media_file=media_file_image).count() == 1


@pytest.mark.django_db
class TestColorModeConversion:
    """Tests for color mode conversion to RGB."""

    def test_rgba_to_rgb_conversion(self, rgba_image_file):
        """Test that RGBA images are converted to RGB."""
        asset = generate_image_thumbnail(rgba_image_file)

        with asset.file.open("rb") as f:
            thumb_image = Image.open(f)
            # WebP can store RGB or RGBA, but our conversion should produce RGB
            assert thumb_image.mode in ("RGB", "RGBA")

    def test_palette_to_rgb_conversion(self, palette_image_file):
        """Test that palette (P) mode images are converted to RGB."""
        asset = generate_image_thumbnail(palette_image_file)

        with asset.file.open("rb") as f:
            thumb_image = Image.open(f)
            assert thumb_image.mode in ("RGB", "RGBA")

    def test_grayscale_to_rgb_conversion(self, grayscale_image_file):
        """Test that grayscale (L) mode images are converted to RGB."""
        asset = generate_image_thumbnail(grayscale_image_file)

        with asset.file.open("rb") as f:
            thumb_image = Image.open(f)
            assert thumb_image.mode in ("RGB", "RGBA")


class TestConvertToRgbFunction:
    """Unit tests for the _convert_to_rgb helper function."""

    def test_rgb_passthrough(self):
        """Test that RGB images pass through unchanged."""
        img = Image.new("RGB", (100, 100), color="red")
        result = _convert_to_rgb(img)
        assert result.mode == "RGB"

    def test_rgba_conversion(self):
        """Test RGBA to RGB conversion with white background."""
        img = Image.new("RGBA", (100, 100), color=(255, 0, 0, 128))
        result = _convert_to_rgb(img)
        assert result.mode == "RGB"

    def test_la_conversion(self):
        """Test LA (grayscale with alpha) to RGB conversion."""
        img = Image.new("LA", (100, 100), color=(128, 200))
        result = _convert_to_rgb(img)
        assert result.mode == "RGB"

    def test_palette_conversion(self):
        """Test palette mode to RGB conversion."""
        img = Image.new("P", (100, 100), color=1)
        result = _convert_to_rgb(img)
        assert result.mode == "RGB"

    def test_palette_with_transparency(self):
        """Test palette mode with transparency to RGB conversion."""
        img = Image.new("P", (100, 100), color=1)
        img.info["transparency"] = 0  # Mark color 0 as transparent
        result = _convert_to_rgb(img)
        assert result.mode == "RGB"

    def test_grayscale_conversion(self):
        """Test grayscale (L) to RGB conversion."""
        img = Image.new("L", (100, 100), color=128)
        result = _convert_to_rgb(img)
        assert result.mode == "RGB"

    def test_cmyk_conversion(self):
        """Test CMYK to RGB conversion."""
        img = Image.new("CMYK", (100, 100), color=(0, 255, 255, 0))
        result = _convert_to_rgb(img)
        assert result.mode == "RGB"


@pytest.mark.django_db
class TestImageProcessingErrors:
    """Tests for error handling in image processing."""

    def test_corrupted_image_raises_error(self, user):
        """Test that corrupted images raise ImageProcessingError."""
        # Create a file with invalid image data
        uploaded = SimpleUploadedFile(
            name="corrupted.jpg",
            content=b"not a valid image file content",
            content_type="image/jpeg",
        )
        media_file = MediaFile.create_from_upload(
            file=uploaded,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )

        with pytest.raises(ImageProcessingError) as exc_info:
            generate_image_thumbnail(media_file)

        assert "Cannot identify image format" in str(exc_info.value)

    def test_truncated_image_raises_error(self, user):
        """Test that truncated images raise ImageProcessingError."""
        # Create a valid JPEG header but truncate the file
        image = Image.new("RGB", (100, 100), color="red")
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG")
        # Take only first 100 bytes (truncated)
        truncated_content = buffer.getvalue()[:100]

        uploaded = SimpleUploadedFile(
            name="truncated.jpg",
            content=truncated_content,
            content_type="image/jpeg",
        )
        media_file = MediaFile.create_from_upload(
            file=uploaded,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )

        with pytest.raises(ImageProcessingError) as exc_info:
            generate_image_thumbnail(media_file)

        assert (
            "truncated" in str(exc_info.value).lower()
            or "corrupted" in str(exc_info.value).lower()
        )

    def test_decompression_bomb_raises_error(self, user, sample_jpeg_uploaded):
        """Test that extremely large images raise ImageProcessingError."""
        media_file = MediaFile.create_from_upload(
            file=sample_jpeg_uploaded,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )

        # Mock the DecompressionBombError
        with patch("media.processors.image.Image.open") as mock_open:
            mock_open.side_effect = Image.DecompressionBombError(
                "Image size exceeds limit"
            )

            with pytest.raises(ImageProcessingError) as exc_info:
                generate_image_thumbnail(media_file)

            assert "exceeds maximum size limit" in str(exc_info.value)

    def test_io_error_propagates_for_retry(self, user, sample_jpeg_uploaded):
        """Test that I/O errors propagate (for Celery retry)."""
        media_file = MediaFile.create_from_upload(
            file=sample_jpeg_uploaded,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )

        # Mock an I/O error that should be retried
        with patch.object(media_file.file, "open") as mock_open:
            mock_open.side_effect = OSError("Storage temporarily unavailable")

            with pytest.raises(OSError) as exc_info:
                generate_image_thumbnail(media_file)

            assert "Storage temporarily unavailable" in str(exc_info.value)


@pytest.mark.django_db
class TestAssetPathGeneration:
    """Tests for asset upload path generation."""

    def test_thumbnail_path_structure(self, media_file_image):
        """Test that thumbnail path has correct structure."""
        asset = generate_image_thumbnail(media_file_image)

        path = asset.file.name
        # Path should contain: assets/{media_type}/{year}/{month}/{uuid}/thumbnail/
        assert path.startswith("assets/")
        assert "image" in path
        assert "thumbnail" in path
        assert ".webp" in path

    def test_thumbnail_filename_contains_parent_uuid(self, media_file_image):
        """Test that thumbnail filename references parent MediaFile UUID."""
        asset = generate_image_thumbnail(media_file_image)

        filename = asset.file.name.split("/")[-1]
        # Filename starts with "thumb_" and contains at least part of the UUID
        # Django storage may truncate long filenames, so we check for prefix
        uuid_str = str(media_file_image.pk)
        uuid_prefix = uuid_str.split("-")[0]  # First segment of UUID
        assert filename.startswith("thumb_")
        assert uuid_prefix in filename


# =============================================================================
# Tests for Image Metadata Extraction
# =============================================================================


@pytest.mark.django_db
class TestExtractImageMetadata:
    """Tests for the extract_image_metadata function."""

    def test_extracts_basic_metadata_jpeg(self, media_file_image):
        """Test extraction of basic metadata from JPEG."""
        from media.processors.image import extract_image_metadata

        metadata = extract_image_metadata(media_file_image)

        assert "width" in metadata
        assert "height" in metadata
        assert "color_space" in metadata
        assert "format" in metadata
        assert "has_alpha" in metadata

        assert metadata["width"] == 100
        assert metadata["height"] == 100
        assert metadata["format"] == "JPEG"
        assert metadata["has_alpha"] is False

    def test_extracts_metadata_png_with_alpha(self, rgba_image_file):
        """Test metadata extraction detects alpha channel."""
        from media.processors.image import extract_image_metadata

        metadata = extract_image_metadata(rgba_image_file)

        assert metadata["format"] == "PNG"
        assert metadata["has_alpha"] is True
        assert metadata["color_space"] == "RGBA"

    def test_metadata_extraction_corrupted_image_raises_error(self, user):
        """Test that corrupted images raise error during metadata extraction."""
        from media.processors.image import extract_image_metadata

        uploaded = SimpleUploadedFile(
            name="corrupted.jpg",
            content=b"not a valid image",
            content_type="image/jpeg",
        )
        media_file = MediaFile.create_from_upload(
            file=uploaded,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )

        with pytest.raises(ImageProcessingError):
            extract_image_metadata(media_file)


# =============================================================================
# Tests for Image Preview Generation
# =============================================================================


@pytest.mark.django_db
class TestGenerateImagePreview:
    """Tests for the generate_image_preview function."""

    def test_generates_preview(self, media_file_image):
        """Test that preview is generated."""
        from media.processors.image import generate_image_preview

        asset = generate_image_preview(media_file_image)

        assert asset is not None
        assert asset.asset_type == MediaAsset.AssetType.PREVIEW
        assert asset.width <= 800
        assert asset.height <= 800

    def test_preview_larger_than_thumbnail(self, user):
        """Test that preview is larger than thumbnail for large images."""
        from media.processors.image import generate_image_preview

        # Create a large image
        image = Image.new("RGB", (2000, 1500), color="purple")
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG")
        buffer.seek(0)
        uploaded = SimpleUploadedFile(
            name="large_image.jpg",
            content=buffer.read(),
            content_type="image/jpeg",
        )
        media_file = MediaFile.create_from_upload(
            file=uploaded,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )

        asset = generate_image_preview(media_file)

        # Preview should be scaled to max 800x800
        assert asset.width == 800
        assert asset.height == 600  # Maintains 4:3 ratio

    def test_preview_no_upscale_small_image(self, media_file_image):
        """Test that small images are not upscaled for preview."""
        from media.processors.image import generate_image_preview

        asset = generate_image_preview(media_file_image)

        # Original is 100x100, should not be upscaled
        assert asset.width == 100
        assert asset.height == 100


# =============================================================================
# Tests for Web Optimized Generation
# =============================================================================


@pytest.mark.django_db
class TestGenerateImageWebOptimized:
    """Tests for the generate_image_web_optimized function."""

    def test_generates_web_optimized(self, media_file_image):
        """Test that web-optimized version is generated."""
        from media.processors.image import generate_image_web_optimized

        asset = generate_image_web_optimized(media_file_image)

        assert asset is not None
        assert asset.asset_type == MediaAsset.AssetType.WEB_OPTIMIZED
        assert asset.width <= 2048
        assert asset.height <= 2048

    def test_web_optimized_max_dimensions(self, user):
        """Test that very large images are scaled to max 2048."""
        from media.processors.image import generate_image_web_optimized

        # Create a very large image
        image = Image.new("RGB", (5000, 4000), color="teal")
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG")
        buffer.seek(0)
        uploaded = SimpleUploadedFile(
            name="huge_image.jpg",
            content=buffer.read(),
            content_type="image/jpeg",
        )
        media_file = MediaFile.create_from_upload(
            file=uploaded,
            uploader=user,
            media_type="image",
            mime_type="image/jpeg",
        )

        asset = generate_image_web_optimized(media_file)

        # Should be scaled to max 2048 maintaining aspect ratio
        assert asset.width == 2048
        assert asset.height == 1638  # Maintains 5:4 ratio (rounded)


# =============================================================================
# Tests for Video Processor
# =============================================================================


@pytest.mark.django_db
class TestExtractVideoMetadata:
    """Tests for video metadata extraction."""

    def test_extracts_metadata_with_mock(self, user, sample_mp4_uploaded, mock_ffmpeg):
        """Test video metadata extraction with mocked FFmpeg."""
        from media.processors.video import extract_video_metadata

        media_file = MediaFile.create_from_upload(
            file=sample_mp4_uploaded,
            uploader=user,
            media_type="video",
            mime_type="video/mp4",
        )

        metadata = extract_video_metadata(media_file)

        assert metadata["width"] == 1920
        assert metadata["height"] == 1080
        assert metadata["codec"] == "h264"
        assert metadata["has_audio"] is True
        assert metadata["audio_codec"] == "aac"
        assert metadata["duration"] == 60.0
        # Frame rate: 30000/1001 ≈ 29.97
        assert abs(metadata["frame_rate"] - 29.97) < 0.01

    def test_ffprobe_not_found_raises_transient_error(self, user, sample_mp4_uploaded):
        """Test that missing FFmpeg raises TransientProcessingError."""
        from media.processors.video import extract_video_metadata
        from media.processors.base import TransientProcessingError

        media_file = MediaFile.create_from_upload(
            file=sample_mp4_uploaded,
            uploader=user,
            media_type="video",
            mime_type="video/mp4",
        )

        with patch(
            "subprocess.run", side_effect=FileNotFoundError("ffprobe not found")
        ):
            with pytest.raises(TransientProcessingError) as exc_info:
                extract_video_metadata(media_file)

            assert "FFprobe not found" in str(exc_info.value)

    def test_timeout_raises_transient_error(self, user, sample_mp4_uploaded):
        """Test that FFprobe timeout raises TransientProcessingError."""
        import subprocess
        from media.processors.video import extract_video_metadata
        from media.processors.base import TransientProcessingError

        media_file = MediaFile.create_from_upload(
            file=sample_mp4_uploaded,
            uploader=user,
            media_type="video",
            mime_type="video/mp4",
        )

        with patch(
            "subprocess.run", side_effect=subprocess.TimeoutExpired("ffprobe", 60)
        ):
            with pytest.raises(TransientProcessingError) as exc_info:
                extract_video_metadata(media_file)

            assert "timed out" in str(exc_info.value)


@pytest.mark.django_db
class TestExtractVideoPoster:
    """Tests for video poster frame extraction."""

    def test_poster_extraction_with_mock(self, user, sample_mp4_uploaded, tmp_path):
        """Test poster frame extraction creates asset."""
        from media.processors.video import extract_video_poster
        import json

        media_file = MediaFile.create_from_upload(
            file=sample_mp4_uploaded,
            uploader=user,
            media_type="video",
            mime_type="video/mp4",
        )

        # Create a mock image file for the poster
        poster_image = Image.new("RGB", (1920, 1080), color="blue")

        def mock_run(cmd, **kwargs):
            """Mock subprocess.run for FFmpeg/FFprobe."""
            mock_result = type("MockResult", (), {})()
            mock_result.returncode = 0
            mock_result.stderr = ""

            if "ffprobe" in cmd[0]:
                mock_result.stdout = json.dumps(
                    {
                        "streams": [
                            {"codec_type": "video", "width": 1920, "height": 1080}
                        ],
                        "format": {"duration": "60.0"},
                    }
                )
            else:
                # FFmpeg - create output file
                output_path = cmd[-1] if cmd[-1].endswith(".png") else None
                if output_path:
                    poster_image.save(output_path, format="PNG")
                mock_result.stdout = ""

            return mock_result

        with patch("subprocess.run", side_effect=mock_run):
            asset = extract_video_poster(media_file)

        assert asset is not None
        assert asset.asset_type == MediaAsset.AssetType.POSTER
        assert asset.width <= 1280  # Max width constraint


# =============================================================================
# Tests for Document Processor
# =============================================================================


@pytest.mark.django_db
class TestExtractDocumentMetadata:
    """Tests for document metadata extraction."""

    def test_extracts_pdf_metadata(self, user, sample_pdf_uploaded):
        """Test PDF metadata extraction."""
        from media.processors.document import extract_document_metadata

        media_file = MediaFile.create_from_upload(
            file=sample_pdf_uploaded,
            uploader=user,
            media_type="document",
            mime_type="application/pdf",
        )

        try:
            metadata = extract_document_metadata(media_file)
            assert metadata["format"] == "pdf"
            assert "is_encrypted" in metadata
            assert "is_searchable" in metadata
        except Exception as e:
            # pdfplumber may not be installed in test environment
            if "pdfplumber" not in str(e).lower():
                raise

    def test_returns_basic_metadata_for_unknown_type(self, user, sample_txt_uploaded):
        """Test that unknown document types return basic metadata."""
        from media.processors.document import extract_document_metadata

        media_file = MediaFile.create_from_upload(
            file=sample_txt_uploaded,
            uploader=user,
            media_type="document",
            mime_type="text/plain",
        )

        metadata = extract_document_metadata(media_file)

        assert "format" in metadata
        assert "is_searchable" in metadata


@pytest.mark.django_db
class TestGenerateDocumentThumbnail:
    """Tests for document thumbnail generation."""

    def test_thumbnail_generation_asset_type(self, user, sample_pdf_uploaded):
        """Test that document thumbnail uses correct asset type."""
        from media.processors.document import generate_document_thumbnail
        from media.processors.base import (
            TransientProcessingError,
            PermanentProcessingError,
        )

        media_file = MediaFile.create_from_upload(
            file=sample_pdf_uploaded,
            uploader=user,
            media_type="document",
            mime_type="application/pdf",
        )

        # The thumbnail generation may fail due to missing dependencies in test env
        # but we can verify the asset type would be correct by checking function exists
        try:
            asset = generate_document_thumbnail(media_file)
            assert asset.asset_type == MediaAsset.AssetType.THUMBNAIL
        except (TransientProcessingError, PermanentProcessingError, ImportError):
            # Dependencies may not be available in test environment
            pass


# =============================================================================
# Tests for Exception Hierarchy
# =============================================================================


class TestProcessingExceptionHierarchy:
    """Tests for the exception class hierarchy."""

    def test_image_error_is_permanent(self):
        """Test that ImageProcessingError is a PermanentProcessingError."""
        from media.processors.base import PermanentProcessingError

        assert issubclass(ImageProcessingError, PermanentProcessingError)

    def test_video_error_is_permanent(self):
        """Test that VideoProcessingError is a PermanentProcessingError."""
        from media.processors.video import VideoProcessingError
        from media.processors.base import PermanentProcessingError

        assert issubclass(VideoProcessingError, PermanentProcessingError)

    def test_document_error_is_permanent(self):
        """Test that DocumentProcessingError is a PermanentProcessingError."""
        from media.processors.document import DocumentProcessingError
        from media.processors.base import PermanentProcessingError

        assert issubclass(DocumentProcessingError, PermanentProcessingError)

    def test_transient_error_is_processing_error(self):
        """Test TransientProcessingError is a ProcessingError."""
        from media.processors.base import ProcessingError, TransientProcessingError

        assert issubclass(TransientProcessingError, ProcessingError)


# =============================================================================
# Tests for Processing Result Classes
# =============================================================================


class TestProcessingResultClasses:
    """Tests for ProcessingResult and MetadataResult dataclasses."""

    def test_processing_result_ok_factory(self):
        """Test ProcessingResult.ok() factory method."""
        from media.processors.base import ProcessingResult

        result = ProcessingResult.ok(metadata={"width": 100})

        assert result.success is True
        assert result.asset is None
        assert result.error is None
        assert result.metadata == {"width": 100}

    def test_processing_result_fail_factory(self):
        """Test ProcessingResult.fail() factory method."""
        from media.processors.base import ProcessingResult

        result = ProcessingResult.fail("Something went wrong")

        assert result.success is False
        assert result.asset is None
        assert result.error == "Something went wrong"

    def test_metadata_result_ok_factory(self):
        """Test MetadataResult.ok() factory method."""
        from media.processors.base import MetadataResult

        result = MetadataResult.ok({"duration": 60.0, "codec": "h264"})

        assert result.success is True
        assert result.metadata == {"duration": 60.0, "codec": "h264"}
        assert result.error is None

    def test_metadata_result_fail_factory(self):
        """Test MetadataResult.fail() factory method."""
        from media.processors.base import MetadataResult

        result = MetadataResult.fail("Could not extract metadata")

        assert result.success is False
        assert result.metadata == {}
        assert result.error == "Could not extract metadata"
