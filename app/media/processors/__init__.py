"""
Media processors package.

Provides processing functions for different media types:
- Image processing (thumbnails, web optimization)
- Video processing (posters, transcoding) - future
- Document processing (PDF previews, text extraction) - future

Each processor is responsible for:
1. Reading the source file
2. Generating one or more assets
3. Creating MediaAsset records
4. Handling errors appropriately

Usage:
    from media.processors.image import generate_image_thumbnail
    from media.models import MediaFile

    media_file = MediaFile.objects.get(id=uuid)
    thumbnail = generate_image_thumbnail(media_file)
"""

from media.processors.image import ImageProcessingError, generate_image_thumbnail

__all__ = [
    "ImageProcessingError",
    "generate_image_thumbnail",
]
