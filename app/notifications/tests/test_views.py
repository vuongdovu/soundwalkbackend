"""
API tests for notification endpoints.

Tests follow TDD principles - written before implementation.
Tests verify HTTP behavior, serialization, authentication, and permissions.

Test Classes:
    TestNotificationList: Tests for GET /api/v1/notifications/
    TestNotificationDetail: Tests for GET /api/v1/notifications/{id}/
    TestUnreadCount: Tests for GET /api/v1/notifications/unread-count/
    TestMarkSingleRead: Tests for POST /api/v1/notifications/{id}/read/
    TestMarkAllRead: Tests for POST /api/v1/notifications/read-all/
"""

from django.urls import reverse
from rest_framework import status


class TestNotificationList:
    """
    Tests for GET /api/v1/notifications/.

    Verifies:
    - Returns user's notifications
    - Excludes other users' notifications
    - Ordering (newest first)
    - Pagination
    - Filtering by is_read
    - Filtering by notification type
    - Authentication requirement
    """

    def test_returns_users_notifications(
        self, db, authenticated_client, notification, user
    ):
        """Returns the authenticated user's notifications."""
        url = reverse("notifications:notification-list")
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1
        assert response.data["results"][0]["id"] == notification.id
        assert response.data["results"][0]["title"] == notification.title

    def test_excludes_other_users_notifications(
        self, db, authenticated_client, notification, other_user_notifications
    ):
        """Only returns notifications for the authenticated user."""
        url = reverse("notifications:notification-list")
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        # Should only see the user's notification, not other_user's 3 notifications
        assert response.data["count"] == 1

    def test_ordered_by_created_at_desc(
        self, db, authenticated_client, multiple_unread_notifications
    ):
        """Notifications are returned newest first."""
        url = reverse("notifications:notification-list")
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        results = response.data["results"]

        # Each notification should be more recent than the next
        for i in range(len(results) - 1):
            assert results[i]["created_at"] >= results[i + 1]["created_at"]

    def test_pagination(self, db, authenticated_client, many_notifications):
        """Returns paginated results."""
        url = reverse("notifications:notification-list")
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 50
        # Default page size is 20
        assert len(response.data["results"]) == 20
        assert response.data["next"] is not None

    def test_filter_by_is_read_true(
        self, db, authenticated_client, mixed_notifications
    ):
        """Filter to show only read notifications."""
        url = reverse("notifications:notification-list")
        response = authenticated_client.get(url, {"is_read": "true"})

        assert response.status_code == status.HTTP_200_OK
        # mixed_notifications has 2 read notifications
        assert response.data["count"] == 2
        for notification in response.data["results"]:
            assert notification["is_read"] is True

    def test_filter_by_is_read_false(
        self, db, authenticated_client, mixed_notifications
    ):
        """Filter to show only unread notifications."""
        url = reverse("notifications:notification-list")
        response = authenticated_client.get(url, {"is_read": "false"})

        assert response.status_code == status.HTTP_200_OK
        # mixed_notifications has 3 unread notifications
        assert response.data["count"] == 3
        for notification in response.data["results"]:
            assert notification["is_read"] is False

    def test_filter_by_type(
        self, db, authenticated_client, notifications_of_different_types
    ):
        """Filter notifications by type key."""
        url = reverse("notifications:notification-list")
        response = authenticated_client.get(url, {"type": "type_a"})

        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1
        assert response.data["results"][0]["type_key"] == "type_a"

    def test_requires_authentication(self, db, api_client, notification):
        """Unauthenticated requests are rejected."""
        url = reverse("notifications:notification-list")
        response = api_client.get(url)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestNotificationDetail:
    """
    Tests for GET /api/v1/notifications/{id}/.

    Verifies:
    - Returns notification details
    - 404 for nonexistent notification
    - 404 for other user's notification (not 403 to avoid enumeration)
    - Serialized fields
    """

    def test_returns_notification_details(
        self, db, authenticated_client, notification_with_data
    ):
        """Returns full notification details."""
        url = reverse(
            "notifications:notification-detail",
            kwargs={"pk": notification_with_data.pk},
        )
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["id"] == notification_with_data.id
        assert response.data["title"] == notification_with_data.title
        assert response.data["body"] == notification_with_data.body
        assert response.data["data"] == notification_with_data.data
        assert "created_at" in response.data

    def test_includes_type_key(self, db, authenticated_client, notification):
        """Response includes notification type key."""
        url = reverse(
            "notifications:notification-detail", kwargs={"pk": notification.pk}
        )
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["type_key"] == notification.notification_type.key

    def test_404_for_nonexistent(self, db, authenticated_client):
        """Returns 404 for nonexistent notification ID."""
        url = reverse("notifications:notification-detail", kwargs={"pk": 99999})
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_404_for_other_users_notification(
        self, db, authenticated_client, other_user_notifications
    ):
        """Returns 404 for other user's notification (prevents enumeration)."""
        other_notification = other_user_notifications[0]
        url = reverse(
            "notifications:notification-detail", kwargs={"pk": other_notification.pk}
        )
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestUnreadCount:
    """
    Tests for GET /api/v1/notifications/unread-count/.

    Verifies:
    - Returns correct unread count
    - Only counts user's notifications
    - Excludes read notifications
    """

    def test_returns_correct_count(
        self, db, authenticated_client, multiple_unread_notifications
    ):
        """Returns accurate unread notification count."""
        url = reverse("notifications:notification-unread-count")
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["unread_count"] == 5

    def test_only_counts_users_notifications(
        self, db, authenticated_client, notification, other_user_notifications
    ):
        """Only counts notifications belonging to authenticated user."""
        url = reverse("notifications:notification-unread-count")
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        # User has 1 notification, other_user has 3 - should only see 1
        assert response.data["unread_count"] == 1

    def test_excludes_read_notifications(
        self, db, authenticated_client, mixed_notifications
    ):
        """Excludes read notifications from count."""
        url = reverse("notifications:notification-unread-count")
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        # mixed_notifications has 3 unread, 2 read
        assert response.data["unread_count"] == 3

    def test_returns_zero_when_no_notifications(self, db, authenticated_client):
        """Returns zero when user has no notifications."""
        url = reverse("notifications:notification-unread-count")
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["unread_count"] == 0


class TestMarkSingleRead:
    """
    Tests for POST /api/v1/notifications/{id}/read/.

    Verifies:
    - Successfully marks notification as read
    - Idempotent (already read succeeds)
    - Cannot mark other user's notification
    - 404 for nonexistent notification
    """

    def test_marks_notification_as_read(
        self, db, authenticated_client, unread_notification
    ):
        """Successfully marks notification as read."""
        url = reverse(
            "notifications:notification-read", kwargs={"pk": unread_notification.pk}
        )
        response = authenticated_client.post(url)

        assert response.status_code == status.HTTP_200_OK
        unread_notification.refresh_from_db()
        assert unread_notification.is_read is True

    def test_idempotent_already_read(self, db, authenticated_client, read_notification):
        """Marking already-read notification succeeds."""
        url = reverse(
            "notifications:notification-read", kwargs={"pk": read_notification.pk}
        )
        response = authenticated_client.post(url)

        assert response.status_code == status.HTTP_200_OK
        read_notification.refresh_from_db()
        assert read_notification.is_read is True

    def test_cannot_read_other_users_notification(
        self, db, authenticated_client, other_user_notifications
    ):
        """Returns 404 for other user's notification."""
        other_notification = other_user_notifications[0]
        url = reverse(
            "notifications:notification-read", kwargs={"pk": other_notification.pk}
        )
        response = authenticated_client.post(url)

        # 404 instead of 403 to prevent enumeration
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_404_for_nonexistent(self, db, authenticated_client):
        """Returns 404 for nonexistent notification."""
        url = reverse("notifications:notification-read", kwargs={"pk": 99999})
        response = authenticated_client.post(url)

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestMarkAllRead:
    """
    Tests for POST /api/v1/notifications/read-all/.

    Verifies:
    - Marks all unread notifications as read
    - Returns count of marked notifications
    - Only affects user's notifications
    """

    def test_marks_all_as_read(
        self, db, authenticated_client, multiple_unread_notifications
    ):
        """Marks all user's unread notifications as read."""
        from notifications.models import Notification

        url = reverse("notifications:notification-read-all")
        response = authenticated_client.post(url)

        assert response.status_code == status.HTTP_200_OK

        # Verify all are read
        unread = Notification.objects.filter(
            recipient=multiple_unread_notifications[0].recipient,
            is_read=False,
        ).count()
        assert unread == 0

    def test_returns_marked_count(
        self, db, authenticated_client, multiple_unread_notifications
    ):
        """Response includes count of notifications marked."""
        url = reverse("notifications:notification-read-all")
        response = authenticated_client.post(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["marked_count"] == 5

    def test_only_affects_users_notifications(
        self,
        db,
        authenticated_client,
        multiple_unread_notifications,
        other_user_notifications,
    ):
        """Only marks authenticated user's notifications."""
        from notifications.models import Notification

        url = reverse("notifications:notification-read-all")
        response = authenticated_client.post(url)

        assert response.status_code == status.HTTP_200_OK

        # Other user's notifications should still be unread
        other_unread = Notification.objects.filter(
            recipient=other_user_notifications[0].recipient,
            is_read=False,
        ).count()
        assert other_unread == 3

    def test_returns_zero_when_no_unread(self, db, authenticated_client):
        """Returns zero when user has no unread notifications."""
        url = reverse("notifications:notification-read-all")
        response = authenticated_client.post(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["marked_count"] == 0


class TestActorSerialization:
    """Tests for actor field serialization."""

    def test_actor_name_included_when_actor_exists(
        self, db, authenticated_client, notification_with_actor
    ):
        """Response includes actor name when actor exists."""
        url = reverse(
            "notifications:notification-detail",
            kwargs={"pk": notification_with_actor.pk},
        )
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert "actor_name" in response.data
        # Actor name should be the email (no display_name on User model)
        assert response.data["actor_name"] is not None

    def test_actor_name_null_for_system_notification(
        self, db, authenticated_client, notification_without_actor
    ):
        """Actor name is null for system notifications (no actor)."""
        url = reverse(
            "notifications:notification-detail",
            kwargs={"pk": notification_without_actor.pk},
        )
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["actor_name"] is None
