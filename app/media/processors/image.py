"""
Image processing module for generating thumbnails, previews, and optimized versions.

Uses Pillow for image manipulation with proper error handling for:
- Corrupted image files
- Unsupported formats
- Images exceeding size limits
- Memory constraints

All generated images are saved as WebP for optimal web delivery.

Functions:
    generate_image_thumbnail: Create 200x200 thumbnail
    generate_image_preview: Create 800x800 preview
    generate_image_web_optimized: Create compressed web version
    extract_image_metadata: Extract dimensions, color space, EXIF data
"""

from __future__ import annotations

import logging
from io import BytesIO
from typing import TYPE_CHECKING, Any

from django.core.files.base import ContentFile
from PIL import Image
from PIL.ExifTags import TAGS

from media.processors.base import PermanentProcessingError

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

# Web optimized maximum dimensions
WEB_OPTIMIZED_MAX_SIZE = (2048, 2048)

# WebP quality settings (0-100)
WEBP_QUALITY = 80
WEBP_QUALITY_PREVIEW = 80
WEBP_QUALITY_WEB_OPTIMIZED = 75  # Slightly more compressed for web

# Pillow's default max pixels (~178M pixels, about 13380x13380)
# We use this default - files exceeding will raise DecompressionBombError
# Image.MAX_IMAGE_PIXELS = 178956970  # Default, no need to set

# EXIF tags we care about extracting
EXIF_TAGS_TO_EXTRACT = {
    "Make",
    "Model",
    "DateTime",
    "DateTimeOriginal",
    "DateTimeDigitized",
    "GPSInfo",
    "Orientation",
    "Software",
    "Artist",
    "Copyright",
    "ExifImageWidth",
    "ExifImageHeight",
}


# =============================================================================
# Exceptions
# =============================================================================


class ImageProcessingError(PermanentProcessingError):
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


# =============================================================================
# Metadata Extraction
# =============================================================================


def extract_image_metadata(media_file: "MediaFile") -> dict[str, Any]:
    """
    Extract metadata from an image file.

    Extracts basic image properties and optional EXIF data:
    - Dimensions (width, height)
    - Color space/mode
    - Format
    - Has alpha channel
    - EXIF data (camera make/model, datetime, GPS, etc.)

    Args:
        media_file: MediaFile instance with media_type='image'.

    Returns:
        Dictionary containing extracted metadata.

    Raises:
        ImageProcessingError: If image cannot be read (permanent failure).
        OSError: For transient I/O errors that should be retried.

    Example:
        >>> metadata = extract_image_metadata(media_file)
        >>> print(metadata)
        {
            'width': 1920,
            'height': 1080,
            'color_space': 'RGB',
            'format': 'JPEG',
            'has_alpha': False,
            'exif': {'make': 'Canon', 'model': 'EOS 5D', ...}
        }
    """
    logger.info(
        "Extracting metadata from image",
        extra={"media_file_id": str(media_file.pk)},
    )

    try:
        with media_file.file.open("rb") as f:
            img = Image.open(f)

            # Force load to ensure file is valid
            img.load()

            # Basic metadata
            metadata: dict[str, Any] = {
                "width": img.size[0],
                "height": img.size[1],
                "color_space": img.mode,
                "format": img.format or "unknown",
                "has_alpha": img.mode in ("RGBA", "LA", "PA")
                or "transparency" in img.info,
            }

            # Extract EXIF data if available
            exif_data = _extract_exif(img)
            if exif_data:
                metadata["exif"] = exif_data

            logger.info(
                "Extracted image metadata successfully",
                extra={
                    "media_file_id": str(media_file.pk),
                    "width": metadata["width"],
                    "height": metadata["height"],
                    "format": metadata["format"],
                    "has_exif": bool(exif_data),
                },
            )

            return metadata

    except Image.DecompressionBombError as e:
        logger.warning(
            "Image exceeds size limit during metadata extraction",
            extra={"media_file_id": str(media_file.pk), "error": str(e)},
        )
        raise ImageProcessingError(f"Image exceeds maximum size limit: {e}") from e

    except Image.UnidentifiedImageError as e:
        logger.warning(
            "Cannot identify image format during metadata extraction",
            extra={"media_file_id": str(media_file.pk), "error": str(e)},
        )
        raise ImageProcessingError(
            f"Cannot identify image format - file may be corrupted: {e}"
        ) from e

    except OSError as e:
        error_str = str(e).lower()
        if "truncated" in error_str or "cannot identify" in error_str:
            logger.warning(
                "Image file is truncated or corrupted",
                extra={"media_file_id": str(media_file.pk), "error": str(e)},
            )
            raise ImageProcessingError(
                f"Image file is truncated or corrupted: {e}"
            ) from e
        # Transient error - let it propagate for retry
        logger.error(
            "I/O error during metadata extraction",
            extra={"media_file_id": str(media_file.pk), "error": str(e)},
        )
        raise


def _extract_exif(img: Image.Image) -> dict[str, Any] | None:
    """
    Extract EXIF data from a PIL Image.

    Args:
        img: PIL Image object.

    Returns:
        Dictionary of EXIF data or None if no EXIF data available.
    """
    try:
        exif = img.getexif()
        if not exif:
            return None

        exif_data: dict[str, Any] = {}

        for tag_id, value in exif.items():
            tag_name = TAGS.get(tag_id, str(tag_id))

            # Only extract tags we care about
            if tag_name not in EXIF_TAGS_TO_EXTRACT:
                continue

            # Convert value to JSON-serializable format
            if isinstance(value, bytes):
                try:
                    value = value.decode("utf-8", errors="ignore")
                except Exception:
                    continue
            elif hasattr(value, "numerator"):
                # Handle rational numbers (fractions)
                value = float(value)
            elif isinstance(value, tuple):
                # Handle tuple values (e.g., GPS coordinates)
                value = list(value)

            # Use lowercase key names for consistency
            key = _camel_to_snake(tag_name)
            exif_data[key] = value

        # Handle GPS info specially if present
        if "gps_info" in exif_data:
            gps = _parse_gps_info(exif_data.pop("gps_info"))
            if gps:
                exif_data["gps"] = gps

        return exif_data if exif_data else None

    except Exception as e:
        # EXIF extraction is optional - don't fail if it doesn't work
        logger.debug(f"Could not extract EXIF data: {e}")
        return None


def _camel_to_snake(name: str) -> str:
    """Convert CamelCase to snake_case."""
    import re

    s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def _parse_gps_info(gps_info: Any) -> dict[str, float] | None:
    """
    Parse GPS EXIF data into lat/lng coordinates.

    Args:
        gps_info: Raw GPS info from EXIF.

    Returns:
        Dictionary with 'lat' and 'lng' keys, or None if parsing fails.
    """
    try:
        if not isinstance(gps_info, dict):
            return None

        def convert_to_degrees(value: Any) -> float:
            """Convert GPS coordinate to degrees."""
            if isinstance(value, (list, tuple)) and len(value) >= 3:
                d, m, s = value[0], value[1], value[2]
                if hasattr(d, "numerator"):
                    d = float(d)
                if hasattr(m, "numerator"):
                    m = float(m)
                if hasattr(s, "numerator"):
                    s = float(s)
                return d + (m / 60.0) + (s / 3600.0)
            return float(value)

        lat = gps_info.get(2)  # GPSLatitude
        lat_ref = gps_info.get(1)  # GPSLatitudeRef
        lng = gps_info.get(4)  # GPSLongitude
        lng_ref = gps_info.get(3)  # GPSLongitudeRef

        if lat and lng:
            lat_deg = convert_to_degrees(lat)
            lng_deg = convert_to_degrees(lng)

            if lat_ref == "S":
                lat_deg = -lat_deg
            if lng_ref == "W":
                lng_deg = -lng_deg

            return {"lat": round(lat_deg, 6), "lng": round(lng_deg, 6)}

    except Exception as e:
        logger.debug(f"Could not parse GPS info: {e}")

    return None


# =============================================================================
# Preview Generation
# =============================================================================


def generate_image_preview(media_file: "MediaFile") -> "MediaAsset":
    """
    Generate a medium-sized preview for an image file.

    Creates an 800x800 maximum dimension preview while maintaining
    aspect ratio. Useful for detail views and image galleries.

    Args:
        media_file: MediaFile instance with media_type='image'.

    Returns:
        MediaAsset instance containing the generated preview.

    Raises:
        ImageProcessingError: If image cannot be processed (permanent failure).
        OSError: For transient I/O errors that should be retried.
    """
    from media.models import MediaAsset

    logger.info(
        "Generating preview for image",
        extra={"media_file_id": str(media_file.pk)},
    )

    try:
        with media_file.file.open("rb") as f:
            img = Image.open(f)
            img.load()

            original_width, original_height = img.size
            img = _convert_to_rgb(img)

            # Generate preview (maintains aspect ratio, max 800x800)
            img.thumbnail(PREVIEW_SIZE, Image.Resampling.LANCZOS)

            # Save to WebP format
            buffer = BytesIO()
            img.save(buffer, format="WEBP", quality=WEBP_QUALITY_PREVIEW)
            buffer.seek(0)

            preview_width, preview_height = img.size
            file_size = buffer.getbuffer().nbytes

            # Create or update the preview asset
            asset, created = MediaAsset.objects.update_or_create(
                media_file=media_file,
                asset_type=MediaAsset.AssetType.PREVIEW,
                defaults={
                    "width": preview_width,
                    "height": preview_height,
                    "file_size": file_size,
                },
            )

            filename = f"preview_{media_file.pk}.webp"
            asset.file.save(filename, ContentFile(buffer.read()), save=True)

            logger.info(
                "Generated preview successfully",
                extra={
                    "media_file_id": str(media_file.pk),
                    "asset_id": str(asset.pk),
                    "original_size": f"{original_width}x{original_height}",
                    "preview_size": f"{preview_width}x{preview_height}",
                    "file_size": file_size,
                    "is_new": created,
                },
            )

            return asset

    except Image.DecompressionBombError as e:
        logger.warning(
            "Image exceeds size limit",
            extra={"media_file_id": str(media_file.pk), "error": str(e)},
        )
        raise ImageProcessingError(f"Image exceeds maximum size limit: {e}") from e

    except Image.UnidentifiedImageError as e:
        logger.warning(
            "Cannot identify image format",
            extra={"media_file_id": str(media_file.pk), "error": str(e)},
        )
        raise ImageProcessingError(
            f"Cannot identify image format - file may be corrupted: {e}"
        ) from e

    except OSError as e:
        error_str = str(e).lower()
        if "truncated" in error_str or "cannot identify" in error_str:
            logger.warning(
                "Image file is truncated or corrupted",
                extra={"media_file_id": str(media_file.pk), "error": str(e)},
            )
            raise ImageProcessingError(
                f"Image file is truncated or corrupted: {e}"
            ) from e
        logger.error(
            "I/O error during preview generation",
            extra={"media_file_id": str(media_file.pk), "error": str(e)},
        )
        raise

    except Exception as e:
        logger.exception(
            "Unexpected error generating preview",
            extra={"media_file_id": str(media_file.pk), "error": str(e)},
        )
        raise


# =============================================================================
# Web Optimized Generation
# =============================================================================


def generate_image_web_optimized(media_file: "MediaFile") -> "MediaAsset":
    """
    Generate a web-optimized version of an image file.

    Creates a compressed WebP version with:
    - Maximum dimensions of 2048x2048
    - Quality setting of 75 (slightly more compressed)
    - EXIF data stripped for privacy

    This is the primary image served for web viewing, balancing
    quality and file size for optimal loading performance.

    Args:
        media_file: MediaFile instance with media_type='image'.

    Returns:
        MediaAsset instance containing the web-optimized version.

    Raises:
        ImageProcessingError: If image cannot be processed (permanent failure).
        OSError: For transient I/O errors that should be retried.
    """
    from media.models import MediaAsset

    logger.info(
        "Generating web-optimized version for image",
        extra={"media_file_id": str(media_file.pk)},
    )

    try:
        with media_file.file.open("rb") as f:
            img = Image.open(f)
            img.load()

            original_width, original_height = img.size
            img = _convert_to_rgb(img)

            # Resize if larger than max dimensions (maintains aspect ratio)
            img.thumbnail(WEB_OPTIMIZED_MAX_SIZE, Image.Resampling.LANCZOS)

            # Save to WebP format with slightly more compression
            # EXIF is already stripped when we loaded through _convert_to_rgb
            buffer = BytesIO()
            img.save(
                buffer,
                format="WEBP",
                quality=WEBP_QUALITY_WEB_OPTIMIZED,
            )
            buffer.seek(0)

            optimized_width, optimized_height = img.size
            file_size = buffer.getbuffer().nbytes

            # Create or update the web optimized asset
            asset, created = MediaAsset.objects.update_or_create(
                media_file=media_file,
                asset_type=MediaAsset.AssetType.WEB_OPTIMIZED,
                defaults={
                    "width": optimized_width,
                    "height": optimized_height,
                    "file_size": file_size,
                },
            )

            filename = f"web_{media_file.pk}.webp"
            asset.file.save(filename, ContentFile(buffer.read()), save=True)

            logger.info(
                "Generated web-optimized version successfully",
                extra={
                    "media_file_id": str(media_file.pk),
                    "asset_id": str(asset.pk),
                    "original_size": f"{original_width}x{original_height}",
                    "optimized_size": f"{optimized_width}x{optimized_height}",
                    "file_size": file_size,
                    "is_new": created,
                },
            )

            return asset

    except Image.DecompressionBombError as e:
        logger.warning(
            "Image exceeds size limit",
            extra={"media_file_id": str(media_file.pk), "error": str(e)},
        )
        raise ImageProcessingError(f"Image exceeds maximum size limit: {e}") from e

    except Image.UnidentifiedImageError as e:
        logger.warning(
            "Cannot identify image format",
            extra={"media_file_id": str(media_file.pk), "error": str(e)},
        )
        raise ImageProcessingError(
            f"Cannot identify image format - file may be corrupted: {e}"
        ) from e

    except OSError as e:
        error_str = str(e).lower()
        if "truncated" in error_str or "cannot identify" in error_str:
            logger.warning(
                "Image file is truncated or corrupted",
                extra={"media_file_id": str(media_file.pk), "error": str(e)},
            )
            raise ImageProcessingError(
                f"Image file is truncated or corrupted: {e}"
            ) from e
        logger.error(
            "I/O error during web-optimized generation",
            extra={"media_file_id": str(media_file.pk), "error": str(e)},
        )
        raise

    except Exception as e:
        logger.exception(
            "Unexpected error generating web-optimized version",
            extra={"media_file_id": str(media_file.pk), "error": str(e)},
        )
        raise
