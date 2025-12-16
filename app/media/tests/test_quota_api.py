"""
Tests for quota status API endpoint.

This module tests:
- GET /api/v1/media/quota/ returns correct quota information
- Authentication is required
- Response contains all expected fields

TDD: These tests are written before implementing the endpoint.
"""

import uuid

import pytest
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from authentication.tests.factories import UserFactory


# =============================================================================
# Test Fixtures
# =============================================================================


def create_unique_user(**kwargs):
    """Create a user with a unique email to avoid factory sequence collisions."""
    unique_email = f"test_{uuid.uuid4().hex[:8]}@example.com"
    return UserFactory(email=unique_email, **kwargs)


@pytest.fixture
def user(db):
    """Create a verified user with known quota values."""
    user = create_unique_user(email_verified=True)
    user.profile.storage_quota_bytes = 25 * 1024 * 1024 * 1024  # 25GB
    user.profile.total_storage_bytes = 5 * 1024 * 1024 * 1024  # 5GB used
    user.profile.save()
    return user


@pytest.fixture
def api_client():
    """Unauthenticated API client."""
    return APIClient()


@pytest.fixture
def authenticated_client(user):
    """API client authenticated with JWT token."""
    client = APIClient()
    refresh = RefreshToken.for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


# =============================================================================
# GET /api/v1/media/quota/ Tests
# =============================================================================


@pytest.mark.django_db
class TestQuotaStatusEndpoint:
    """Tests for GET /api/v1/media/quota/ endpoint."""

    def test_quota_status_requires_authentication(self, api_client):
        """
        Unauthenticated requests should return 401.

        Why it matters: Quota is user-specific, sensitive data.
        """
        response = api_client.get("/api/v1/media/quota/")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_quota_status_returns_200_for_authenticated_user(
        self, authenticated_client
    ):
        """
        Authenticated requests should return 200 OK.

        Why it matters: Basic endpoint functionality.
        """
        response = authenticated_client.get("/api/v1/media/quota/")

        assert response.status_code == status.HTTP_200_OK

    def test_quota_status_contains_total_storage_bytes(
        self, authenticated_client, user
    ):
        """
        Response should include total_storage_bytes.

        Why it matters: Shows how much storage is used.
        """
        response = authenticated_client.get("/api/v1/media/quota/")

        assert "total_storage_bytes" in response.data
        assert response.data["total_storage_bytes"] == user.profile.total_storage_bytes

    def test_quota_status_contains_storage_quota_bytes(
        self, authenticated_client, user
    ):
        """
        Response should include storage_quota_bytes.

        Why it matters: Shows user's quota limit.
        """
        response = authenticated_client.get("/api/v1/media/quota/")

        assert "storage_quota_bytes" in response.data
        assert response.data["storage_quota_bytes"] == user.profile.storage_quota_bytes

    def test_quota_status_contains_storage_remaining_bytes(
        self, authenticated_client, user
    ):
        """
        Response should include storage_remaining_bytes.

        Why it matters: Shows how much space is available.
        """
        response = authenticated_client.get("/api/v1/media/quota/")

        assert "storage_remaining_bytes" in response.data
        expected_remaining = (
            user.profile.storage_quota_bytes - user.profile.total_storage_bytes
        )
        assert response.data["storage_remaining_bytes"] == expected_remaining

    def test_quota_status_contains_storage_used_percent(
        self, authenticated_client, user
    ):
        """
        Response should include storage_used_percent.

        Why it matters: Easy-to-display percentage for UI.
        """
        response = authenticated_client.get("/api/v1/media/quota/")

        assert "storage_used_percent" in response.data
        # 5GB / 25GB = 20%
        assert response.data["storage_used_percent"] == 20.0

    def test_quota_status_contains_storage_used_mb(self, authenticated_client, user):
        """
        Response should include storage_used_mb.

        Why it matters: Human-readable size in megabytes.
        """
        response = authenticated_client.get("/api/v1/media/quota/")

        assert "storage_used_mb" in response.data
        # 5GB = 5120MB
        expected_mb = user.profile.total_storage_bytes / (1024 * 1024)
        assert response.data["storage_used_mb"] == expected_mb

    def test_quota_status_contains_storage_quota_mb(self, authenticated_client, user):
        """
        Response should include storage_quota_mb.

        Why it matters: Human-readable quota in megabytes.
        """
        response = authenticated_client.get("/api/v1/media/quota/")

        assert "storage_quota_mb" in response.data
        # 25GB = 25600MB
        expected_mb = user.profile.storage_quota_bytes / (1024 * 1024)
        assert response.data["storage_quota_mb"] == expected_mb

    def test_quota_status_contains_can_upload(self, authenticated_client, user):
        """
        Response should include can_upload boolean.

        Why it matters: Quick check if user can upload.
        """
        response = authenticated_client.get("/api/v1/media/quota/")

        assert "can_upload" in response.data
        assert response.data["can_upload"] is True  # Still has 20GB remaining

    def test_quota_status_can_upload_false_when_at_quota(
        self, authenticated_client, user
    ):
        """
        can_upload should be False when quota is exhausted.

        Why it matters: Prevents uploads when no space left.
        """
        # Set used bytes to equal quota
        user.profile.total_storage_bytes = user.profile.storage_quota_bytes
        user.profile.save()

        response = authenticated_client.get("/api/v1/media/quota/")

        assert response.data["can_upload"] is False
        assert response.data["storage_remaining_bytes"] == 0

    def test_quota_status_handles_zero_usage(self, authenticated_client, user):
        """
        Response handles user with zero storage used.

        Why it matters: Edge case for new users.
        """
        user.profile.total_storage_bytes = 0
        user.profile.save()

        response = authenticated_client.get("/api/v1/media/quota/")

        assert response.data["total_storage_bytes"] == 0
        assert response.data["storage_used_percent"] == 0.0
        assert response.data["storage_used_mb"] == 0.0
        assert response.data["can_upload"] is True

    def test_quota_status_handles_over_quota(self, authenticated_client, user):
        """
        Response handles user over quota gracefully.

        Why it matters: Edge case for corrupted data.
        """
        # Set 5GB over quota (significant amount for percentage calculation)
        user.profile.total_storage_bytes = (
            user.profile.storage_quota_bytes + 5 * 1024 * 1024 * 1024
        )
        user.profile.save()

        response = authenticated_client.get("/api/v1/media/quota/")

        assert response.data["storage_remaining_bytes"] == 0  # Floors at 0
        assert response.data["storage_used_percent"] > 100.0  # 120% (30GB / 25GB)
        assert response.data["can_upload"] is False
