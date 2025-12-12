"""
Factory Boy factories for notification models.

Provides realistic test data generation for:
- NotificationType: Notification type definitions with templates
- Notification: Individual user notifications

Usage:
    from notifications.tests.factories import NotificationTypeFactory, NotificationFactory

    # Create a notification type with defaults
    notification_type = NotificationTypeFactory()

    # Create a notification type with custom template
    notification_type = NotificationTypeFactory(
        key="welcome",
        title_template="Welcome, {username}!",
    )

    # Create a notification for a user
    notification = NotificationFactory(recipient=user)

    # Create an unread notification
    notification = NotificationFactory(recipient=user, is_read=False)
"""

import factory

from authentication.tests.factories import UserFactory


class NotificationTypeFactory(factory.django.DjangoModelFactory):
    """
    Factory for NotificationType model.

    Creates notification type definitions with template strings.
    By default creates active types that support push and websocket.

    Examples:
        # Basic notification type
        nt = NotificationTypeFactory()

        # Type with template placeholders
        nt = NotificationTypeFactory(
            key="order_shipped",
            title_template="Your order {order_id} has shipped",
            body_template="Track your package: {tracking_url}",
        )

        # Inactive type (disabled)
        nt = NotificationTypeFactory(is_active=False)

        # Email-only type
        nt = NotificationTypeFactory(
            supports_push=False,
            supports_email=True,
            supports_websocket=False,
        )
    """

    class Meta:
        model = "notifications.NotificationType"

    key = factory.Sequence(lambda n: f"notification_type_{n}")
    display_name = factory.LazyAttribute(lambda obj: obj.key.replace("_", " ").title())
    title_template = factory.LazyAttribute(lambda obj: f"{obj.display_name} Title")
    body_template = factory.LazyAttribute(
        lambda obj: f"{obj.display_name} body message."
    )
    is_active = True
    supports_push = True
    supports_email = False
    supports_websocket = True


class NotificationFactory(factory.django.DjangoModelFactory):
    """
    Factory for Notification model.

    Creates notifications for users.
    By default creates unread notifications without actor.

    Examples:
        # Basic notification for a user
        notification = NotificationFactory(recipient=user)

        # Read notification
        notification = NotificationFactory(recipient=user, is_read=True)

        # Notification with actor (e.g., "John liked your post")
        notification = NotificationFactory(
            recipient=user,
            actor=other_user,
        )

        # Notification with custom content
        notification = NotificationFactory(
            recipient=user,
            title="Custom Title",
            body="Custom body text",
        )

        # Notification with JSON data
        notification = NotificationFactory(
            recipient=user,
            data={"deep_link": "/posts/123", "badge_count": 5},
        )
    """

    class Meta:
        model = "notifications.Notification"

    notification_type = factory.SubFactory(NotificationTypeFactory)
    recipient = factory.SubFactory(UserFactory, email_verified=True)
    actor = None  # System notification by default
    title = factory.Faker("sentence", nb_words=5)
    body = factory.Faker("paragraph", nb_sentences=2)
    data = factory.LazyFunction(lambda: {})
    content_type = None  # No GFK by default
    object_id = None
    is_read = False
