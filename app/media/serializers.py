"""
Serializers for media file uploads, sharing, and chunked uploads.

Provides:
- MediaFileUploadSerializer: Handle file upload with validation
- MediaFileSerializer: Read-only serializer for API responses
- MediaFileShareSerializer: Read-only serializer for share responses
- MediaFileShareCreateSerializer: Create/update shares
- ChunkedUploadInitSerializer: Initialize chunked upload session
- ChunkedUploadSessionSerializer: Session status and progress
- ChunkTargetSerializer: Chunk upload target information
- PartCompletionResultSerializer: Part completion result
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from drf_spectacular.utils import OpenApiExample, extend_schema_serializer
from rest_framework import serializers

from authentication.models import User
from media.models import MediaFile, MediaFileShare, MediaFileTag, Tag, UploadSession
from media.validators import MediaValidator

if TYPE_CHECKING:
    from django.core.files.uploadedfile import UploadedFile


class MediaFileUploadSerializer(serializers.Serializer):
    """
    Serializer for handling file uploads.

    Validates:
    - File content against allowed MIME types
    - File size against per-media-type limits
    - User storage quota

    Usage:
        serializer = MediaFileUploadSerializer(
            data={"file": uploaded_file},
            context={"request": request}
        )
        if serializer.is_valid():
            media_file = serializer.save()
    """

    file = serializers.FileField(
        required=True,
        help_text="The file to upload",
    )

    visibility = serializers.ChoiceField(
        choices=MediaFile.Visibility.choices,
        default=MediaFile.Visibility.PRIVATE,
        required=False,
        help_text="Access level for this file",
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize serializer with validator instance."""
        super().__init__(*args, **kwargs)
        self._validator = MediaValidator()
        self._validation_result = None

    def validate_file(self, file: "UploadedFile") -> "UploadedFile":
        """
        Validate uploaded file content and size.

        Performs:
        1. Content-based MIME type detection
        2. MIME type allowlist check
        3. Per-media-type size limit check
        4. User storage quota check

        Args:
            file: The uploaded file to validate.

        Returns:
            The validated file.

        Raises:
            ValidationError: If file fails any validation check.
        """
        # Run content-based validation
        result = self._validator.validate(file)

        if not result.is_valid:
            raise serializers.ValidationError(result.error)

        # Store result for later use
        self._validation_result = result

        # Check user storage quota
        request = self.context.get("request")
        if request and hasattr(request, "user") and request.user.is_authenticated:
            user = request.user
            if hasattr(user, "profile"):
                if not user.profile.can_upload(file.size):
                    raise serializers.ValidationError(
                        "Storage quota exceeded. Please free up space or upgrade your plan."
                    )

        return file

    def create(self, validated_data: dict[str, Any]) -> MediaFile:
        """
        Create MediaFile from validated data.

        Uses the factory method to create and save the media file,
        then updates user's storage quota and triggers async processing.

        Args:
            validated_data: Data that passed validation.

        Returns:
            Created MediaFile instance.
        """
        request = self.context.get("request")
        user = request.user

        file = validated_data["file"]
        visibility = validated_data.get("visibility", MediaFile.Visibility.PRIVATE)

        # Use validation result from validate_file
        media_type = self._validation_result.media_type
        mime_type = self._validation_result.mime_type

        # Create the media file
        media_file = MediaFile.create_from_upload(
            file=file,
            uploader=user,
            media_type=media_type,
            mime_type=mime_type,
            visibility=visibility,
        )

        # Update user's storage quota
        if hasattr(user, "profile"):
            user.profile.add_storage_usage(media_file.file_size)

        # Compute initial search vector with filename only
        from media.services.search import SearchVectorService

        SearchVectorService.update_vector_filename_only(media_file)

        # Trigger async scan -> process -> search vector chain
        # Import here to avoid circular imports
        from celery import chain

        from media.tasks import (
            process_media_file,
            scan_file_for_malware,
            update_search_vector_safe,
        )

        # Chain: scan first, then process, then update search vector with content
        task_chain = chain(
            scan_file_for_malware.s(str(media_file.id)),
            process_media_file.s(),
            update_search_vector_safe.s(),
        )
        task_chain.delay()

        return media_file


@extend_schema_serializer(
    examples=[
        OpenApiExample(
            "Image file",
            value={
                "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "original_filename": "vacation_photo.jpg",
                "media_type": "image",
                "mime_type": "image/jpeg",
                "file_size": 2457600,
                "visibility": "private",
                "file_url": "https://api.example.com/api/v1/media/files/a1b2c3d4-e5f6-7890-abcd-ef1234567890/download/",
                "processing_status": "ready",
                "scan_status": "clean",
                "version": 1,
                "is_current": True,
                "created_at": "2024-01-15T10:30:00Z",
                "updated_at": "2024-01-15T10:31:00Z",
            },
            response_only=True,
        ),
        OpenApiExample(
            "Document file (processing)",
            value={
                "id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
                "original_filename": "quarterly_report_2024.pdf",
                "media_type": "document",
                "mime_type": "application/pdf",
                "file_size": 5242880,
                "visibility": "shared",
                "file_url": "https://api.example.com/api/v1/media/files/b2c3d4e5-f6a7-8901-bcde-f12345678901/download/",
                "processing_status": "processing",
                "scan_status": "clean",
                "version": 1,
                "is_current": True,
                "created_at": "2024-01-15T14:00:00Z",
                "updated_at": "2024-01-15T14:00:30Z",
            },
            response_only=True,
        ),
    ]
)
class MediaFileSerializer(serializers.ModelSerializer):
    """
    Read-only serializer for MediaFile model.

    Used for API responses to provide file information to clients.
    Excludes sensitive internal fields like processing errors.
    """

    file_url = serializers.SerializerMethodField(
        help_text="URL to access the file",
    )

    class Meta:
        """Serializer metadata."""

        model = MediaFile
        fields = [
            "id",
            "original_filename",
            "media_type",
            "mime_type",
            "file_size",
            "visibility",
            "file_url",
            "processing_status",
            "scan_status",
            "version",
            "is_current",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_file_url(self, obj: MediaFile) -> str | None:
        """
        Get protected URL for the file.

        Returns URL to the protected download endpoint rather than
        direct storage URL, ensuring access control is enforced.

        Args:
            obj: MediaFile instance.

        Returns:
            URL to protected download endpoint, or None if no file.
        """
        if not obj.file:
            return None

        from media.services.delivery import FileDeliveryService

        request = self.context.get("request")
        url = FileDeliveryService.get_download_url(obj)

        if request:
            return request.build_absolute_uri(url)
        return url


class MediaFileShareSerializer(serializers.ModelSerializer):
    """
    Read-only serializer for MediaFileShare.

    Used for API responses when listing or viewing shares.
    """

    shared_with_email = serializers.EmailField(
        source="shared_with.email",
        read_only=True,
    )
    shared_by_email = serializers.EmailField(
        source="shared_by.email",
        read_only=True,
    )

    class Meta:
        """Serializer metadata."""

        model = MediaFileShare
        fields = [
            "id",
            "media_file",
            "shared_with",
            "shared_with_email",
            "shared_by",
            "shared_by_email",
            "can_download",
            "expires_at",
            "message",
            "is_expired",
            "created_at",
        ]
        read_only_fields = fields


@extend_schema_serializer(
    examples=[
        OpenApiExample(
            "Share with download permission",
            value={
                "shared_with": "c3d4e5f6-a7b8-9012-cdef-123456789012",
                "can_download": True,
                "expires_in_days": 30,
                "message": "Here are the project files you requested.",
            },
            request_only=True,
        ),
        OpenApiExample(
            "View-only share (no expiration)",
            value={
                "shared_with": "d4e5f6a7-b8c9-0123-def0-234567890123",
                "can_download": False,
            },
            request_only=True,
        ),
    ]
)
class MediaFileShareCreateSerializer(serializers.Serializer):
    """
    Serializer for creating file shares.

    Validates share parameters and creates the share via
    AccessControlService.
    """

    shared_with = serializers.UUIDField(
        help_text="UUID of the user to share with",
    )
    can_download = serializers.BooleanField(
        default=True,
        help_text="Whether the recipient can download (vs view-only)",
    )
    expires_in_days = serializers.IntegerField(
        required=False,
        min_value=1,
        max_value=365,
        help_text="Days until share expires (omit for no expiration)",
    )
    message = serializers.CharField(
        required=False,
        max_length=500,
        allow_blank=True,
        help_text="Optional message to the recipient",
    )

    def validate_shared_with(self, value):
        """Validate the recipient user exists."""
        try:
            user = User.objects.get(pk=value)
            return user
        except User.DoesNotExist:
            raise serializers.ValidationError("User not found")

    def create(self, validated_data: dict[str, Any]) -> MediaFileShare:
        """
        Create the share via AccessControlService.

        Args:
            validated_data: Validated share parameters.

        Returns:
            Created MediaFileShare instance.

        Raises:
            ValidationError: If share creation fails.
        """
        from media.services.access_control import AccessControlService

        media_file = self.context["media_file"]
        request = self.context["request"]

        result = AccessControlService.share_file(
            media_file=media_file,
            owner=request.user,
            recipient=validated_data["shared_with"],
            can_download=validated_data.get("can_download", True),
            expires_in_days=validated_data.get("expires_in_days"),
            message=validated_data.get("message"),
        )

        if not result.success:
            raise serializers.ValidationError(result.error)

        return result.data


# =============================================================================
# Chunked Upload Serializers
# =============================================================================


@extend_schema_serializer(
    examples=[
        OpenApiExample(
            "Large video file",
            value={
                "filename": "company_presentation.mp4",
                "file_size": 536870912,
                "mime_type": "video/mp4",
            },
            request_only=True,
        ),
        OpenApiExample(
            "Large document",
            value={
                "filename": "annual_report_2024.pdf",
                "file_size": 104857600,
                "mime_type": "application/pdf",
            },
            request_only=True,
        ),
    ]
)
class ChunkedUploadInitSerializer(serializers.Serializer):
    """
    Serializer for initializing a chunked upload session.

    Validates the file metadata and returns session information.
    """

    filename = serializers.CharField(max_length=255, help_text="Original filename")
    file_size = serializers.IntegerField(
        min_value=1, help_text="Total file size in bytes"
    )
    mime_type = serializers.CharField(max_length=100, help_text="MIME type of the file")

    # Media type mapping from MIME type
    MIME_TO_MEDIA_TYPE = {
        "image/": "image",
        "video/": "video",
        "audio/": "audio",
        "application/pdf": "document",
        "application/msword": "document",
        "application/vnd.openxmlformats-officedocument": "document",
        "text/": "document",
    }

    def validate_mime_type(self, value: str) -> str:
        """Validate and normalize MIME type."""
        if "/" not in value:
            raise serializers.ValidationError("Invalid MIME type format.")
        return value.lower()

    def validate(self, attrs: dict) -> dict:
        """Derive media_type from mime_type."""
        mime_type = attrs["mime_type"]

        # Determine media type from MIME type
        media_type = "other"
        for prefix, m_type in self.MIME_TO_MEDIA_TYPE.items():
            if mime_type.startswith(prefix):
                media_type = m_type
                break

        attrs["media_type"] = media_type
        return attrs


class ChunkedUploadPartCompleteSerializer(serializers.Serializer):
    """
    Serializer for recording a completed part upload (S3 flow).

    Used when the client uploads directly to S3 and needs to inform
    our server about the completion.
    """

    etag = serializers.CharField(max_length=200)
    part_size = serializers.IntegerField(min_value=1)


@extend_schema_serializer(
    examples=[
        OpenApiExample(
            "Session in progress",
            value={
                "session_id": "e5f6a7b8-c9d0-1234-ef01-234567890abc",
                "filename": "company_presentation.mp4",
                "file_size": 536870912,
                "mime_type": "video/mp4",
                "media_type": "video",
                "bytes_received": 157286400,
                "parts_completed": 15,
                "total_parts": 51,
                "progress_percent": 29.4,
                "chunk_size": 10485760,
                "status": "in_progress",
                "expires_at": "2024-01-16T10:30:00Z",
                "is_complete": False,
                "backend": "s3",
            },
            response_only=True,
        ),
    ]
)
class ChunkedUploadSessionSerializer(serializers.ModelSerializer):
    """
    Serializer for chunked upload session details.

    Returns all information needed for the client to track and complete the upload.
    """

    session_id = serializers.UUIDField(source="id", read_only=True)
    parts_completed = serializers.SerializerMethodField()
    progress_percent = serializers.FloatField(read_only=True)
    total_parts = serializers.IntegerField(read_only=True)
    is_complete = serializers.BooleanField(source="is_upload_complete", read_only=True)

    class Meta:
        model = UploadSession
        fields = [
            "session_id",
            "filename",
            "file_size",
            "mime_type",
            "media_type",
            "bytes_received",
            "parts_completed",
            "total_parts",
            "progress_percent",
            "chunk_size",
            "status",
            "expires_at",
            "is_complete",
            "backend",
        ]
        read_only_fields = fields

    def get_parts_completed(self, obj: UploadSession) -> int:
        """Return count of completed parts."""
        return obj.parts_completed_count


@extend_schema_serializer(
    examples=[
        OpenApiExample(
            "S3 presigned URL",
            value={
                "upload_url": "https://bucket.s3.amazonaws.com/uploads/pending/abc123?X-Amz-Signature=...",
                "part_number": 1,
                "method": "PUT",
                "direct": True,
                "expires_in": 3600,
                "headers": {"Content-Type": "application/octet-stream"},
            },
            response_only=True,
        ),
        OpenApiExample(
            "Local storage endpoint",
            value={
                "upload_url": "/api/v1/media/chunked/sessions/abc-123/parts/1/",
                "part_number": 1,
                "method": "PUT",
                "direct": False,
                "expires_in": None,
                "headers": None,
            },
            response_only=True,
        ),
    ]
)
class ChunkTargetSerializer(serializers.Serializer):
    """
    Serializer for chunk upload target information.

    Tells the client where and how to upload a specific chunk.
    """

    upload_url = serializers.CharField(help_text="URL to upload the chunk to")
    part_number = serializers.IntegerField(help_text="Part number (1-indexed)")
    method = serializers.CharField(help_text="HTTP method to use (PUT)")
    direct = serializers.BooleanField(
        help_text="True if uploading directly to storage (S3)"
    )
    expires_in = serializers.IntegerField(
        allow_null=True, required=False, help_text="Seconds until presigned URL expires"
    )
    headers = serializers.DictField(
        child=serializers.CharField(),
        allow_null=True,
        required=False,
        help_text="Headers to include in the upload request",
    )


@extend_schema_serializer(
    examples=[
        OpenApiExample(
            "Chunk uploaded (not complete)",
            value={
                "bytes_received": 31457280,
                "parts_completed": 3,
                "is_complete": False,
            },
            response_only=True,
        ),
        OpenApiExample(
            "Final chunk uploaded",
            value={
                "bytes_received": 536870912,
                "parts_completed": 51,
                "is_complete": True,
            },
            response_only=True,
        ),
    ]
)
class PartCompletionResultSerializer(serializers.Serializer):
    """
    Serializer for part completion result.

    Returns progress information after a chunk is uploaded.
    """

    bytes_received = serializers.IntegerField(help_text="Total bytes received so far")
    parts_completed = serializers.IntegerField(help_text="Number of parts uploaded")
    is_complete = serializers.BooleanField(
        help_text="True if all parts have been uploaded"
    )


class ChunkedUploadProgressSerializer(serializers.Serializer):
    """
    Serializer for progress information.

    Provides a summary of upload progress.
    """

    session_id = serializers.CharField()
    filename = serializers.CharField()
    file_size = serializers.IntegerField()
    bytes_received = serializers.IntegerField()
    parts_completed = serializers.IntegerField()
    total_parts = serializers.IntegerField()
    progress_percent = serializers.FloatField()
    status = serializers.CharField()


class ChunkedUploadFinalizeResultSerializer(serializers.Serializer):
    """
    Serializer for finalization result.

    Returns the created MediaFile ID.
    """

    media_file_id = serializers.UUIDField()
    message = serializers.CharField()


# =============================================================================
# Quota Status Serializer
# =============================================================================


@extend_schema_serializer(
    examples=[
        OpenApiExample(
            "Plenty of storage available",
            value={
                "total_storage_bytes": 524288000,
                "storage_quota_bytes": 5368709120,
                "storage_remaining_bytes": 4844421120,
                "storage_used_percent": 9.77,
                "storage_used_mb": 500.0,
                "storage_quota_mb": 5120.0,
                "can_upload": True,
            },
            response_only=True,
        ),
        OpenApiExample(
            "Approaching quota limit",
            value={
                "total_storage_bytes": 4831838208,
                "storage_quota_bytes": 5368709120,
                "storage_remaining_bytes": 536870912,
                "storage_used_percent": 90.0,
                "storage_used_mb": 4608.0,
                "storage_quota_mb": 5120.0,
                "can_upload": True,
            },
            response_only=True,
        ),
        OpenApiExample(
            "Quota exceeded",
            value={
                "total_storage_bytes": 5905580032,
                "storage_quota_bytes": 5368709120,
                "storage_remaining_bytes": 0,
                "storage_used_percent": 110.0,
                "storage_used_mb": 5632.0,
                "storage_quota_mb": 5120.0,
                "can_upload": False,
            },
            response_only=True,
        ),
    ]
)
class QuotaStatusSerializer(serializers.Serializer):
    """
    Serializer for storage quota status response.

    Provides all quota-related information for the current user:
    - Bytes used and remaining
    - Percentage used
    - Human-readable MB values
    - Boolean indicating if uploads are allowed
    """

    total_storage_bytes = serializers.IntegerField(
        help_text="Total storage used by this user in bytes"
    )
    storage_quota_bytes = serializers.IntegerField(
        help_text="Maximum storage allowed for this user in bytes"
    )
    storage_remaining_bytes = serializers.IntegerField(
        help_text="Remaining storage available in bytes (0 if over quota)"
    )
    storage_used_percent = serializers.FloatField(
        help_text="Percentage of quota used (can exceed 100 if over quota)"
    )
    storage_used_mb = serializers.FloatField(help_text="Storage used in megabytes")
    storage_quota_mb = serializers.FloatField(help_text="Storage quota in megabytes")
    can_upload = serializers.BooleanField(
        help_text="Whether the user can upload new files"
    )


# =============================================================================
# Tag Serializers
# =============================================================================


@extend_schema_serializer(
    examples=[
        OpenApiExample(
            "User tag",
            value={
                "id": "f6a7b8c9-d0e1-2345-f012-345678901234",
                "name": "Receipts",
                "slug": "receipts",
                "tag_type": "user",
                "category": "Finance",
                "color": "#4CAF50",
                "is_user_tag": True,
                "is_system_tag": False,
                "is_auto_tag": False,
                "created_at": "2024-01-10T09:00:00Z",
            },
            response_only=True,
        ),
        OpenApiExample(
            "Auto-generated tag",
            value={
                "id": "a7b8c9d0-e1f2-3456-0123-456789012345",
                "name": "Invoice",
                "slug": "invoice",
                "tag_type": "auto",
                "category": "Document Type",
                "color": "#2196F3",
                "is_user_tag": False,
                "is_system_tag": False,
                "is_auto_tag": True,
                "created_at": "2024-01-01T00:00:00Z",
            },
            response_only=True,
        ),
    ]
)
class TagSerializer(serializers.ModelSerializer):
    """
    Read-only serializer for Tag model.

    Used for API responses when listing or viewing tags.
    """

    is_user_tag = serializers.BooleanField(read_only=True)
    is_system_tag = serializers.BooleanField(read_only=True)
    is_auto_tag = serializers.BooleanField(read_only=True)

    class Meta:
        """Serializer metadata."""

        model = Tag
        fields = [
            "id",
            "name",
            "slug",
            "tag_type",
            "category",
            "color",
            "is_user_tag",
            "is_system_tag",
            "is_auto_tag",
            "created_at",
        ]
        read_only_fields = fields


@extend_schema_serializer(
    examples=[
        OpenApiExample(
            "Basic tag",
            value={
                "name": "Important",
            },
            request_only=True,
        ),
        OpenApiExample(
            "Tag with category and color",
            value={
                "name": "Tax Documents",
                "category": "Finance",
                "color": "#FF9800",
            },
            request_only=True,
        ),
    ]
)
class TagCreateSerializer(serializers.ModelSerializer):
    """
    Serializer for creating user tags.

    Only user tags can be created through the API.
    System and auto tags are managed internally.
    """

    class Meta:
        """Serializer metadata."""

        model = Tag
        fields = ["name", "category", "color"]

    def validate_name(self, value: str) -> str:
        """Validate tag name length and format."""
        if len(value) < 1:
            raise serializers.ValidationError("Tag name cannot be empty.")
        if len(value) > 100:
            raise serializers.ValidationError("Tag name cannot exceed 100 characters.")
        return value

    def create(self, validated_data: dict[str, Any]) -> Tag:
        """Create a user tag for the current user."""
        request = self.context.get("request")
        user = request.user

        tag, _ = Tag.get_or_create_user_tag(
            name=validated_data["name"],
            owner=user,
            category=validated_data.get("category", ""),
            color=validated_data.get("color", ""),
        )
        return tag


class MediaFileTagSerializer(serializers.ModelSerializer):
    """
    Serializer for MediaFileTag associations.

    Used to represent a tag applied to a file with metadata.
    """

    tag = TagSerializer(read_only=True)
    applied_by_email = serializers.EmailField(
        source="applied_by.email",
        read_only=True,
        allow_null=True,
    )

    class Meta:
        """Serializer metadata."""

        model = MediaFileTag
        fields = [
            "id",
            "tag",
            "applied_by",
            "applied_by_email",
            "confidence",
            "created_at",
        ]
        read_only_fields = fields


@extend_schema_serializer(
    examples=[
        OpenApiExample(
            "Apply by tag ID",
            value={
                "tag_id": "f6a7b8c9-d0e1-2345-f012-345678901234",
            },
            request_only=True,
        ),
        OpenApiExample(
            "Apply by tag name (creates if needed)",
            value={
                "tag_name": "Project Alpha",
            },
            request_only=True,
        ),
    ]
)
class ApplyTagSerializer(serializers.Serializer):
    """
    Serializer for applying a tag to a file.

    Supports applying by tag ID or by tag name (creates if needed).
    """

    tag_id = serializers.UUIDField(
        required=False,
        help_text="ID of existing tag to apply",
    )
    tag_name = serializers.CharField(
        required=False,
        max_length=100,
        help_text="Name of tag to apply (creates user tag if needed)",
    )

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """Ensure either tag_id or tag_name is provided."""
        if not attrs.get("tag_id") and not attrs.get("tag_name"):
            raise serializers.ValidationError(
                "Either tag_id or tag_name must be provided."
            )
        if attrs.get("tag_id") and attrs.get("tag_name"):
            raise serializers.ValidationError(
                "Only one of tag_id or tag_name should be provided."
            )
        return attrs

    def validate_tag_id(self, value: str) -> str:
        """Validate that the tag exists."""
        if value and not Tag.objects.filter(pk=value).exists():
            raise serializers.ValidationError("Tag not found.")
        return value


class FilesByTagsSerializer(serializers.Serializer):
    """
    Serializer for querying files by tags.

    Supports filtering by multiple tags with AND/OR logic.
    """

    tags = serializers.ListField(
        child=serializers.UUIDField(),
        min_length=1,
        help_text="List of tag IDs to filter by",
    )
    mode = serializers.ChoiceField(
        choices=["and", "or"],
        default="and",
        help_text="Match mode: 'and' requires all tags, 'or' requires any tag",
    )


# =============================================================================
# Search Serializers
# =============================================================================


class MediaFileSearchQuerySerializer(serializers.Serializer):
    """
    Serializer for validating search query parameters.

    All parameters are optional - empty request returns browse mode
    (files ordered by created_at).
    """

    q = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Search query (PostgreSQL websearch syntax)",
    )
    media_type = serializers.ChoiceField(
        choices=MediaFile.MediaType.choices,
        required=False,
        help_text="Filter by media type",
    )
    uploaded_after = serializers.DateField(
        required=False,
        help_text="Filter by upload date (inclusive lower bound)",
    )
    uploaded_before = serializers.DateField(
        required=False,
        help_text="Filter by upload date (inclusive upper bound)",
    )
    tags = serializers.CharField(
        required=False,
        help_text="Comma-separated tag slugs (all must match)",
    )
    uploader = serializers.UUIDField(
        required=False,
        help_text="Filter by uploader UUID",
    )

    def validate_tags(self, value: str) -> list[str] | None:
        """Parse comma-separated tags into a list."""
        if not value:
            return None
        return [t.strip() for t in value.split(",") if t.strip()]


class TagMinimalSerializer(serializers.ModelSerializer):
    """Minimal tag serializer for search results."""

    class Meta:
        model = Tag
        fields = ["slug", "name"]
        read_only_fields = fields


class UserMinimalSerializer(serializers.ModelSerializer):
    """Minimal user serializer for search results."""

    class Meta:
        model = User
        fields = ["id", "email"]
        read_only_fields = fields


class MediaFileSearchResultSerializer(serializers.ModelSerializer):
    """
    Serializer for search result items.

    Includes:
    - Basic file info
    - Thumbnail URL (nullable)
    - Relevance score (null in browse mode)
    - Tags and uploader details
    """

    thumbnail_url = serializers.SerializerMethodField(
        help_text="URL to thumbnail image (null if not available)",
    )
    relevance_score = serializers.FloatField(
        read_only=True,
        allow_null=True,
        required=False,
        help_text="Search relevance score (null in browse mode)",
    )
    tags = TagMinimalSerializer(
        many=True,
        read_only=True,
        help_text="Tags applied to this file",
    )
    uploader = UserMinimalSerializer(
        read_only=True,
        help_text="User who uploaded the file",
    )

    class Meta:
        model = MediaFile
        fields = [
            "id",
            "original_filename",
            "media_type",
            "mime_type",
            "file_size",
            "visibility",
            "processing_status",
            "uploader",
            "thumbnail_url",
            "relevance_score",
            "created_at",
            "tags",
        ]
        read_only_fields = fields

    def get_thumbnail_url(self, obj: MediaFile) -> str | None:
        """
        Get the thumbnail URL for the file.

        Returns None if no thumbnail asset exists.
        """
        from media.models import MediaAsset

        thumbnail = obj.assets.filter(asset_type=MediaAsset.AssetType.THUMBNAIL).first()

        if thumbnail and thumbnail.file:
            request = self.context.get("request")
            if request:
                return request.build_absolute_uri(thumbnail.file.url)
            return thumbnail.file.url

        return None
