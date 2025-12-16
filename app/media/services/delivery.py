"""
FileDeliveryService for serving media files securely.

Provides:
- URL generation for protected file access
- Storage-agnostic delivery (local FileSystem or S3)
- X-Accel-Redirect for nginx in production
- Presigned URLs for S3
- Content-Disposition handling (attachment vs inline)
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import quote

from django.conf import settings
from django.core.files.storage import default_storage
from django.http import FileResponse, HttpResponse
from django.urls import reverse

from core.services import BaseService

if TYPE_CHECKING:
    from media.models import MediaFile


class FileDeliveryService(BaseService):
    """
    Service for delivering media files to users.

    Abstracts storage backend differences and provides secure file delivery:
    - Local storage: FileResponse (DEBUG) or X-Accel-Redirect (production)
    - S3 storage: Presigned URLs for direct browser access

    Usage:
        # Get URL for download
        url = FileDeliveryService.get_download_url(media_file)

        # Get URL for inline viewing
        url = FileDeliveryService.get_view_url(media_file)

        # Serve file directly (for protected endpoints)
        response = FileDeliveryService.serve_file_response(
            media_file,
            as_attachment=True,
        )
    """

    @classmethod
    def is_s3_storage(cls) -> bool:
        """
        Check if the default storage is S3.

        Detects S3Boto3Storage by checking for the 'bucket' attribute.

        Returns:
            True if using S3 storage, False for local FileSystemStorage
        """
        return hasattr(default_storage, "bucket")

    @classmethod
    def get_download_url(
        cls,
        media_file: "MediaFile",
        expires_in: int = 3600,
    ) -> str:
        """
        Get URL for downloading a file (attachment disposition).

        For local storage: Returns protected endpoint URL
        For S3 storage: Returns presigned URL with attachment disposition

        Args:
            media_file: The media file to generate URL for
            expires_in: Expiration time in seconds (S3 only, default 1 hour)

        Returns:
            URL string for downloading the file
        """
        if cls.is_s3_storage():
            return cls._get_s3_presigned_url(
                media_file,
                response_content_disposition=f'attachment; filename="{cls._encode_filename(media_file.original_filename)}"',
                expires_in=expires_in,
            )

        # Local storage - return protected endpoint URL
        return reverse("media:download", kwargs={"file_id": str(media_file.id)})

    @classmethod
    def get_view_url(
        cls,
        media_file: "MediaFile",
        expires_in: int = 3600,
    ) -> str:
        """
        Get URL for viewing a file inline (browser display).

        For local storage: Returns protected endpoint URL
        For S3 storage: Returns presigned URL with inline disposition

        Args:
            media_file: The media file to generate URL for
            expires_in: Expiration time in seconds (S3 only, default 1 hour)

        Returns:
            URL string for viewing the file inline
        """
        if cls.is_s3_storage():
            return cls._get_s3_presigned_url(
                media_file,
                response_content_disposition=f'inline; filename="{cls._encode_filename(media_file.original_filename)}"',
                expires_in=expires_in,
            )

        # Local storage - return protected endpoint URL
        return reverse("media:view", kwargs={"file_id": str(media_file.id)})

    @classmethod
    def serve_file_response(
        cls,
        media_file: "MediaFile",
        as_attachment: bool = True,
    ) -> HttpResponse:
        """
        Create HTTP response for serving a file.

        In DEBUG mode: Returns Django FileResponse (Django serves the file)
        In production: Returns X-Accel-Redirect for nginx to serve the file

        This method should be called from protected views after access
        control has been verified.

        Args:
            media_file: The media file to serve
            as_attachment: If True, force download; if False, display inline

        Returns:
            HttpResponse configured for file delivery

        Raises:
            FileNotFoundError: If the file doesn't exist on disk
        """
        # Verify file exists
        if not media_file.file or not default_storage.exists(media_file.file.name):
            raise FileNotFoundError(
                f"File not found: {media_file.file.name if media_file.file else 'No file'}"
            )

        filename = cls._encode_filename(media_file.original_filename)
        content_type = media_file.mime_type

        if as_attachment:
            disposition = f'attachment; filename="{filename}"'
        else:
            disposition = f'inline; filename="{filename}"'

        if settings.DEBUG:
            # Development: Django serves the file directly
            response = FileResponse(
                media_file.file.open("rb"),
                content_type=content_type,
            )
            response["Content-Disposition"] = disposition
            return response

        # Production: nginx serves via X-Accel-Redirect
        response = HttpResponse(content_type=content_type)
        response["Content-Disposition"] = disposition

        # X-Accel-Redirect path - nginx internal location
        # The path must match nginx configuration
        internal_path = f"/protected-media/{media_file.file.name}"
        response["X-Accel-Redirect"] = internal_path

        return response

    # =========================================================================
    # Private Helper Methods
    # =========================================================================

    @classmethod
    def _get_s3_presigned_url(
        cls,
        media_file: "MediaFile",
        response_content_disposition: str,
        expires_in: int,
    ) -> str:
        """
        Generate a presigned S3 URL with custom response headers.

        Args:
            media_file: The media file
            response_content_disposition: Content-Disposition header value
            expires_in: URL expiration in seconds

        Returns:
            Presigned S3 URL
        """
        # S3Boto3Storage provides url() method that generates presigned URLs
        # For custom response headers, we need to use boto3 directly
        try:
            # Try to use boto3 for full control
            client = default_storage.connection.meta.client
            bucket_name = default_storage.bucket_name

            url = client.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": bucket_name,
                    "Key": media_file.file.name,
                    "ResponseContentDisposition": response_content_disposition,
                    "ResponseContentType": media_file.mime_type,
                },
                ExpiresIn=expires_in,
            )
            return url
        except AttributeError:
            # Fallback to storage's url() method
            return default_storage.url(media_file.file.name)

    @classmethod
    def _encode_filename(cls, filename: str) -> str:
        """
        Encode filename for Content-Disposition header.

        Handles special characters like spaces, unicode, etc.

        Args:
            filename: Original filename

        Returns:
            URL-encoded filename safe for HTTP headers
        """
        # Quote special characters but keep the filename readable
        # RFC 5987 encoding for non-ASCII characters
        return quote(filename, safe="")
