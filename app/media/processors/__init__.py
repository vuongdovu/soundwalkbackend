"""
Media processors package.

Provides processing functions for different media types:
- Image processing (thumbnails, preview, web optimization)
- Video processing (poster frames, metadata extraction)
- Document processing (PDF thumbnails, text extraction)

Each processor is responsible for:
1. Reading the source file
2. Generating one or more assets
3. Creating MediaAsset records
4. Handling errors appropriately

Exception Hierarchy:
    ProcessingError (base)
    ├── PermanentProcessingError (don't retry)
    │   ├── ImageProcessingError
    │   ├── VideoProcessingError
    │   └── DocumentProcessingError
    └── TransientProcessingError (retry)

Usage:
    from media.processors import (
        generate_image_thumbnail,
        generate_image_preview,
        extract_image_metadata,
        PermanentProcessingError,
    )
    from media.models import MediaFile

    media_file = MediaFile.objects.get(id=uuid)
    try:
        thumbnail = generate_image_thumbnail(media_file)
    except PermanentProcessingError:
        # Handle permanent failure
        pass
"""

from media.processors.base import (
    PROCESSING_TIMEOUT,
    MetadataResult,
    PermanentProcessingError,
    ProcessingError,
    ProcessingResult,
    TransientProcessingError,
)
from media.processors.image import (
    ImageProcessingError,
    extract_image_metadata,
    generate_image_preview,
    generate_image_thumbnail,
    generate_image_web_optimized,
)
from media.processors.video import (
    VideoProcessingError,
    extract_video_metadata,
    extract_video_poster,
)
from media.processors.document import (
    DocumentProcessingError,
    convert_to_pdf,
    extract_document_metadata,
    extract_document_text,
    generate_document_thumbnail,
)

__all__ = [
    # Base exceptions and types
    "ProcessingError",
    "PermanentProcessingError",
    "TransientProcessingError",
    "ProcessingResult",
    "MetadataResult",
    "PROCESSING_TIMEOUT",
    # Image processing
    "ImageProcessingError",
    "generate_image_thumbnail",
    "generate_image_preview",
    "generate_image_web_optimized",
    "extract_image_metadata",
    # Video processing
    "VideoProcessingError",
    "extract_video_metadata",
    "extract_video_poster",
    # Document processing
    "DocumentProcessingError",
    "convert_to_pdf",
    "extract_document_metadata",
    "extract_document_text",
    "generate_document_thumbnail",
]
