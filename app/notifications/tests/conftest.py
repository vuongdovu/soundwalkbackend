"""
Test configuration and fixtures for notification tests.

This module provides:
- Reusable fixtures for common test scenarios
- NotificationType fixtures (active/inactive, with/without templates)
- Notification fixtures (read/unread, with/without actor)
- API client helpers for authenticated requests

Usage:
    def test_example(user, unread_notification, authenticated_client):
        response = authenticated_client.get('/api/v1/notifications/')
        assert response.status_code == 200
"""

import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from authentication.tests.factories import UserFactory


# =============================================================================
# User Fixtures
# =============================================================================


@pytest.fixture
def user(db):
    """Create a basic verified user to receive notifications."""
    return UserFactory(email_verified=True)


@pytest.fixture
def other_user(db):
    """Create another verified user for multi-user tests."""
    return UserFactory(email_verified=True)


@pytest.fixture
def actor_user(db):
    """Create a user to act as notification actor (trigger)."""
    return UserFactory(email_verified=True)


# =============================================================================
# NotificationType Fixtures
# =============================================================================


@pytest.fixture
def notification_type(db):
    """
    Create an active notification type with simple templates.

    Default type supports push and websocket, not email.
    """
    from notifications.models import NotificationType

    return NotificationType.objects.create(
        key="test_notification",
        display_name="Test Notification",
        title_template="Test Title",
        body_template="Test Body",
        is_active=True,
        supports_push=True,
        supports_email=False,
        supports_websocket=True,
    )


@pytest.fixture
def notification_type_with_placeholders(db):
    """
    Create notification type with template placeholders.

    Templates use {user_name} and {actor_name} placeholders.
    """
    from notifications.models import NotificationType

    return NotificationType.objects.create(
        key="template_notification",
        display_name="Template Notification",
        title_template="Hello, {user_name}!",
        body_template="You have a new notification from {actor_name}.",
        is_active=True,
        supports_push=True,
        supports_email=True,
        supports_websocket=True,
    )


@pytest.fixture
def inactive_notification_type(db):
    """Create an inactive (deactivated) notification type."""
    from notifications.models import NotificationType

    return NotificationType.objects.create(
        key="inactive_notification",
        display_name="Inactive Notification",
        title_template="",
        body_template="",
        is_active=False,
        supports_push=True,
        supports_email=False,
        supports_websocket=True,
    )


@pytest.fixture
def notification_type_no_templates(db):
    """Create a notification type without templates (requires explicit title/body)."""
    from notifications.models import NotificationType

    return NotificationType.objects.create(
        key="no_template_notification",
        display_name="No Template",
        title_template="",
        body_template="",
        is_active=True,
        supports_push=True,
        supports_email=False,
        supports_websocket=False,
    )


@pytest.fixture
def push_only_notification_type(db):
    """Create a notification type that only supports push."""
    from notifications.models import NotificationType

    return NotificationType.objects.create(
        key="push_only",
        display_name="Push Only",
        title_template="Push notification",
        body_template="",
        is_active=True,
        supports_push=True,
        supports_email=False,
        supports_websocket=False,
    )


@pytest.fixture
def multiple_notification_types(db):
    """Create multiple notification types for filtering tests."""
    from notifications.models import NotificationType

    types = []
    for key in ["type_a", "type_b", "type_c"]:
        types.append(
            NotificationType.objects.create(
                key=key,
                display_name=key.replace("_", " ").title(),
                title_template=f"{key} title",
                body_template=f"{key} body",
                is_active=True,
            )
        )
    return types


# =============================================================================
# Notification Fixtures
# =============================================================================


@pytest.fixture
def notification(db, user, notification_type):
    """Create a basic unread notification for the user."""
    from notifications.models import Notification

    return Notification.objects.create(
        notification_type=notification_type,
        recipient=user,
        title="Test Notification",
        body="This is a test notification.",
        is_read=False,
    )


@pytest.fixture
def unread_notification(db, user, notification_type):
    """Create an unread notification."""
    from notifications.models import Notification

    return Notification.objects.create(
        notification_type=notification_type,
        recipient=user,
        title="Unread Notification",
        body="This notification has not been read.",
        is_read=False,
    )


@pytest.fixture
def read_notification(db, user, notification_type):
    """Create a read notification."""
    from notifications.models import Notification

    return Notification.objects.create(
        notification_type=notification_type,
        recipient=user,
        title="Read Notification",
        body="This notification has been read.",
        is_read=True,
    )


@pytest.fixture
def notification_with_actor(db, user, actor_user, notification_type):
    """Create notification with an actor (triggering user)."""
    from notifications.models import Notification

    return Notification.objects.create(
        notification_type=notification_type,
        recipient=user,
        actor=actor_user,
        title="Notification with Actor",
        body="Actor-triggered notification.",
        is_read=False,
    )


@pytest.fixture
def notification_without_actor(db, user, notification_type):
    """Create notification without actor (system notification)."""
    from notifications.models import Notification

    return Notification.objects.create(
        notification_type=notification_type,
        recipient=user,
        actor=None,
        title="System Notification",
        body="System-generated notification.",
        is_read=False,
    )


@pytest.fixture
def notification_with_data(db, user, notification_type):
    """Create notification with JSON data for deep linking."""
    from notifications.models import Notification

    return Notification.objects.create(
        notification_type=notification_type,
        recipient=user,
        title="Notification with Data",
        body="Contains extra data.",
        data={
            "deep_link": "/posts/123",
            "badge_count": 5,
            "custom_field": "value",
        },
        is_read=False,
    )


@pytest.fixture
def multiple_unread_notifications(db, user, notification_type):
    """Create multiple unread notifications for the user."""
    from notifications.models import Notification

    notifications = []
    for i in range(5):
        notifications.append(
            Notification.objects.create(
                notification_type=notification_type,
                recipient=user,
                title=f"Unread Notification {i + 1}",
                body=f"Body of notification {i + 1}",
                is_read=False,
            )
        )
    return notifications


@pytest.fixture
def mixed_notifications(db, user, notification_type):
    """
    Create a mix of read and unread notifications.

    Returns dict with 'unread', 'read', and 'all' keys.
    """
    from notifications.models import Notification

    unread = []
    read = []

    for i in range(3):
        unread.append(
            Notification.objects.create(
                notification_type=notification_type,
                recipient=user,
                title=f"Unread {i + 1}",
                body="Unread notification",
                is_read=False,
            )
        )

    for i in range(2):
        read.append(
            Notification.objects.create(
                notification_type=notification_type,
                recipient=user,
                title=f"Read {i + 1}",
                body="Read notification",
                is_read=True,
            )
        )

    return {"unread": unread, "read": read, "all": unread + read}


@pytest.fixture
def other_user_notifications(db, other_user, notification_type):
    """Create notifications for another user (for scoping tests)."""
    from notifications.models import Notification

    notifications = []
    for i in range(3):
        notifications.append(
            Notification.objects.create(
                notification_type=notification_type,
                recipient=other_user,
                title=f"Other User Notification {i + 1}",
                body="This belongs to another user.",
                is_read=False,
            )
        )
    return notifications


@pytest.fixture
def many_notifications(db, user, notification_type):
    """Create many notifications for pagination testing (50 notifications)."""
    from notifications.models import Notification

    notifications = []
    for i in range(50):
        notifications.append(
            Notification.objects.create(
                notification_type=notification_type,
                recipient=user,
                title=f"Notification {i + 1}",
                body=f"Body {i + 1}",
                is_read=False,
            )
        )
    return notifications


@pytest.fixture
def notifications_of_different_types(db, user, multiple_notification_types):
    """Create notifications of different types for filtering tests."""
    from notifications.models import Notification

    notifications = []
    for nt in multiple_notification_types:
        notifications.append(
            Notification.objects.create(
                notification_type=nt,
                recipient=user,
                title=f"{nt.key} notification",
                body=f"Notification of type {nt.key}",
                is_read=False,
            )
        )
    return notifications


# =============================================================================
# API Client Fixtures
# =============================================================================


@pytest.fixture
def api_client():
    """Unauthenticated API client for public endpoints."""
    return APIClient()


@pytest.fixture
def authenticated_client(user):
    """API client authenticated with JWT token for the default user fixture."""
    client = APIClient()
    refresh = RefreshToken.for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


@pytest.fixture
def authenticated_client_factory(db):
    """
    Factory to create authenticated clients for any user.

    Usage:
        def test_example(authenticated_client_factory, some_user):
            client = authenticated_client_factory(some_user)
            response = client.get('/api/v1/notifications/')
    """

    def _make_client(user):
        client = APIClient()
        refresh = RefreshToken.for_user(user)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
        return client

    return _make_client


@pytest.fixture
def other_user_client(authenticated_client_factory, other_user):
    """API client authenticated as the other user."""
    return authenticated_client_factory(other_user)


# =============================================================================
# Template Data Fixtures
# =============================================================================


@pytest.fixture
def valid_template_data():
    """Valid data for template rendering with all placeholders."""
    return {
        "user_name": "testuser",
        "actor_name": "John Doe",
    }


@pytest.fixture
def incomplete_template_data():
    """Data missing required placeholder (actor_name)."""
    return {
        "user_name": "testuser",
        # Missing 'actor_name' placeholder
    }


# =============================================================================
# Mock Fixtures
# =============================================================================


@pytest.fixture
def mock_notification_tasks(mocker):
    """
    Mock all notification Celery tasks to prevent actual task execution.

    Use this when testing services that trigger background tasks.
    """
    mocker.patch("notifications.tasks.send_push_notification.delay")
    mocker.patch("notifications.tasks.send_email_notification.delay")
    mocker.patch("notifications.tasks.broadcast_websocket_notification.delay")
    return mocker
