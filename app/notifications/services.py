"""
Notification service layer.

This module provides the business logic for the notification system,
encapsulating all operations for creating and managing notifications.

Services:
    NotificationService: Notification creation and read status management
    PreferenceService: User notification preference management

Design Principles:
    - Services are stateless (use class methods)
    - Expected failures return ServiceResult.failure()
    - Template rendering raises KeyError on missing placeholders
    - Celery tasks are enqueued based on user preferences and channel support
    - Delivery records track per-channel status for retry and analytics

Usage:
    from notifications.services import NotificationService, PreferenceService

    # Create a notification with template rendering
    result = NotificationService.create_notification(
        recipient=user,
        type_key="new_follower",
        data={"actor_name": "John Doe"},
        actor=john_doe,
    )

    # Create with explicit title/body (overrides template)
    result = NotificationService.create_notification(
        recipient=user,
        type_key="system_alert",
        title="Maintenance Notice",
        body="System will be down for maintenance.",
    )

    # Mark as read
    result = NotificationService.mark_as_read(notification, user)

    # Mark all as read
    result = NotificationService.mark_all_as_read(user)

    # Get user preferences
    result = PreferenceService.get_user_preferences(user)

    # Update global mute setting
    result = PreferenceService.set_global_preference(user, all_disabled=True)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from core.services import BaseService, ServiceResult

from notifications.models import (
    DeliveryChannel,
    DeliveryStatus,
    Notification,
    NotificationDelivery,
    NotificationType,
    SkipReason,
    UserCategoryPreference,
    UserGlobalPreference,
    UserNotificationPreference,
)
from notifications.preferences import PreferenceResolver

if TYPE_CHECKING:
    from django.db.models import Model

    from authentication.models import User

logger = logging.getLogger(__name__)


class NotificationService(BaseService):
    """
    Service for notification operations.

    Methods:
        create_notification: Create a new notification with template rendering
        mark_as_read: Mark a single notification as read
        mark_all_as_read: Mark all user's unread notifications as read
    """

    @classmethod
    def create_notification(
        cls,
        recipient: User,
        type_key: str,
        data: dict | None = None,
        title: str | None = None,
        body: str | None = None,
        actor: User | None = None,
        source_object: Model | None = None,
        idempotency_key: str | None = None,
    ) -> ServiceResult[Notification]:
        """
        Create a new notification for a user.

        If title/body are not provided, templates from NotificationType are
        rendered using the data dict. Explicit title/body override templates.

        Implementation:
            1. Idempotency check (if key provided)
            2. Look up NotificationType by key
            3. Validate type exists and is active
            4. Resolve user preferences
            5. Render templates (or use explicit values)
            6. Create notification with delivery records in transaction
            7. Enqueue Celery tasks for PENDING deliveries only

        Args:
            recipient: User receiving the notification
            type_key: NotificationType.key to look up
            data: Dict for template rendering (e.g., {"actor_name": "John"})
            title: Explicit title (overrides template)
            body: Explicit body (overrides template)
            actor: User who triggered the notification (optional)
            source_object: Object that triggered the notification (GFK)
            idempotency_key: Optional key to prevent duplicate notifications

        Returns:
            ServiceResult with created Notification if successful

        Error codes:
            TYPE_NOT_FOUND: Notification type key doesn't exist
            TYPE_INACTIVE: Notification type is deactivated
            DUPLICATE: Notification with this idempotency_key already exists

        Raises:
            KeyError: If template placeholder is missing from data

        Example:
            # With template
            result = NotificationService.create_notification(
                recipient=user,
                type_key="new_follower",
                data={"actor_name": follower.display_name},
                actor=follower,
            )

            # With explicit title/body and idempotency
            result = NotificationService.create_notification(
                recipient=user,
                type_key="system_alert",
                title="Server Maintenance",
                body="Scheduled maintenance at midnight.",
                idempotency_key="maintenance-2024-01-15",
            )
        """
        # Import tasks here to avoid circular imports
        from notifications import tasks

        data = data or {}

        # Look up notification type first (needed for idempotency check)
        try:
            notification_type = NotificationType.objects.get(key=type_key)
        except NotificationType.DoesNotExist:
            cls.get_logger().warning(f"Notification type not found: {type_key}")
            return ServiceResult.failure(
                f"Notification type not found: {type_key}",
                error_code="TYPE_NOT_FOUND",
            )

        # Check if type is active
        if not notification_type.is_active:
            cls.get_logger().info(
                f"Notification type inactive: {type_key} - skipping creation"
            )
            return ServiceResult.failure(
                f"Notification type is inactive: {type_key}",
                error_code="TYPE_INACTIVE",
            )

        # Idempotency check with SELECT FOR UPDATE to prevent races
        if idempotency_key:
            with transaction.atomic():
                existing = (
                    Notification.objects.select_for_update()
                    .filter(
                        idempotency_key=idempotency_key,
                    )
                    .first()
                )
                if existing:
                    cls.get_logger().info(
                        f"Duplicate notification prevented: idempotency_key={idempotency_key}"
                    )
                    return ServiceResult.failure(
                        f"Notification with idempotency_key already exists: {idempotency_key}",
                        error_code="DUPLICATE",
                    )

        # Resolve user preferences
        prefs = PreferenceResolver.resolve(recipient, notification_type)

        # If all channels are blocked, we still create the notification
        # but all deliveries will be SKIPPED
        if prefs.blocked:
            cls.get_logger().info(
                f"User {recipient.id} has blocked notifications: {prefs.blocked_reason}"
            )

        # Render templates or use explicit values
        # KeyError is raised if placeholder is missing - this is intentional
        rendered_title = title or notification_type.title_template.format(**data)
        rendered_body = body or notification_type.body_template.format(**data)

        # Prepare GFK fields if source_object provided
        content_type = None
        object_id = None
        if source_object is not None:
            content_type = ContentType.objects.get_for_model(source_object)
            # Convert PK to string to support both UUID and integer PKs
            object_id = str(source_object.pk)

        # Create notification and delivery records in transaction
        with transaction.atomic():
            notification = Notification.objects.create(
                notification_type=notification_type,
                recipient=recipient,
                actor=actor,
                title=rendered_title,
                body=rendered_body,
                data=data,
                content_type=content_type,
                object_id=object_id,
                idempotency_key=idempotency_key,
            )

            # Create delivery records for each supported channel
            deliveries = []

            # Push delivery
            if notification_type.supports_push:
                if prefs.blocked:
                    deliveries.append(
                        NotificationDelivery(
                            notification=notification,
                            channel=DeliveryChannel.PUSH,
                            status=DeliveryStatus.SKIPPED,
                            skipped_reason=prefs.blocked_reason
                            or SkipReason.GLOBAL_DISABLED,
                        )
                    )
                elif not prefs.push_enabled:
                    deliveries.append(
                        NotificationDelivery(
                            notification=notification,
                            channel=DeliveryChannel.PUSH,
                            status=DeliveryStatus.SKIPPED,
                            skipped_reason=SkipReason.CHANNEL_DISABLED,
                        )
                    )
                else:
                    deliveries.append(
                        NotificationDelivery(
                            notification=notification,
                            channel=DeliveryChannel.PUSH,
                            status=DeliveryStatus.PENDING,
                        )
                    )

            # Email delivery
            if notification_type.supports_email:
                if prefs.blocked:
                    deliveries.append(
                        NotificationDelivery(
                            notification=notification,
                            channel=DeliveryChannel.EMAIL,
                            status=DeliveryStatus.SKIPPED,
                            skipped_reason=prefs.blocked_reason
                            or SkipReason.GLOBAL_DISABLED,
                        )
                    )
                elif not prefs.email_enabled:
                    deliveries.append(
                        NotificationDelivery(
                            notification=notification,
                            channel=DeliveryChannel.EMAIL,
                            status=DeliveryStatus.SKIPPED,
                            skipped_reason=SkipReason.CHANNEL_DISABLED,
                        )
                    )
                else:
                    deliveries.append(
                        NotificationDelivery(
                            notification=notification,
                            channel=DeliveryChannel.EMAIL,
                            status=DeliveryStatus.PENDING,
                        )
                    )

            # WebSocket delivery
            if notification_type.supports_websocket:
                if prefs.blocked:
                    deliveries.append(
                        NotificationDelivery(
                            notification=notification,
                            channel=DeliveryChannel.WEBSOCKET,
                            status=DeliveryStatus.SKIPPED,
                            skipped_reason=prefs.blocked_reason
                            or SkipReason.GLOBAL_DISABLED,
                        )
                    )
                elif not prefs.websocket_enabled:
                    deliveries.append(
                        NotificationDelivery(
                            notification=notification,
                            channel=DeliveryChannel.WEBSOCKET,
                            status=DeliveryStatus.SKIPPED,
                            skipped_reason=SkipReason.CHANNEL_DISABLED,
                        )
                    )
                else:
                    deliveries.append(
                        NotificationDelivery(
                            notification=notification,
                            channel=DeliveryChannel.WEBSOCKET,
                            status=DeliveryStatus.PENDING,
                        )
                    )

            # Bulk create delivery records
            if deliveries:
                NotificationDelivery.objects.bulk_create(deliveries)

        cls.get_logger().info(
            f"Created notification {notification.id} of type {type_key} "
            f"for user {recipient.id} with {len(deliveries)} delivery records"
        )

        # Enqueue tasks only for PENDING deliveries
        for delivery in deliveries:
            if delivery.status == DeliveryStatus.PENDING:
                if delivery.channel == DeliveryChannel.PUSH:
                    tasks.send_push_notification.delay(str(delivery.id))
                elif delivery.channel == DeliveryChannel.EMAIL:
                    tasks.send_email_notification.delay(str(delivery.id))
                elif delivery.channel == DeliveryChannel.WEBSOCKET:
                    tasks.broadcast_websocket_notification.delay(str(delivery.id))

        return ServiceResult.success(notification)

    @classmethod
    def mark_as_read(
        cls,
        notification: Notification,
        user: User,
    ) -> ServiceResult[Notification]:
        """
        Mark a single notification as read.

        Validates that the user owns the notification before marking.
        Operation is idempotent - marking an already-read notification succeeds.

        Args:
            notification: The notification to mark as read
            user: The user making the request (for ownership validation)

        Returns:
            ServiceResult with updated Notification if successful

        Error codes:
            NOT_OWNER: User doesn't own the notification
        """
        # Validate ownership
        if notification.recipient_id != user.id:
            cls.get_logger().warning(
                f"User {user.id} attempted to mark notification {notification.id} "
                f"owned by user {notification.recipient_id}"
            )
            return ServiceResult.failure(
                "Cannot mark notification you don't own",
                error_code="NOT_OWNER",
            )

        # Mark as read (idempotent)
        if not notification.is_read:
            notification.is_read = True
            notification.save(update_fields=["is_read", "updated_at"])
            cls.get_logger().debug(f"Marked notification {notification.id} as read")

        return ServiceResult.success(notification)

    @classmethod
    def mark_all_as_read(cls, user: User) -> ServiceResult[int]:
        """
        Mark all user's unread notifications as read.

        Performs a bulk update in a single database query for efficiency.

        Args:
            user: The user whose notifications to mark as read

        Returns:
            ServiceResult with count of notifications marked as read
        """
        count = Notification.objects.filter(
            recipient=user,
            is_read=False,
        ).update(is_read=True)

        cls.get_logger().info(
            f"Marked {count} notifications as read for user {user.id}"
        )

        return ServiceResult.success(count)


class PreferenceService(BaseService):
    """
    Service for user notification preference management.

    Methods:
        get_user_preferences: Get all preferences for API display
        set_global_preference: Update global mute setting
        set_category_preference: Update category preference
        set_type_preference: Update type-level preferences
        reset_preferences: Reset all preferences to defaults
    """

    @classmethod
    def get_user_preferences(cls, user: User) -> ServiceResult[dict]:
        """
        Get all notification preferences for a user.

        Returns a structured response suitable for API display, including:
        - Global mute status
        - Category preferences
        - Type preferences with channel overrides

        Args:
            user: The user to get preferences for

        Returns:
            ServiceResult with preferences dict:
            {
                "global": {"all_disabled": bool},
                "categories": [{"category": str, "disabled": bool}, ...],
                "types": [{"type_key": str, "disabled": bool,
                          "push_enabled": bool|None, ...}, ...]
            }
        """
        # Get global preference
        try:
            global_pref = UserGlobalPreference.objects.get(user=user)
            global_data = {"all_disabled": global_pref.all_disabled}
        except UserGlobalPreference.DoesNotExist:
            global_data = {"all_disabled": False}

        # Get category preferences
        category_prefs = UserCategoryPreference.objects.filter(user=user)
        categories_data = [
            {"category": pref.category, "disabled": pref.disabled}
            for pref in category_prefs
        ]

        # Get type preferences
        type_prefs = UserNotificationPreference.objects.filter(
            user=user
        ).select_related("notification_type")
        types_data = [
            {
                "type_key": pref.notification_type.key,
                "type_name": pref.notification_type.display_name,
                "disabled": pref.disabled,
                "push_enabled": pref.push_enabled,
                "email_enabled": pref.email_enabled,
                "websocket_enabled": pref.websocket_enabled,
            }
            for pref in type_prefs
        ]

        return ServiceResult.success(
            {
                "global": global_data,
                "categories": categories_data,
                "types": types_data,
            }
        )

    @classmethod
    def set_global_preference(
        cls,
        user: User,
        all_disabled: bool,
    ) -> ServiceResult[UserGlobalPreference]:
        """
        Update global mute setting for a user.

        When all_disabled is True, no notifications will be sent to this user
        regardless of category or type preferences.

        Args:
            user: The user to update
            all_disabled: Whether to disable all notifications

        Returns:
            ServiceResult with the updated UserGlobalPreference
        """
        pref, created = UserGlobalPreference.objects.update_or_create(
            user=user,
            defaults={"all_disabled": all_disabled},
        )

        # Invalidate cache for this user
        PreferenceResolver.invalidate_cache(user.id)

        action = "created" if created else "updated"
        cls.get_logger().info(
            f"Global preference {action} for user {user.id}: "
            f"all_disabled={all_disabled}"
        )

        return ServiceResult.success(pref)

    @classmethod
    def set_category_preference(
        cls,
        user: User,
        category: str,
        disabled: bool,
    ) -> ServiceResult[UserCategoryPreference]:
        """
        Update category preference for a user.

        When disabled is True, no notifications of this category will be sent
        to this user regardless of type preferences.

        Args:
            user: The user to update
            category: The notification category (must be valid NotificationCategory)
            disabled: Whether to disable this category

        Returns:
            ServiceResult with the updated UserCategoryPreference

        Error codes:
            INVALID_CATEGORY: Category is not a valid NotificationCategory
        """
        from notifications.models import NotificationCategory

        # Validate category
        valid_categories = [c.value for c in NotificationCategory]
        if category not in valid_categories:
            return ServiceResult.failure(
                f"Invalid category: {category}. Must be one of {valid_categories}",
                error_code="INVALID_CATEGORY",
            )

        pref, created = UserCategoryPreference.objects.update_or_create(
            user=user,
            category=category,
            defaults={"disabled": disabled},
        )

        # Invalidate cache for this user
        PreferenceResolver.invalidate_cache(user.id)

        action = "created" if created else "updated"
        cls.get_logger().info(
            f"Category preference {action} for user {user.id}: "
            f"category={category}, disabled={disabled}"
        )

        return ServiceResult.success(pref)

    @classmethod
    def set_type_preference(
        cls,
        user: User,
        type_key: str,
        disabled: bool | None = None,
        push_enabled: bool | None = None,
        email_enabled: bool | None = None,
        websocket_enabled: bool | None = None,
    ) -> ServiceResult[UserNotificationPreference]:
        """
        Update type-level preferences for a user.

        Allows enabling/disabling the entire type or individual channels.
        Channel values of None mean "inherit from type defaults".

        Args:
            user: The user to update
            type_key: The notification type key
            disabled: Whether to disable this type entirely
            push_enabled: Override for push channel (None = inherit)
            email_enabled: Override for email channel (None = inherit)
            websocket_enabled: Override for websocket channel (None = inherit)

        Returns:
            ServiceResult with the updated UserNotificationPreference

        Error codes:
            TYPE_NOT_FOUND: Notification type key doesn't exist
        """
        # Look up notification type
        try:
            notification_type = NotificationType.objects.get(key=type_key)
        except NotificationType.DoesNotExist:
            return ServiceResult.failure(
                f"Notification type not found: {type_key}",
                error_code="TYPE_NOT_FOUND",
            )

        # Build defaults dict with only provided values
        defaults = {}
        if disabled is not None:
            defaults["disabled"] = disabled
        if push_enabled is not None:
            defaults["push_enabled"] = push_enabled
        if email_enabled is not None:
            defaults["email_enabled"] = email_enabled
        if websocket_enabled is not None:
            defaults["websocket_enabled"] = websocket_enabled

        # If no defaults provided, just get or create with default values
        if defaults:
            pref, created = UserNotificationPreference.objects.update_or_create(
                user=user,
                notification_type=notification_type,
                defaults=defaults,
            )
        else:
            pref, created = UserNotificationPreference.objects.get_or_create(
                user=user,
                notification_type=notification_type,
            )

        # Invalidate cache for this specific type
        PreferenceResolver.invalidate_cache(user.id, notification_type.id)

        action = "created" if created else "updated"
        cls.get_logger().info(
            f"Type preference {action} for user {user.id}: "
            f"type={type_key}, disabled={pref.disabled}"
        )

        return ServiceResult.success(pref)

    @classmethod
    def reset_preferences(cls, user: User) -> ServiceResult[int]:
        """
        Reset all user preferences to defaults.

        Deletes all preference records, effectively returning to default behavior
        (all notifications enabled, inheriting from type settings).

        Args:
            user: The user whose preferences to reset

        Returns:
            ServiceResult with count of deleted preference records
        """
        with transaction.atomic():
            global_count, _ = UserGlobalPreference.objects.filter(user=user).delete()
            category_count, _ = UserCategoryPreference.objects.filter(
                user=user
            ).delete()
            type_count, _ = UserNotificationPreference.objects.filter(
                user=user
            ).delete()

        total = global_count + category_count + type_count

        # Invalidate cache for this user
        PreferenceResolver.invalidate_cache(user.id)

        cls.get_logger().info(
            f"Reset {total} preferences for user {user.id}: "
            f"global={global_count}, category={category_count}, type={type_count}"
        )

        return ServiceResult.success(total)
