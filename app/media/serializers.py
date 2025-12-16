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

from rest_framework import serializers

from authentication.models import User
from media.models import MediaFile, MediaFileShare, UploadSession
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

        # Trigger async scan -> process chain
        # Import here to avoid circular imports
        from celery import chain

        from media.tasks import process_media_file, scan_file_for_malware

        # Chain: scan first, then process if not infected
        task_chain = chain(
            scan_file_for_malware.s(str(media_file.id)),
            process_media_file.s(),
        )
        task_chain.delay()

        return media_file


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


class ChunkedUploadInitSerializer(serializers.Serializer):
    """
    Serializer for initializing a chunked upload session.

    Validates the file metadata and returns session information.
    """

    filename = serializers.CharField(max_length=255)
    file_size = serializers.IntegerField(min_value=1)
    mime_type = serializers.CharField(max_length=100)

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


class ChunkTargetSerializer(serializers.Serializer):
    """
    Serializer for chunk upload target information.

    Tells the client where and how to upload a specific chunk.
    """

    upload_url = serializers.CharField()
    part_number = serializers.IntegerField()
    method = serializers.CharField()
    direct = serializers.BooleanField()
    expires_in = serializers.IntegerField(allow_null=True, required=False)
    headers = serializers.DictField(
        child=serializers.CharField(),
        allow_null=True,
        required=False,
    )


class PartCompletionResultSerializer(serializers.Serializer):
    """
    Serializer for part completion result.

    Returns progress information after a chunk is uploaded.
    """

    bytes_received = serializers.IntegerField()
    parts_completed = serializers.IntegerField()
    is_complete = serializers.BooleanField()


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
