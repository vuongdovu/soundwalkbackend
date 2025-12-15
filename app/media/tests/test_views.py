"""
Tests for media upload API views.

These tests verify:
- POST /api/v1/media/upload/ endpoint
- Authentication requirements
- File validation through the API
- Response format and status codes

TDD: Write these tests first, then implement views to pass them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.urls import reverse
from rest_framework import status

if TYPE_CHECKING:
    from rest_framework.test import APIClient

    from authentication.models import User


@pytest.mark.django_db
class TestMediaUploadView:
    """Tests for the media upload endpoint."""

    def test_upload_jpeg_returns_201(
        self,
        authenticated_client: "APIClient",
        sample_jpeg_uploaded,
    ):
        """
        Uploading valid JPEG should return 201 Created.

        Why it matters: Core happy path for image uploads.
        """
        url = reverse("media:upload")
        response = authenticated_client.post(
            url,
            {"file": sample_jpeg_uploaded},
            format="multipart",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert "id" in response.data
        assert response.data["media_type"] == "image"
        assert response.data["mime_type"] == "image/jpeg"

    def test_upload_pdf_returns_201(
        self,
        authenticated_client: "APIClient",
        sample_pdf_uploaded,
    ):
        """
        Uploading valid PDF should return 201 Created.

        Why it matters: Documents are key media type.
        """
        url = reverse("media:upload")
        response = authenticated_client.post(
            url,
            {"file": sample_pdf_uploaded},
            format="multipart",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["media_type"] == "document"
        assert response.data["mime_type"] == "application/pdf"

    def test_upload_png_returns_201(
        self,
        authenticated_client: "APIClient",
        sample_png_uploaded,
    ):
        """
        Uploading valid PNG should return 201 Created.

        Why it matters: PNG is common image format.
        """
        url = reverse("media:upload")
        response = authenticated_client.post(
            url,
            {"file": sample_png_uploaded},
            format="multipart",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["media_type"] == "image"
        assert response.data["mime_type"] == "image/png"

    def test_upload_executable_returns_400(
        self,
        authenticated_client: "APIClient",
        executable_file_uploaded,
    ):
        """
        Uploading executable should return 400 Bad Request.

        Why it matters: Security - prevent malware upload.
        """
        url = reverse("media:upload")
        response = authenticated_client.post(
            url,
            {"file": executable_file_uploaded},
            format="multipart",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "file" in response.data

    def test_upload_oversized_returns_400(
        self,
        authenticated_client: "APIClient",
        oversized_image_uploaded,
    ):
        """
        Uploading oversized file should return 400 Bad Request.

        Why it matters: Prevent storage abuse.
        """
        url = reverse("media:upload")
        response = authenticated_client.post(
            url,
            {"file": oversized_image_uploaded},
            format="multipart",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "file" in response.data

    def test_upload_unauthenticated_returns_401(
        self,
        api_client: "APIClient",
        sample_jpeg_uploaded,
    ):
        """
        Uploading without authentication should return 401.

        Why it matters: Uploads require authenticated user.
        """
        url = reverse("media:upload")
        response = api_client.post(
            url,
            {"file": sample_jpeg_uploaded},
            format="multipart",
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_upload_empty_file_returns_400(
        self,
        authenticated_client: "APIClient",
        empty_file_uploaded,
    ):
        """
        Uploading empty file should return 400 Bad Request.

        Why it matters: Empty files are useless.
        """
        url = reverse("media:upload")
        response = authenticated_client.post(
            url,
            {"file": empty_file_uploaded},
            format="multipart",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "file" in response.data

    def test_upload_no_file_returns_400(
        self,
        authenticated_client: "APIClient",
    ):
        """
        Request without file should return 400 Bad Request.

        Why it matters: File is required field.
        """
        url = reverse("media:upload")
        response = authenticated_client.post(
            url,
            {},
            format="multipart",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "file" in response.data


@pytest.mark.django_db
class TestMediaUploadViewVisibility:
    """Tests for visibility handling in upload endpoint."""

    def test_upload_with_private_visibility(
        self,
        authenticated_client: "APIClient",
        sample_jpeg_uploaded,
    ):
        """
        Upload with private visibility should succeed.

        Why it matters: Default visibility mode.
        """
        url = reverse("media:upload")
        response = authenticated_client.post(
            url,
            {
                "file": sample_jpeg_uploaded,
                "visibility": "private",
            },
            format="multipart",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["visibility"] == "private"

    def test_upload_with_shared_visibility(
        self,
        authenticated_client: "APIClient",
        sample_jpeg_uploaded,
    ):
        """
        Upload with shared visibility should succeed.

        Why it matters: Users can share files on upload.
        """
        url = reverse("media:upload")
        response = authenticated_client.post(
            url,
            {
                "file": sample_jpeg_uploaded,
                "visibility": "shared",
            },
            format="multipart",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["visibility"] == "shared"

    def test_upload_defaults_to_private(
        self,
        authenticated_client: "APIClient",
        sample_jpeg_uploaded,
    ):
        """
        Upload without visibility should default to private.

        Why it matters: Secure by default.
        """
        url = reverse("media:upload")
        response = authenticated_client.post(
            url,
            {"file": sample_jpeg_uploaded},
            format="multipart",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["visibility"] == "private"


@pytest.mark.django_db
class TestMediaUploadViewQuota:
    """Tests for storage quota handling in upload endpoint."""

    def test_upload_exceeds_quota_returns_400(
        self,
        authenticated_client_near_quota: "APIClient",
        user_near_quota: "User",
        sample_jpeg_uploaded,
    ):
        """
        Upload that exceeds quota should return 400.

        Why it matters: Prevent exceeding allocated storage.
        """
        # Reduce quota to nearly used amount
        user_near_quota.profile.storage_quota_bytes = (
            user_near_quota.profile.total_storage_bytes + 100
        )
        user_near_quota.profile.save()

        url = reverse("media:upload")
        response = authenticated_client_near_quota.post(
            url,
            {"file": sample_jpeg_uploaded},
            format="multipart",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "file" in response.data


@pytest.mark.django_db
class TestMediaUploadViewResponse:
    """Tests for upload response format."""

    def test_response_includes_file_url(
        self,
        authenticated_client: "APIClient",
        sample_jpeg_uploaded,
    ):
        """
        Response should include file URL.

        Why it matters: Clients need URL to access file.
        """
        url = reverse("media:upload")
        response = authenticated_client.post(
            url,
            {"file": sample_jpeg_uploaded},
            format="multipart",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert "file_url" in response.data
        assert response.data["file_url"] is not None

    def test_response_includes_required_fields(
        self,
        authenticated_client: "APIClient",
        sample_jpeg_uploaded,
    ):
        """
        Response should include all required fields.

        Why it matters: API contract.
        """
        url = reverse("media:upload")
        response = authenticated_client.post(
            url,
            {"file": sample_jpeg_uploaded},
            format="multipart",
        )

        assert response.status_code == status.HTTP_201_CREATED

        required_fields = [
            "id",
            "original_filename",
            "media_type",
            "mime_type",
            "file_size",
            "visibility",
            "file_url",
            "created_at",
        ]

        for field in required_fields:
            assert field in response.data, f"Missing field: {field}"
