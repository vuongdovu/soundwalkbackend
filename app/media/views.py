"""
API views for media file uploads.

Provides:
- MediaUploadView: Handle file uploads via POST
"""

from __future__ import annotations

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from media.serializers import MediaFileSerializer, MediaFileUploadSerializer


class MediaUploadView(APIView):
    """
    Handle media file uploads.

    POST /api/v1/media/upload/
        Upload a new media file.

    Authentication:
        Requires valid JWT token.

    Request:
        Content-Type: multipart/form-data
        - file (required): The file to upload
        - visibility (optional): Access level (private, shared, internal)

    Response:
        201 Created: File uploaded successfully
        400 Bad Request: Validation error (invalid file type, size, quota)
        401 Unauthorized: Not authenticated
    """

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(
        summary="Upload media file",
        description=(
            "Upload a new media file. Validates MIME type from content, "
            "enforces size limits, and checks storage quota."
        ),
        request=MediaFileUploadSerializer,
        responses={
            201: OpenApiResponse(
                response=MediaFileSerializer,
                description="File uploaded successfully",
            ),
            400: OpenApiResponse(
                description="Validation error",
            ),
            401: OpenApiResponse(
                description="Not authenticated",
            ),
        },
        tags=["Media"],
    )
    def post(self, request):
        """
        Upload a media file.

        Args:
            request: HTTP request with multipart file data.

        Returns:
            Response with created media file data or validation errors.
        """
        upload_serializer = MediaFileUploadSerializer(
            data=request.data,
            context={"request": request},
        )

        if not upload_serializer.is_valid():
            return Response(
                upload_serializer.errors,
                status=status.HTTP_400_BAD_REQUEST,
            )

        media_file = upload_serializer.save()

        output_serializer = MediaFileSerializer(
            media_file,
            context={"request": request},
        )

        return Response(
            output_serializer.data,
            status=status.HTTP_201_CREATED,
        )
