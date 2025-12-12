"""
Unit tests for notification models.

Tests focus on model behavior and properties following TDD principles.
These tests define the expected behavior before implementation.

Test Classes:
    TestNotificationType: Tests for NotificationType model
    TestNotification: Tests for Notification model
"""

import pytest
from django.db import IntegrityError
from django.db.models import ProtectedError

from authentication.tests.factories import UserFactory


class TestNotificationType:
    """
    Tests for NotificationType model.

    Verifies:
    - Field constraints (unique key)
    - Default values (is_active, channel flags)
    - String representation
    """

    def test_creates_notification_type_with_all_fields(self, db):
        """Successfully creates a NotificationType with all fields."""
        from notifications.models import NotificationType

        nt = NotificationType.objects.create(
            key="test_type",
            display_name="Test Type",
            title_template="Hello {name}",
            body_template="Body for {name}",
            is_active=True,
            supports_push=True,
            supports_email=True,
            supports_websocket=True,
        )

        assert nt.pk is not None
        assert nt.key == "test_type"
        assert nt.display_name == "Test Type"
        assert nt.title_template == "Hello {name}"
        assert nt.body_template == "Body for {name}"
        assert nt.is_active is True
        assert nt.supports_push is True
        assert nt.supports_email is True
        assert nt.supports_websocket is True

    def test_key_must_be_unique(self, db):
        """Key field enforces uniqueness constraint."""
        from notifications.models import NotificationType

        NotificationType.objects.create(
            key="unique_key",
            display_name="First",
        )

        with pytest.raises(IntegrityError):
            NotificationType.objects.create(
                key="unique_key",
                display_name="Second",
            )

    def test_active_flag_default_true(self, db):
        """New notification types are active by default."""
        from notifications.models import NotificationType

        nt = NotificationType.objects.create(
            key="default_active",
            display_name="Default Active",
        )

        assert nt.is_active is True

    def test_supports_push_default_true(self, db):
        """New notification types support push by default."""
        from notifications.models import NotificationType

        nt = NotificationType.objects.create(
            key="default_push",
            display_name="Default Push",
        )

        assert nt.supports_push is True

    def test_supports_email_default_false(self, db):
        """New notification types do not support email by default."""
        from notifications.models import NotificationType

        nt = NotificationType.objects.create(
            key="default_email",
            display_name="Default Email",
        )

        assert nt.supports_email is False

    def test_supports_websocket_default_true(self, db):
        """New notification types support websocket by default."""
        from notifications.models import NotificationType

        nt = NotificationType.objects.create(
            key="default_websocket",
            display_name="Default WebSocket",
        )

        assert nt.supports_websocket is True

    def test_title_template_can_be_blank(self, db):
        """Title template field allows blank values."""
        from notifications.models import NotificationType

        nt = NotificationType.objects.create(
            key="no_title_template",
            display_name="No Title Template",
            title_template="",
        )

        assert nt.title_template == ""

    def test_body_template_can_be_blank(self, db):
        """Body template field allows blank values."""
        from notifications.models import NotificationType

        nt = NotificationType.objects.create(
            key="no_body_template",
            display_name="No Body Template",
            body_template="",
        )

        assert nt.body_template == ""

    def test_str_representation(self, db):
        """String representation shows display_name and key."""
        from notifications.models import NotificationType

        nt = NotificationType.objects.create(
            key="my_key",
            display_name="My Display Name",
        )

        assert str(nt) == "My Display Name (my_key)"


class TestNotification:
    """
    Tests for Notification model.

    Verifies:
    - Required fields and relationships
    - Default values (is_read)
    - Ordering (newest first)
    - Deletion behaviors (CASCADE, SET_NULL, PROTECT)
    - JSONField data storage
    - Generic foreign key for source objects
    """

    def test_creates_notification_with_required_fields(
        self, db, notification_type, user
    ):
        """Successfully creates a Notification with required fields."""
        from notifications.models import Notification

        notification = Notification.objects.create(
            notification_type=notification_type,
            recipient=user,
            title="Test Title",
        )

        assert notification.pk is not None
        assert notification.notification_type == notification_type
        assert notification.recipient == user
        assert notification.title == "Test Title"
        assert notification.is_read is False
        assert notification.created_at is not None
        assert notification.updated_at is not None

    def test_is_read_default_false(self, db, notification_type, user):
        """New notifications are unread by default."""
        from notifications.models import Notification

        notification = Notification.objects.create(
            notification_type=notification_type,
            recipient=user,
            title="Test",
        )

        assert notification.is_read is False

    def test_body_can_be_blank(self, db, notification_type, user):
        """Body field allows blank values."""
        from notifications.models import Notification

        notification = Notification.objects.create(
            notification_type=notification_type,
            recipient=user,
            title="Title Only",
            body="",
        )

        assert notification.body == ""

    def test_actor_can_be_null(self, db, notification_type, user):
        """Actor field is optional (system notifications)."""
        from notifications.models import Notification

        notification = Notification.objects.create(
            notification_type=notification_type,
            recipient=user,
            title="System Notification",
            actor=None,
        )

        assert notification.actor is None

    def test_data_json_field_storage(self, db, notification_type, user):
        """Data field stores and retrieves JSON correctly."""
        from notifications.models import Notification

        data = {
            "deep_link": "/posts/123",
            "badge_count": 5,
            "nested": {"key": "value"},
        }

        notification = Notification.objects.create(
            notification_type=notification_type,
            recipient=user,
            title="With Data",
            data=data,
        )

        notification.refresh_from_db()
        assert notification.data == data
        assert notification.data["deep_link"] == "/posts/123"
        assert notification.data["nested"]["key"] == "value"

    def test_data_default_empty_dict(self, db, notification_type, user):
        """Data field defaults to empty dict."""
        from notifications.models import Notification

        notification = Notification.objects.create(
            notification_type=notification_type,
            recipient=user,
            title="No Data",
        )

        assert notification.data == {}

    def test_default_ordering_by_created_at_desc(self, db, notification_type, user):
        """Notifications are ordered by -created_at by default (newest first)."""
        from datetime import timedelta

        from django.utils import timezone

        from notifications.models import Notification

        base_time = timezone.now()

        # Create notifications with explicit different timestamps
        n1 = Notification.objects.create(
            notification_type=notification_type,
            recipient=user,
            title="First",
        )
        Notification.objects.filter(pk=n1.pk).update(
            created_at=base_time - timedelta(hours=2)
        )

        n2 = Notification.objects.create(
            notification_type=notification_type,
            recipient=user,
            title="Second",
        )
        Notification.objects.filter(pk=n2.pk).update(
            created_at=base_time - timedelta(hours=1)
        )

        n3 = Notification.objects.create(
            notification_type=notification_type,
            recipient=user,
            title="Third",
        )
        Notification.objects.filter(pk=n3.pk).update(created_at=base_time)

        # Query should return newest first (n3 -> n2 -> n1)
        # Filter by recipient to avoid interference from other tests
        notifications = list(Notification.objects.filter(recipient=user))
        assert len(notifications) == 3
        assert notifications[0].title == "Third"  # Most recent (base_time)
        assert notifications[1].title == "Second"  # base_time - 1 hour
        assert notifications[2].title == "First"  # Oldest (base_time - 2 hours)

    def test_cascade_delete_on_recipient_delete(self, db, notification_type):
        """Notifications are deleted when recipient is deleted."""
        from notifications.models import Notification

        user = UserFactory(email_verified=True)
        Notification.objects.create(
            notification_type=notification_type,
            recipient=user,
            title="Test",
        )

        assert Notification.objects.count() == 1
        user.delete()
        assert Notification.objects.count() == 0

    def test_protect_on_notification_type_delete(self, db, notification_type, user):
        """Cannot delete NotificationType with existing notifications."""
        from notifications.models import Notification

        Notification.objects.create(
            notification_type=notification_type,
            recipient=user,
            title="Test",
        )

        with pytest.raises(ProtectedError):
            notification_type.delete()

    def test_actor_set_null_on_delete(self, db, notification_type, user):
        """Actor is set to NULL when actor user is deleted (SET_NULL)."""
        from notifications.models import Notification

        actor = UserFactory(email_verified=True)
        notification = Notification.objects.create(
            notification_type=notification_type,
            recipient=user,
            actor=actor,
            title="Test",
        )

        actor.delete()
        notification.refresh_from_db()

        assert notification.actor is None

    def test_source_object_gfk_optional(self, db, notification_type, user):
        """Source object (GFK) fields are optional."""
        from notifications.models import Notification

        notification = Notification.objects.create(
            notification_type=notification_type,
            recipient=user,
            title="No Source",
            content_type=None,
            object_id=None,
        )

        assert notification.content_type is None
        assert notification.object_id is None
        assert notification.source_object is None

    def test_source_object_gfk_reference(self, db, notification_type, user):
        """GFK correctly references source object."""
        from django.contrib.contenttypes.models import ContentType
        from notifications.models import Notification

        # Use the user model as a sample source object
        content_type = ContentType.objects.get_for_model(user)

        notification = Notification.objects.create(
            notification_type=notification_type,
            recipient=user,
            title="With Source",
            content_type=content_type,
            object_id=user.pk,
        )

        assert notification.source_object == user

    def test_source_object_null_when_deleted(self, db, notification_type, user):
        """GFK returns None when source object is deleted."""
        from django.contrib.contenttypes.models import ContentType
        from notifications.models import Notification

        # Create a user to be the source object
        source_user = UserFactory(email_verified=True)
        content_type = ContentType.objects.get_for_model(source_user)
        source_pk = source_user.pk

        notification = Notification.objects.create(
            notification_type=notification_type,
            recipient=user,
            title="With Source",
            content_type=content_type,
            object_id=source_pk,
        )

        # Verify GFK works before deletion
        assert notification.source_object == source_user

        # Delete source object
        source_user.delete()
        notification.refresh_from_db()

        # GFK returns None when object doesn't exist
        # (content_type and object_id are still set, but source_object is None)
        assert notification.content_type == content_type
        assert notification.object_id == source_pk
        assert notification.source_object is None

    def test_notification_str_representation(self, db, notification_type, user):
        """String representation shows type key, recipient, and read status."""
        from notifications.models import Notification

        notification = Notification.objects.create(
            notification_type=notification_type,
            recipient=user,
            title="Test",
            is_read=False,
        )

        str_repr = str(notification)
        assert notification_type.key in str_repr
        assert str(user.pk) in str_repr
        assert "unread" in str_repr

    def test_notification_str_representation_read(self, db, notification_type, user):
        """String representation shows 'read' status when notification is read."""
        from notifications.models import Notification

        notification = Notification.objects.create(
            notification_type=notification_type,
            recipient=user,
            title="Test",
            is_read=True,
        )

        str_repr = str(notification)
        assert "read" in str_repr
