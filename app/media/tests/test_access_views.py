"""
Tests for protected media views and share management endpoints.

These tests verify:
- File download endpoint with access control
- File view endpoint with access control
- Share creation and revocation
- Share listing

TDD: Write these tests first, then implement views to pass them.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from media.models import MediaFile
from media.services.access_control import AccessControlService

if TYPE_CHECKING:
    from rest_framework.test import APIClient

    from authentication.models import User


@pytest.mark.django_db
class TestMediaFileDownloadView:
    """Tests for file download endpoint."""

    def test_owner_can_download_file(
        self,
        authenticated_client: "APIClient",
        user: "User",
        media_file_for_processing: MediaFile,
    ):
        """
        Owner should be able to download their file.

        Why it matters: Core functionality for file access.
        """
        url = reverse(
            "media:download",
            kwargs={"file_id": str(media_file_for_processing.id)},
        )
        response = authenticated_client.get(url)

        # Should succeed (FileResponse or X-Accel-Redirect)
        assert response.status_code == status.HTTP_200_OK

    def test_unauthenticated_cannot_download(
        self,
        api_client: "APIClient",
        media_file_for_processing: MediaFile,
    ):
        """
        Unauthenticated user should get 401.

        Why it matters: Downloads require authentication.
        """
        url = reverse(
            "media:download",
            kwargs={"file_id": str(media_file_for_processing.id)},
        )
        response = api_client.get(url)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_non_owner_cannot_download_private_file(
        self,
        other_authenticated_client: "APIClient",
        media_file_for_processing: MediaFile,
    ):
        """
        Non-owner should get 403 for private file.

        Why it matters: Private files are restricted.
        """
        media_file_for_processing.visibility = MediaFile.Visibility.PRIVATE
        media_file_for_processing.save()

        url = reverse(
            "media:download",
            kwargs={"file_id": str(media_file_for_processing.id)},
        )
        response = other_authenticated_client.get(url)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_shared_user_with_download_access_can_download(
        self,
        authenticated_client: "APIClient",
        other_authenticated_client: "APIClient",
        user: "User",
        other_user: "User",
        media_file_for_processing: MediaFile,
    ):
        """
        User with DOWNLOAD share can download the file.

        Why it matters: Sharing grants download access.
        """
        media_file_for_processing.visibility = MediaFile.Visibility.SHARED
        media_file_for_processing.save()

        # Create share with download permission
        AccessControlService.share_file(
            media_file=media_file_for_processing,
            owner=user,
            recipient=other_user,
            can_download=True,
        )

        url = reverse(
            "media:download",
            kwargs={"file_id": str(media_file_for_processing.id)},
        )
        response = other_authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK

    def test_shared_user_with_view_only_cannot_download(
        self,
        authenticated_client: "APIClient",
        other_authenticated_client: "APIClient",
        user: "User",
        other_user: "User",
        media_file_for_processing: MediaFile,
    ):
        """
        User with VIEW-only share cannot download the file.

        Why it matters: VIEW doesn't grant download.
        """
        media_file_for_processing.visibility = MediaFile.Visibility.SHARED
        media_file_for_processing.save()

        # Create VIEW-only share (can_download=False)
        AccessControlService.share_file(
            media_file=media_file_for_processing,
            owner=user,
            recipient=other_user,
            can_download=False,
        )

        url = reverse(
            "media:download",
            kwargs={"file_id": str(media_file_for_processing.id)},
        )
        response = other_authenticated_client.get(url)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_nonexistent_file_returns_404(
        self,
        authenticated_client: "APIClient",
    ):
        """
        Request for nonexistent file should return 404.

        Why it matters: Clear error for missing resources.
        """
        import uuid

        url = reverse(
            "media:download",
            kwargs={"file_id": str(uuid.uuid4())},
        )
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestMediaFileViewView:
    """Tests for file view (inline) endpoint."""

    def test_owner_can_view_file(
        self,
        authenticated_client: "APIClient",
        media_file_for_processing: MediaFile,
    ):
        """
        Owner should be able to view their file inline.

        Why it matters: Core functionality for file preview.
        """
        url = reverse(
            "media:view",
            kwargs={"file_id": str(media_file_for_processing.id)},
        )
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK

    def test_shared_user_with_view_access_can_view(
        self,
        authenticated_client: "APIClient",
        other_authenticated_client: "APIClient",
        user: "User",
        other_user: "User",
        media_file_for_processing: MediaFile,
    ):
        """
        User with VIEW share can view the file inline.

        Why it matters: VIEW grants preview access.
        """
        media_file_for_processing.visibility = MediaFile.Visibility.SHARED
        media_file_for_processing.save()

        # Create VIEW share
        AccessControlService.share_file(
            media_file=media_file_for_processing,
            owner=user,
            recipient=other_user,
            can_download=False,  # View only
        )

        url = reverse(
            "media:view",
            kwargs={"file_id": str(media_file_for_processing.id)},
        )
        response = other_authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK


@pytest.mark.django_db
class TestMediaFileDetailView:
    """Tests for file detail/metadata endpoint."""

    def test_owner_can_get_file_details(
        self,
        authenticated_client: "APIClient",
        media_file_for_processing: MediaFile,
    ):
        """
        Owner should be able to get file metadata.

        Why it matters: Users need to see file info.
        """
        url = reverse(
            "media:detail",
            kwargs={"file_id": str(media_file_for_processing.id)},
        )
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["id"] == str(media_file_for_processing.id)
        assert (
            response.data["original_filename"]
            == media_file_for_processing.original_filename
        )

    def test_unauthorized_user_cannot_get_details(
        self,
        other_authenticated_client: "APIClient",
        media_file_for_processing: MediaFile,
    ):
        """
        Non-owner should get 403 for private file details.

        Why it matters: Metadata is also protected.
        """
        media_file_for_processing.visibility = MediaFile.Visibility.PRIVATE
        media_file_for_processing.save()

        url = reverse(
            "media:detail",
            kwargs={"file_id": str(media_file_for_processing.id)},
        )
        response = other_authenticated_client.get(url)

        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
class TestMediaFileShareCreate:
    """Tests for share creation endpoint."""

    def test_owner_can_create_share(
        self,
        authenticated_client: "APIClient",
        user: "User",
        other_user: "User",
        media_file_for_processing: MediaFile,
    ):
        """
        Owner should be able to share file with another user.

        Why it matters: Core sharing functionality.
        """
        url = reverse(
            "media:shares",
            kwargs={"file_id": str(media_file_for_processing.id)},
        )
        response = authenticated_client.post(
            url,
            {
                "shared_with": str(other_user.id),
                "can_download": False,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert str(response.data["shared_with"]) == str(other_user.id)
        assert response.data["can_download"] is False

    def test_non_owner_cannot_create_share(
        self,
        other_authenticated_client: "APIClient",
        user: "User",
        third_user: "User",
        media_file_for_processing: MediaFile,
    ):
        """
        Non-owner should get 400 when trying to share.

        Why it matters: Only owners control sharing.
        """
        url = reverse(
            "media:shares",
            kwargs={"file_id": str(media_file_for_processing.id)},
        )
        response = other_authenticated_client.post(
            url,
            {
                "shared_with": str(third_user.id),
                "can_download": True,
            },
            format="json",
        )

        # Returns 400 because serializer raises ValidationError
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_cannot_share_with_self(
        self,
        authenticated_client: "APIClient",
        user: "User",
        media_file_for_processing: MediaFile,
    ):
        """
        Owner cannot share file with themselves.

        Why it matters: Self-sharing is redundant.
        """
        url = reverse(
            "media:shares",
            kwargs={"file_id": str(media_file_for_processing.id)},
        )
        response = authenticated_client.post(
            url,
            {
                "shared_with": str(user.id),
                "can_download": True,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_share_with_expiration(
        self,
        authenticated_client: "APIClient",
        user: "User",
        other_user: "User",
        media_file_for_processing: MediaFile,
    ):
        """
        Owner can create share with expiration days.

        Why it matters: Time-limited access is a feature.
        """
        url = reverse(
            "media:shares",
            kwargs={"file_id": str(media_file_for_processing.id)},
        )
        response = authenticated_client.post(
            url,
            {
                "shared_with": str(other_user.id),
                "can_download": True,
                "expires_in_days": 7,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["expires_at"] is not None


@pytest.mark.django_db
class TestMediaFileShareList:
    """Tests for share listing endpoint."""

    def test_owner_can_list_shares(
        self,
        authenticated_client: "APIClient",
        user: "User",
        other_user: "User",
        third_user: "User",
        media_file_for_processing: MediaFile,
    ):
        """
        Owner should be able to list all shares for a file.

        Why it matters: Owners need to manage access.
        """
        media_file_for_processing.visibility = MediaFile.Visibility.SHARED
        media_file_for_processing.save()

        # Create shares
        AccessControlService.share_file(
            media_file=media_file_for_processing,
            owner=user,
            recipient=other_user,
            can_download=False,
        )
        AccessControlService.share_file(
            media_file=media_file_for_processing,
            owner=user,
            recipient=third_user,
            can_download=True,
        )

        url = reverse(
            "media:shares",
            kwargs={"file_id": str(media_file_for_processing.id)},
        )
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 2

    def test_non_owner_cannot_list_shares(
        self,
        other_authenticated_client: "APIClient",
        media_file_for_processing: MediaFile,
    ):
        """
        Non-owner should get 403 when listing shares.

        Why it matters: Share list is private to owner.
        """
        url = reverse(
            "media:shares",
            kwargs={"file_id": str(media_file_for_processing.id)},
        )
        response = other_authenticated_client.get(url)

        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
class TestMediaFileShareRevoke:
    """Tests for share revocation endpoint."""

    def test_owner_can_revoke_share(
        self,
        authenticated_client: "APIClient",
        user: "User",
        other_user: "User",
        media_file_for_processing: MediaFile,
    ):
        """
        Owner should be able to revoke a share.

        Why it matters: Owners control access.
        """
        media_file_for_processing.visibility = MediaFile.Visibility.SHARED
        media_file_for_processing.save()

        # Create share
        AccessControlService.share_file(
            media_file=media_file_for_processing,
            owner=user,
            recipient=other_user,
            can_download=True,
        )

        url = reverse(
            "media:share-delete",
            kwargs={
                "file_id": str(media_file_for_processing.id),
                "user_id": str(other_user.id),
            },
        )
        response = authenticated_client.delete(url)

        assert response.status_code == status.HTTP_204_NO_CONTENT

        # Verify access revoked
        assert not AccessControlService.user_can_access(
            other_user, media_file_for_processing
        )

    def test_non_owner_cannot_revoke_share(
        self,
        other_authenticated_client: "APIClient",
        user: "User",
        other_user: "User",
        third_user: "User",
        media_file_for_processing: MediaFile,
    ):
        """
        Non-owner should get 403 when trying to revoke share.

        Why it matters: Only owners control access.
        """
        media_file_for_processing.visibility = MediaFile.Visibility.SHARED
        media_file_for_processing.save()

        # Create share as owner
        AccessControlService.share_file(
            media_file=media_file_for_processing,
            owner=user,
            recipient=third_user,
            can_download=True,
        )

        url = reverse(
            "media:share-delete",
            kwargs={
                "file_id": str(media_file_for_processing.id),
                "user_id": str(third_user.id),
            },
        )
        response = other_authenticated_client.delete(url)

        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
class TestSharedWithMeView:
    """Tests for listing files shared with current user."""

    def test_list_files_shared_with_me(
        self,
        authenticated_client: "APIClient",
        other_authenticated_client: "APIClient",
        user: "User",
        other_user: "User",
        media_file_for_processing: MediaFile,
    ):
        """
        User should see files shared with them.

        Why it matters: Users need to find shared files.
        """
        media_file_for_processing.visibility = MediaFile.Visibility.SHARED
        media_file_for_processing.save()

        # Share with other_user
        AccessControlService.share_file(
            media_file=media_file_for_processing,
            owner=user,
            recipient=other_user,
            can_download=True,
        )

        url = reverse("media:shared-with-me")
        response = other_authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) >= 1

        # Find our file in response
        file_ids = [str(f["id"]) for f in response.data]
        assert str(media_file_for_processing.id) in file_ids

    def test_shared_with_me_excludes_expired_shares(
        self,
        other_authenticated_client: "APIClient",
        user: "User",
        other_user: "User",
        media_file_for_processing: MediaFile,
    ):
        """
        Expired shares should not appear in shared-with-me list.

        Why it matters: Expired access is no access.
        """
        media_file_for_processing.visibility = MediaFile.Visibility.SHARED
        media_file_for_processing.save()

        # Create share then expire it
        result = AccessControlService.share_file(
            media_file=media_file_for_processing,
            owner=user,
            recipient=other_user,
            can_download=True,
            expires_in_days=1,
        )
        share = result.data
        share.expires_at = timezone.now() - timedelta(hours=1)
        share.save()

        url = reverse("media:shared-with-me")
        response = other_authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK

        # Our file should NOT be in the list
        file_ids = [str(f["id"]) for f in response.data]
        assert str(media_file_for_processing.id) not in file_ids
