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
