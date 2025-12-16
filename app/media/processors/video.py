"""
Video processing module for extracting poster frames and metadata.

Uses FFmpeg/FFprobe via subprocess for video processing with proper
error handling for:
- Corrupted video files
- Unsupported codecs
- Videos without video tracks
- Timeout constraints

Generated poster frames are saved as WebP for optimal web delivery.

Functions:
    extract_video_metadata: Extract duration, resolution, codecs
    extract_video_poster: Extract a poster frame at intelligent position
"""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Any

from django.core.files.base import ContentFile
from PIL import Image

from media.processors.base import (
    POSTER_FRAME_POSITIONS,
    POSTER_MAX_WIDTH,
    PermanentProcessingError,
    TransientProcessingError,
    VIDEO_METADATA_TIMEOUT,
    VIDEO_POSTER_TIMEOUT,
    WEBP_QUALITY,
)

if TYPE_CHECKING:
    from media.models import MediaAsset, MediaFile

logger = logging.getLogger(__name__)


# =============================================================================
# Exceptions
# =============================================================================


class VideoProcessingError(PermanentProcessingError):
    """
    Raised when video processing fails permanently.

    This exception indicates a non-recoverable error such as:
    - Corrupted video file
    - Unsupported video codec
    - No video track present
    - Invalid container format

    Tasks should NOT retry when this exception is raised.
    """

    pass


# =============================================================================
# Metadata Extraction
# =============================================================================


def extract_video_metadata(media_file: "MediaFile") -> dict[str, Any]:
    """
    Extract metadata from a video file using FFprobe.

    Extracts:
    - Duration (seconds)
    - Dimensions (width, height)
    - Frame rate
    - Video codec
    - Audio codec (if present)
    - Bitrate
    - Has audio track

    Args:
        media_file: MediaFile instance with media_type='video'.

    Returns:
        Dictionary containing extracted metadata.

    Raises:
        VideoProcessingError: If video cannot be read (permanent failure).
        TransientProcessingError: For timeout errors that should be retried.

    Example:
        >>> metadata = extract_video_metadata(media_file)
        >>> print(metadata)
        {
            'width': 1920,
            'height': 1080,
            'duration': 125.5,
            'frame_rate': 29.97,
            'codec': 'h264',
            'audio_codec': 'aac',
            'bitrate': 5000000,
            'has_audio': True
        }
    """
    logger.info(
        "Extracting metadata from video",
        extra={"media_file_id": str(media_file.pk)},
    )

    file_path = media_file.file.path

    try:
        # Run ffprobe to get video info as JSON
        cmd = [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            file_path,
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=VIDEO_METADATA_TIMEOUT,
        )

        if result.returncode != 0:
            stderr = result.stderr.strip() if result.stderr else "Unknown error"
            logger.warning(
                "FFprobe failed to read video",
                extra={
                    "media_file_id": str(media_file.pk),
                    "returncode": result.returncode,
                    "stderr": stderr,
                },
            )
            raise VideoProcessingError(f"FFprobe failed to read video: {stderr}")

        # Parse JSON output
        try:
            probe_data = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            logger.warning(
                "Failed to parse FFprobe output",
                extra={"media_file_id": str(media_file.pk), "error": str(e)},
            )
            raise VideoProcessingError(f"Failed to parse video metadata: {e}") from e

        # Extract stream information
        video_stream = None
        audio_stream = None

        for stream in probe_data.get("streams", []):
            codec_type = stream.get("codec_type")
            if codec_type == "video" and video_stream is None:
                video_stream = stream
            elif codec_type == "audio" and audio_stream is None:
                audio_stream = stream

        if video_stream is None:
            logger.warning(
                "No video track found in file",
                extra={"media_file_id": str(media_file.pk)},
            )
            raise VideoProcessingError("No video track found in file")

        # Extract format information
        format_info = probe_data.get("format", {})

        # Build metadata dictionary
        metadata: dict[str, Any] = {
            "width": video_stream.get("width"),
            "height": video_stream.get("height"),
            "codec": video_stream.get("codec_name"),
            "has_audio": audio_stream is not None,
        }

        # Duration from format (more reliable) or stream
        duration_str = format_info.get("duration") or video_stream.get("duration")
        if duration_str:
            try:
                metadata["duration"] = float(duration_str)
            except (ValueError, TypeError):
                pass

        # Frame rate - parse fractional format like "30000/1001"
        frame_rate_str = video_stream.get("r_frame_rate") or video_stream.get(
            "avg_frame_rate"
        )
        if frame_rate_str:
            try:
                if "/" in frame_rate_str:
                    num, den = frame_rate_str.split("/")
                    if int(den) != 0:
                        metadata["frame_rate"] = round(int(num) / int(den), 2)
                else:
                    metadata["frame_rate"] = float(frame_rate_str)
            except (ValueError, TypeError, ZeroDivisionError):
                pass

        # Bitrate from format
        bitrate_str = format_info.get("bit_rate")
        if bitrate_str:
            try:
                metadata["bitrate"] = int(bitrate_str)
            except (ValueError, TypeError):
                pass

        # Audio codec if present
        if audio_stream:
            metadata["audio_codec"] = audio_stream.get("codec_name")

        logger.info(
            "Extracted video metadata successfully",
            extra={
                "media_file_id": str(media_file.pk),
                "width": metadata.get("width"),
                "height": metadata.get("height"),
                "duration": metadata.get("duration"),
                "codec": metadata.get("codec"),
            },
        )

        return metadata

    except subprocess.TimeoutExpired:
        logger.warning(
            "FFprobe timed out",
            extra={
                "media_file_id": str(media_file.pk),
                "timeout": VIDEO_METADATA_TIMEOUT,
            },
        )
        raise TransientProcessingError(
            f"FFprobe timed out after {VIDEO_METADATA_TIMEOUT} seconds"
        )

    except FileNotFoundError:
        logger.error(
            "FFprobe not found - ensure FFmpeg is installed",
            extra={"media_file_id": str(media_file.pk)},
        )
        raise TransientProcessingError(
            "FFprobe not found - FFmpeg may not be installed"
        )

    except OSError as e:
        logger.error(
            "I/O error during metadata extraction",
            extra={"media_file_id": str(media_file.pk), "error": str(e)},
        )
        raise


# =============================================================================
# Poster Frame Extraction
# =============================================================================


def extract_video_poster(media_file: "MediaFile") -> "MediaAsset":
    """
    Extract a poster frame from a video file.

    Uses intelligent frame selection:
    - Tries multiple positions (10%, 25%, 50% of duration)
    - Selects first non-black frame
    - Scales to max width of 1280px maintaining aspect ratio
    - Outputs as WebP format

    For very short videos (< 2 seconds), extracts from 10% of duration.

    Args:
        media_file: MediaFile instance with media_type='video'.

    Returns:
        MediaAsset instance containing the extracted poster frame.

    Raises:
        VideoProcessingError: If video cannot be processed (permanent failure).
        TransientProcessingError: For timeout errors that should be retried.
    """
    from media.models import MediaAsset

    logger.info(
        "Extracting poster frame from video",
        extra={"media_file_id": str(media_file.pk)},
    )

    file_path = media_file.file.path

    # First, get duration from metadata if not already extracted
    duration = media_file.metadata.get("duration") if media_file.metadata else None

    if duration is None:
        try:
            metadata = extract_video_metadata(media_file)
            duration = metadata.get("duration", 0)
        except VideoProcessingError:
            # If we can't get duration, try from the start
            duration = 0

    # Calculate frame positions to try
    positions = []
    if duration and duration > 0:
        for pos_ratio in POSTER_FRAME_POSITIONS:
            positions.append(duration * pos_ratio)
    else:
        # Fallback for unknown duration - try 0, 1, 2 seconds
        positions = [0, 1, 2]

    # For very short videos, just use the first position
    if duration and duration < 2:
        positions = [duration * 0.1]

    # Try each position until we get a valid frame
    extracted_frame = None

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            for seek_pos in positions:
                try:
                    frame_path = temp_path / f"frame_{seek_pos:.2f}.png"

                    # Extract single frame using FFmpeg
                    cmd = [
                        "ffmpeg",
                        "-y",  # Overwrite output
                        "-ss",
                        str(seek_pos),  # Seek position
                        "-i",
                        file_path,
                        "-vframes",
                        "1",  # Extract single frame
                        "-f",
                        "image2",
                        str(frame_path),
                    ]

                    result = subprocess.run(
                        cmd,
                        capture_output=True,
                        timeout=VIDEO_POSTER_TIMEOUT,
                    )

                    if result.returncode != 0:
                        logger.debug(
                            f"FFmpeg failed at position {seek_pos}",
                            extra={"media_file_id": str(media_file.pk)},
                        )
                        continue

                    # Check if frame was created and is not empty
                    if frame_path.exists() and frame_path.stat().st_size > 0:
                        # Check if frame is mostly black
                        if not _is_black_frame(frame_path):
                            extracted_frame = frame_path
                            break
                        else:
                            logger.debug(
                                f"Frame at {seek_pos} is mostly black, trying next position",
                                extra={"media_file_id": str(media_file.pk)},
                            )

                except subprocess.TimeoutExpired:
                    logger.warning(
                        f"FFmpeg timed out at position {seek_pos}",
                        extra={
                            "media_file_id": str(media_file.pk),
                            "timeout": VIDEO_POSTER_TIMEOUT,
                        },
                    )
                    # Try next position
                    continue

            # If no good frame found, use first position even if black
            if extracted_frame is None:
                # Last resort - extract from position 0
                frame_path = temp_path / "frame_fallback.png"
                try:
                    cmd = [
                        "ffmpeg",
                        "-y",
                        "-i",
                        file_path,
                        "-vframes",
                        "1",
                        "-f",
                        "image2",
                        str(frame_path),
                    ]

                    result = subprocess.run(
                        cmd,
                        capture_output=True,
                        timeout=VIDEO_POSTER_TIMEOUT,
                    )

                    if result.returncode == 0 and frame_path.exists():
                        extracted_frame = frame_path
                    else:
                        stderr = (
                            result.stderr.decode() if result.stderr else "Unknown error"
                        )
                        logger.warning(
                            "Failed to extract any frame from video",
                            extra={
                                "media_file_id": str(media_file.pk),
                                "stderr": stderr[:500],  # Truncate long errors
                            },
                        )
                        raise VideoProcessingError(
                            "Failed to extract poster frame from video"
                        )

                except subprocess.TimeoutExpired:
                    raise TransientProcessingError(
                        f"FFmpeg timed out after {VIDEO_POSTER_TIMEOUT} seconds"
                    )

            # Process the extracted frame
            try:
                with Image.open(extracted_frame) as img:
                    # Scale to max width maintaining aspect ratio
                    width, height = img.size

                    if width > POSTER_MAX_WIDTH:
                        ratio = POSTER_MAX_WIDTH / width
                        new_height = int(height * ratio)
                        img = img.resize(
                            (POSTER_MAX_WIDTH, new_height), Image.Resampling.LANCZOS
                        )
                        width, height = img.size

                    # Convert to RGB if necessary (WebP doesn't support all modes)
                    if img.mode not in ("RGB", "RGBA"):
                        img = img.convert("RGB")

                    # Save as WebP
                    buffer = BytesIO()
                    img.save(buffer, format="WEBP", quality=WEBP_QUALITY)
                    buffer.seek(0)

                    file_size = buffer.getbuffer().nbytes

                    # Create or update the poster asset
                    asset, created = MediaAsset.objects.update_or_create(
                        media_file=media_file,
                        asset_type=MediaAsset.AssetType.POSTER,
                        defaults={
                            "width": width,
                            "height": height,
                            "file_size": file_size,
                        },
                    )

                    filename = f"poster_{media_file.pk}.webp"
                    asset.file.save(filename, ContentFile(buffer.read()), save=True)

                    logger.info(
                        "Extracted poster frame successfully",
                        extra={
                            "media_file_id": str(media_file.pk),
                            "asset_id": str(asset.pk),
                            "size": f"{width}x{height}",
                            "file_size": file_size,
                            "is_new": created,
                        },
                    )

                    return asset

            except Image.UnidentifiedImageError as e:
                logger.warning(
                    "Could not process extracted frame",
                    extra={"media_file_id": str(media_file.pk), "error": str(e)},
                )
                raise VideoProcessingError(
                    f"Extracted frame is invalid or corrupted: {e}"
                ) from e

    except FileNotFoundError:
        logger.error(
            "FFmpeg not found - ensure FFmpeg is installed",
            extra={"media_file_id": str(media_file.pk)},
        )
        raise TransientProcessingError("FFmpeg not found - FFmpeg may not be installed")

    except OSError as e:
        logger.error(
            "I/O error during poster extraction",
            extra={"media_file_id": str(media_file.pk), "error": str(e)},
        )
        raise


def _is_black_frame(frame_path: Path, threshold: float = 0.1) -> bool:
    """
    Check if a frame is mostly black.

    Args:
        frame_path: Path to the frame image file.
        threshold: Maximum average pixel value (0-1) to consider as black.

    Returns:
        True if frame is mostly black, False otherwise.
    """
    try:
        with Image.open(frame_path) as img:
            # Convert to grayscale
            gray = img.convert("L")

            # Get image statistics
            pixels = list(gray.getdata())
            if not pixels:
                return True

            # Calculate average brightness (0-255)
            avg_brightness = sum(pixels) / len(pixels)

            # Normalize to 0-1 and compare with threshold
            return (avg_brightness / 255.0) < threshold

    except Exception as e:
        logger.debug(f"Could not analyze frame brightness: {e}")
        # If we can't analyze, assume it's not black
        return False
