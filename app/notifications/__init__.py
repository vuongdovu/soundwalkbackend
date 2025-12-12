"""
Notifications app for in-app and external notification delivery.

This app provides:
- NotificationType model for configuring notification templates and channels
- Notification model for storing user notifications
- NotificationService for centralized notification creation
- Celery tasks for async delivery (push, email, WebSocket)
- REST API for listing and managing notifications

Usage:
    from notifications.services import NotificationService

    # Create a notification
    result = NotificationService.create_notification(
        recipient=user,
        type_key="new_message",
        data={"actor_name": sender.get_full_name(), "message_preview": "..."},
        actor=sender,
    )

    if result.success:
        notification = result.data
"""
