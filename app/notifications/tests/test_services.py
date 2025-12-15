"""
Unit tests for notification services.

Tests follow TDD principles - written before implementation.
Tests focus on service behavior and ServiceResult patterns.

Test Classes:
    TestNotificationServiceCreate: Tests for create_notification()
    TestNotificationServiceMarkAsRead: Tests for mark_as_read()
    TestNotificationServiceMarkAllAsRead: Tests for mark_all_as_read()
"""

import pytest
from django.contrib.contenttypes.models import ContentType


class TestNotificationServiceCreate:
    """
    Tests for NotificationService.create_notification().

    Verifies:
    - Template rendering
    - Type validation (unknown key, inactive type)
    - Actor and source object handling
    - JSON data storage
    - Celery task enqueueing
    """

    def test_creates_notification_with_rendered_templates(
        self, db, notification_type_with_placeholders, user
    ):
        """Successfully creates notification with rendered template."""
        from notifications.services import NotificationService

        result = NotificationService.create_notification(
            recipient=user,
            type_key="template_notification",
            data={"user_name": "Alice", "actor_name": "Bob"},
        )

        assert result.success
        notification = result.data
        assert notification.title == "Hello, Alice!"
        assert notification.body == "You have a new notification from Bob."
        assert notification.recipient == user

    def test_explicit_title_body_overrides_template(
        self, db, notification_type_with_placeholders, user
    ):
        """Explicit title and body override template rendering."""
        from notifications.services import NotificationService

        result = NotificationService.create_notification(
            recipient=user,
            type_key="template_notification",
            title="Custom Title",
            body="Custom Body",
        )

        assert result.success
        notification = result.data
        assert notification.title == "Custom Title"
        assert notification.body == "Custom Body"

    def test_fails_for_unknown_type_key(self, db, user):
        """Returns failure for unknown notification type key."""
        from notifications.services import NotificationService

        result = NotificationService.create_notification(
            recipient=user,
            type_key="nonexistent_type",
            title="Test",
        )

        assert not result.success
        assert result.error_code == "TYPE_NOT_FOUND"
        assert "nonexistent_type" in result.error

    def test_fails_for_inactive_type(self, db, inactive_notification_type, user):
        """Returns failure for inactive notification type."""
        from notifications.services import NotificationService

        result = NotificationService.create_notification(
            recipient=user,
            type_key="inactive_notification",
            title="Test",
        )

        assert not result.success
        assert result.error_code == "TYPE_INACTIVE"

    def test_fails_on_missing_template_placeholder(
        self, db, notification_type_with_placeholders, user
    ):
        """Raises KeyError when template placeholder is missing."""
        from notifications.services import NotificationService

        # Template expects {user_name} and {actor_name}
        # Only provide user_name
        with pytest.raises(KeyError):
            NotificationService.create_notification(
                recipient=user,
                type_key="template_notification",
                data={"user_name": "Alice"},  # Missing actor_name
            )

    def test_creates_notification_with_actor(
        self, db, notification_type, user, actor_user
    ):
        """Creates notification with actor user."""
        from notifications.services import NotificationService

        result = NotificationService.create_notification(
            recipient=user,
            type_key="test_notification",
            title="Actor Test",
            actor=actor_user,
        )

        assert result.success
        notification = result.data
        assert notification.actor == actor_user

    def test_creates_notification_with_source_object_gfk(
        self, db, notification_type, user
    ):
        """Creates notification with source object via GenericForeignKey."""
        from notifications.services import NotificationService

        # Use the user model as a sample source object
        result = NotificationService.create_notification(
            recipient=user,
            type_key="test_notification",
            title="GFK Test",
            source_object=user,
        )

        assert result.success
        notification = result.data
        assert notification.source_object == user
        assert notification.content_type == ContentType.objects.get_for_model(user)
        assert notification.object_id == str(user.pk)

    def test_stores_data_json(self, db, notification_type, user):
        """Stores arbitrary JSON data in notification."""
        from notifications.services import NotificationService

        data = {
            "deep_link": "/posts/123",
            "badge_count": 5,
            "nested": {"key": "value"},
        }

        result = NotificationService.create_notification(
            recipient=user,
            type_key="test_notification",
            title="Data Test",
            data=data,
        )

        assert result.success
        notification = result.data
        assert notification.data == data

    def test_enqueues_celery_tasks_for_supported_channels(
        self, db, notification_type, user, mocker
    ):
        """Enqueues Celery tasks for channels the type supports."""
        from notifications.services import NotificationService

        # notification_type has supports_push=True, supports_websocket=True
        mock_push = mocker.patch("notifications.tasks.send_push_notification.delay")
        mock_ws = mocker.patch(
            "notifications.tasks.broadcast_websocket_notification.delay"
        )

        result = NotificationService.create_notification(
            recipient=user,
            type_key="test_notification",
            title="Task Test",
        )

        assert result.success
        notification = result.data

        # Verify tasks were enqueued
        mock_push.assert_called_once_with(notification.id)
        mock_ws.assert_called_once_with(notification.id)

    def test_skips_tasks_for_unsupported_channels(
        self, db, push_only_notification_type, user, mocker
    ):
        """Does not enqueue tasks for unsupported channels."""
        from notifications.services import NotificationService

        # push_only_notification_type has supports_email=False, supports_websocket=False
        mock_push = mocker.patch("notifications.tasks.send_push_notification.delay")
        mock_email = mocker.patch("notifications.tasks.send_email_notification.delay")
        mock_ws = mocker.patch(
            "notifications.tasks.broadcast_websocket_notification.delay"
        )

        result = NotificationService.create_notification(
            recipient=user,
            type_key="push_only",
            title="Push Only Test",
        )

        assert result.success

        # Only push should be called
        mock_push.assert_called_once()
        mock_email.assert_not_called()
        mock_ws.assert_not_called()

    def test_enqueues_email_task_when_supported(self, db, user, mocker):
        """Enqueues email task when notification type supports email."""
        from notifications.models import NotificationType
        from notifications.services import NotificationService

        # Create a type that supports email
        NotificationType.objects.create(
            key="email_type",
            display_name="Email Type",
            supports_push=False,
            supports_email=True,
            supports_websocket=False,
        )

        mock_email = mocker.patch("notifications.tasks.send_email_notification.delay")

        result = NotificationService.create_notification(
            recipient=user,
            type_key="email_type",
            title="Email Test",
        )

        assert result.success
        mock_email.assert_called_once()


class TestNotificationServiceMarkAsRead:
    """
    Tests for NotificationService.mark_as_read().

    Verifies:
    - Marking notification as read
    - Idempotency
    - User ownership validation
    """

    def test_marks_notification_as_read(self, db, unread_notification, user):
        """Successfully marks notification as read."""
        from notifications.services import NotificationService

        result = NotificationService.mark_as_read(
            notification=unread_notification,
            user=user,
        )

        assert result.success
        unread_notification.refresh_from_db()
        assert unread_notification.is_read is True

    def test_idempotent_already_read(self, db, read_notification, user):
        """Marking already-read notification succeeds (idempotent)."""
        from notifications.services import NotificationService

        result = NotificationService.mark_as_read(
            notification=read_notification,
            user=user,
        )

        assert result.success
        read_notification.refresh_from_db()
        assert read_notification.is_read is True

    def test_wrong_user_returns_failure(self, db, notification, other_user):
        """Returns failure when user doesn't own notification."""
        from notifications.services import NotificationService

        result = NotificationService.mark_as_read(
            notification=notification,
            user=other_user,
        )

        assert not result.success
        assert result.error_code == "NOT_OWNER"


class TestNotificationServiceMarkAllAsRead:
    """
    Tests for NotificationService.mark_all_as_read().

    Verifies:
    - Bulk update of unread notifications
    - Return of marked count
    - Single database query performance
    - User isolation
    """

    def test_marks_all_unread_as_read(self, db, multiple_unread_notifications, user):
        """Marks all user's unread notifications as read."""
        from notifications.models import Notification
        from notifications.services import NotificationService

        result = NotificationService.mark_all_as_read(user=user)

        assert result.success

        # Verify all are now read
        unread_count = Notification.objects.filter(
            recipient=user, is_read=False
        ).count()
        assert unread_count == 0

    def test_returns_count_of_marked(self, db, multiple_unread_notifications, user):
        """Returns count of notifications marked as read."""
        from notifications.services import NotificationService

        # Should have 5 unread notifications from fixture
        result = NotificationService.mark_all_as_read(user=user)

        assert result.success
        assert result.data == 5

    def test_single_database_query(
        self, db, multiple_unread_notifications, user, django_assert_num_queries
    ):
        """Marks all as read in a single database query."""
        from notifications.services import NotificationService

        # Should be exactly 1 UPDATE query
        with django_assert_num_queries(1):
            NotificationService.mark_all_as_read(user=user)

    def test_does_not_affect_other_users(
        self, db, multiple_unread_notifications, other_user_notifications, user
    ):
        """Only marks current user's notifications."""
        from notifications.models import Notification
        from notifications.services import NotificationService

        result = NotificationService.mark_all_as_read(user=user)

        assert result.success

        # Other user's notifications should still be unread
        other_unread = Notification.objects.filter(
            recipient__in=[n.recipient for n in other_user_notifications],
            is_read=False,
        ).count()
        assert other_unread == 3  # All 3 from other_user_notifications fixture

    def test_returns_zero_when_no_unread(self, db, user):
        """Returns 0 when user has no unread notifications."""
        from notifications.services import NotificationService

        result = NotificationService.mark_all_as_read(user=user)

        assert result.success
        assert result.data == 0

    def test_preserves_already_read_notifications(self, db, mixed_notifications, user):
        """Already-read notifications remain unchanged."""
        from notifications.models import Notification
        from notifications.services import NotificationService

        result = NotificationService.mark_all_as_read(user=user)

        assert result.success
        # Should mark 3 unread notifications (from mixed_notifications fixture)
        assert result.data == 3

        # Total should be 5 (3 were unread, 2 were already read)
        total = Notification.objects.filter(recipient=user).count()
        assert total == 5
