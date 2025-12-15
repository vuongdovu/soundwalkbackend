"""
Serializers for media file uploads.

Provides:
- MediaFileUploadSerializer: Handle file upload with validation
- MediaFileSerializer: Read-only serializer for API responses
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rest_framework import serializers

from media.models import MediaFile
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

        # Trigger async processing (thumbnail generation, etc.)
        # Import here to avoid circular imports
        from media.tasks import process_media_file

        process_media_file.delay(str(media_file.id))

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
        Get absolute URL for the file.

        Args:
            obj: MediaFile instance.

        Returns:
            Absolute URL to access the file, or None if no file.
        """
        if not obj.file:
            return None

        request = self.context.get("request")
        if request:
            return request.build_absolute_uri(obj.file.url)
        return obj.file.url
