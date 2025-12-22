"""
Factory Boy factories for notification models.

Provides realistic test data generation for:
- NotificationType: Notification type definitions with templates
- Notification: Individual user notifications
- UserGlobalPreference: Global notification preferences
- UserCategoryPreference: Category-level preferences
- UserNotificationPreference: Type-level preferences
- NotificationDelivery: Delivery tracking records

Usage:
    from notifications.tests.factories import (
        NotificationTypeFactory,
        NotificationFactory,
        NotificationDeliveryFactory,
    )

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

    # Create a delivery record
    delivery = NotificationDeliveryFactory(
        notification=notification,
        channel="push",
        status="pending",
    )
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

        # Social notification type
        nt = NotificationTypeFactory(category="social")
    """

    class Meta:
        model = "notifications.NotificationType"

    key = factory.Sequence(lambda n: f"notification_type_{n}")
    display_name = factory.LazyAttribute(lambda obj: obj.key.replace("_", " ").title())
    category = "transactional"  # Default category
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


class UserGlobalPreferenceFactory(factory.django.DjangoModelFactory):
    """
    Factory for UserGlobalPreference model.

    Creates global notification preferences for users.
    By default, notifications are enabled (all_disabled=False).

    Examples:
        # Enable all notifications (default)
        pref = UserGlobalPreferenceFactory(user=user)

        # Disable all notifications
        pref = UserGlobalPreferenceFactory(user=user, all_disabled=True)
    """

    class Meta:
        model = "notifications.UserGlobalPreference"

    user = factory.SubFactory(UserFactory, email_verified=True)
    all_disabled = False


class UserCategoryPreferenceFactory(factory.django.DjangoModelFactory):
    """
    Factory for UserCategoryPreference model.

    Creates category-level notification preferences.
    By default, the category is enabled (disabled=False).

    Examples:
        # Enable social notifications
        pref = UserCategoryPreferenceFactory(
            user=user,
            category="social",
        )

        # Disable marketing notifications
        pref = UserCategoryPreferenceFactory(
            user=user,
            category="marketing",
            disabled=True,
        )
    """

    class Meta:
        model = "notifications.UserCategoryPreference"

    user = factory.SubFactory(UserFactory, email_verified=True)
    category = "social"
    disabled = False


class UserNotificationPreferenceFactory(factory.django.DjangoModelFactory):
    """
    Factory for UserNotificationPreference model.

    Creates type-level notification preferences with channel overrides.
    By default, the type is enabled with no channel overrides.

    Examples:
        # Basic preference
        pref = UserNotificationPreferenceFactory(
            user=user,
            notification_type=notification_type,
        )

        # Disable push for this type
        pref = UserNotificationPreferenceFactory(
            user=user,
            notification_type=notification_type,
            push_enabled=False,
        )

        # Disable the entire type
        pref = UserNotificationPreferenceFactory(
            user=user,
            notification_type=notification_type,
            disabled=True,
        )
    """

    class Meta:
        model = "notifications.UserNotificationPreference"

    user = factory.SubFactory(UserFactory, email_verified=True)
    notification_type = factory.SubFactory(NotificationTypeFactory)
    disabled = False
    push_enabled = None  # Inherit from type
    email_enabled = None  # Inherit from type
    websocket_enabled = None  # Inherit from type


class NotificationDeliveryFactory(factory.django.DjangoModelFactory):
    """
    Factory for NotificationDelivery model.

    Creates delivery tracking records for notifications.
    By default creates a PENDING push delivery.

    Examples:
        # Pending push delivery
        delivery = NotificationDeliveryFactory(notification=notification)

        # Sent email delivery
        delivery = NotificationDeliveryFactory(
            notification=notification,
            channel="email",
            status="sent",
            provider_message_id="msg-123",
        )

        # Failed delivery
        delivery = NotificationDeliveryFactory(
            notification=notification,
            status="failed",
            failure_code="unregistered",
            failure_reason="Token expired",
            is_permanent_failure=True,
        )
    """

    class Meta:
        model = "notifications.NotificationDelivery"

    notification = factory.SubFactory(NotificationFactory)
    channel = "push"
    status = "pending"
    sent_at = None
    delivered_at = None
    failed_at = None
    provider_message_id = None
    failure_reason = ""
    failure_code = ""
    is_permanent_failure = False
    attempt_count = 0
    skipped_reason = ""
    websocket_devices_targeted = 0
    websocket_devices_reached = 0
