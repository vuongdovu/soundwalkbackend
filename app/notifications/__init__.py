"""
Notifications app for centralized notification delivery.

This app handles:
- In-app notifications
- Push notifications (FCM/APNs)
- Email notifications
- Device token management
- User notification preferences

Related apps:
    - authentication: User model for recipients
    - payments: Payment event notifications
    - chat: Message notifications

Usage:
    from notifications.services import NotificationService

    # Send single notification
    NotificationService.send(
        user=user,
        notification_type="system",
        title="Welcome!",
        body="Thanks for signing up.",
        channels=["in_app", "email"],
    )

    # Send bulk notifications
    NotificationService.send_bulk(
        users=User.objects.filter(is_active=True),
        notification_type="system",
        title="New Feature",
        body="Check out our new feature!",
    )
"""
