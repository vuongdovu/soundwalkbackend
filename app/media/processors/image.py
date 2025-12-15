"""
Image processing module for generating thumbnails and optimized versions.

Uses Pillow for image manipulation with proper error handling for:
- Corrupted image files
- Unsupported formats
- Images exceeding size limits
- Memory constraints

All generated images are saved as WebP for optimal web delivery.
"""

from __future__ import annotations

import logging
from io import BytesIO
from typing import TYPE_CHECKING

from django.core.files.base import ContentFile
from PIL import Image

if TYPE_CHECKING:
    from media.models import MediaAsset, MediaFile

logger = logging.getLogger(__name__)

# =============================================================================
# Constants
# =============================================================================

# Thumbnail dimensions (maintains aspect ratio)
THUMBNAIL_SIZE = (200, 200)

# Preview dimensions for larger display
PREVIEW_SIZE = (800, 800)

# WebP quality setting (0-100)
WEBP_QUALITY = 80

# Pillow's default max pixels (~178M pixels, about 13380x13380)
# We use this default - files exceeding will raise DecompressionBombError
# Image.MAX_IMAGE_PIXELS = 178956970  # Default, no need to set


# =============================================================================
# Exceptions
# =============================================================================


class ImageProcessingError(Exception):
    """
    Raised when image processing fails permanently.

    This exception indicates a non-recoverable error such as:
    - Corrupted image file
    - Unsupported image format
    - Image exceeds size limits

    Tasks should NOT retry when this exception is raised.
    """

    pass


# =============================================================================
# Processing Functions
# =============================================================================


def generate_image_thumbnail(media_file: "MediaFile") -> "MediaAsset":
    """
    Generate a thumbnail for an image file.

    Opens the source image, resizes it maintaining aspect ratio,
    converts to RGB if necessary, and saves as WebP format.

    Args:
        media_file: MediaFile instance with media_type='image'.

    Returns:
        MediaAsset instance containing the generated thumbnail.

    Raises:
        ImageProcessingError: If image cannot be processed (permanent failure).
        OSError: For transient I/O errors that should be retried.
    """
    from media.models import MediaAsset

    logger.info(
        "Generating thumbnail for image",
        extra={"media_file_id": str(media_file.pk)},
    )

    try:
        # Open and load the source image
        with media_file.file.open("rb") as f:
            img = Image.open(f)

            # Force load to detect corrupt images early
            # This reads the entire image into memory
            img.load()

            # Store original dimensions for logging
            original_width, original_height = img.size

            # Convert color mode for WebP compatibility
            img = _convert_to_rgb(img)

            # Generate thumbnail (modifies in place, maintains aspect ratio)
            img.thumbnail(THUMBNAIL_SIZE, Image.Resampling.LANCZOS)

            # Save to WebP format in memory
            buffer = BytesIO()
            img.save(buffer, format="WEBP", quality=WEBP_QUALITY)
            buffer.seek(0)

            # Get thumbnail dimensions and size
            thumb_width, thumb_height = img.size
            file_size = buffer.getbuffer().nbytes

            # Create or update the thumbnail asset
            # Using update_or_create for idempotency
            asset, created = MediaAsset.objects.update_or_create(
                media_file=media_file,
                asset_type=MediaAsset.AssetType.THUMBNAIL,
                defaults={
                    "width": thumb_width,
                    "height": thumb_height,
                    "file_size": file_size,
                },
            )

            # Generate filename based on parent UUID
            filename = f"thumb_{media_file.pk}.webp"

            # Save the file content
            # This uses the get_asset_upload_path function for the path
            asset.file.save(filename, ContentFile(buffer.read()), save=True)

            logger.info(
                "Generated thumbnail successfully",
                extra={
                    "media_file_id": str(media_file.pk),
                    "asset_id": str(asset.pk),
                    "original_size": f"{original_width}x{original_height}",
                    "thumb_size": f"{thumb_width}x{thumb_height}",
                    "file_size": file_size,
                    "is_new": created,
                },
            )

            return asset

    except Image.DecompressionBombError as e:
        logger.warning(
            "Image exceeds size limit",
            extra={
                "media_file_id": str(media_file.pk),
                "error": str(e),
            },
        )
        raise ImageProcessingError(f"Image exceeds maximum size limit: {e}") from e

    except Image.UnidentifiedImageError as e:
        logger.warning(
            "Cannot identify image format",
            extra={
                "media_file_id": str(media_file.pk),
                "error": str(e),
            },
        )
        raise ImageProcessingError(
            f"Cannot identify image format - file may be corrupted: {e}"
        ) from e

    except OSError as e:
        error_str = str(e).lower()
        if "truncated" in error_str or "cannot identify" in error_str:
            logger.warning(
                "Image file is truncated or corrupted",
                extra={
                    "media_file_id": str(media_file.pk),
                    "error": str(e),
                },
            )
            raise ImageProcessingError(
                f"Image file is truncated or corrupted: {e}"
            ) from e

        # Other OSError (file not found, permission denied, etc.)
        # These might be transient and should be retried
        logger.error(
            "I/O error during thumbnail generation",
            extra={
                "media_file_id": str(media_file.pk),
                "error": str(e),
            },
        )
        raise

    except Exception as e:
        logger.exception(
            "Unexpected error generating thumbnail",
            extra={
                "media_file_id": str(media_file.pk),
                "error": str(e),
            },
        )
        raise


def _convert_to_rgb(img: Image.Image) -> Image.Image:
    """
    Convert image to RGB mode for WebP compatibility.

    Handles various color modes:
    - RGBA: Composites onto white background
    - LA (grayscale with alpha): Converts to RGBA then RGB
    - P (palette): Converts to RGBA if has transparency, else RGB
    - L (grayscale): Converts directly to RGB
    - Other: Converts directly to RGB

    Args:
        img: PIL Image in any color mode.

    Returns:
        PIL Image in RGB mode.
    """
    if img.mode == "RGB":
        return img

    if img.mode in ("RGBA", "LA"):
        # Create white background
        background = Image.new("RGB", img.size, (255, 255, 255))

        # Handle LA mode (grayscale with alpha)
        if img.mode == "LA":
            img = img.convert("RGBA")

        # Paste with alpha channel as mask
        background.paste(img, mask=img.split()[-1])
        return background

    if img.mode == "P":
        # Palette mode - check for transparency
        if "transparency" in img.info:
            img = img.convert("RGBA")
            background = Image.new("RGB", img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[-1])
            return background
        return img.convert("RGB")

    # All other modes (L, CMYK, etc.)
    return img.convert("RGB")
