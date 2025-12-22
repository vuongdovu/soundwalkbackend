"""
Tests for notification delivery tracking.

Tests cover:
- NotificationDelivery model behavior
- Delivery record creation during notification creation
- Idempotency checks
- Task integration with delivery records
- Webhook processing

Usage:
    pytest app/notifications/tests/test_delivery.py -v
"""

import hashlib
import hmac
import json

import pytest
from django.core.cache import cache
from django.test import RequestFactory

from notifications.models import (
    DeliveryChannel,
    DeliveryStatus,
    Notification,
    NotificationCategory,
    NotificationDelivery,
    NotificationType,
    SkipReason,
    UserGlobalPreference,
    UserNotificationPreference,
)
from notifications.services import NotificationService
from notifications.tasks import (
    broadcast_websocket_notification,
    send_email_notification,
    send_push_notification,
)
from notifications.webhooks import email_webhook, fcm_webhook


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def notification_type_all_channels(db):
    """Create notification type supporting all channels."""
    return NotificationType.objects.create(
        key="all_channels",
        display_name="All Channels",
        category=NotificationCategory.SOCIAL,
        title_template="Test title",
        body_template="Test body",
        is_active=True,
        supports_push=True,
        supports_email=True,
        supports_websocket=True,
    )


@pytest.fixture
def notification_type_push_only(db):
    """Create notification type supporting only push."""
    return NotificationType.objects.create(
        key="push_only",
        display_name="Push Only",
        category=NotificationCategory.SYSTEM,
        title_template="Push title",
        body_template="Push body",
        is_active=True,
        supports_push=True,
        supports_email=False,
        supports_websocket=False,
    )


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear cache before and after each test."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def request_factory():
    """Django request factory for webhook tests."""
    return RequestFactory()


# =============================================================================
# Delivery Record Creation Tests
# =============================================================================


class TestDeliveryRecordCreation:
    """Tests for delivery record creation during notification creation."""

    def test_creates_delivery_for_each_supported_channel(
        self, user, notification_type_all_channels, mock_notification_tasks
    ):
        """Creates delivery records for all supported channels."""
        result = NotificationService.create_notification(
            recipient=user,
            type_key="all_channels",
        )

        assert result.success
        notification = result.data

        deliveries = NotificationDelivery.objects.filter(notification=notification)
        assert deliveries.count() == 3

        channels = set(d.channel for d in deliveries)
        assert channels == {
            DeliveryChannel.PUSH,
            DeliveryChannel.EMAIL,
            DeliveryChannel.WEBSOCKET,
        }

    def test_creates_only_supported_channels(
        self, user, notification_type_push_only, mock_notification_tasks
    ):
        """Only creates delivery records for supported channels."""
        result = NotificationService.create_notification(
            recipient=user,
            type_key="push_only",
        )

        assert result.success
        notification = result.data

        deliveries = NotificationDelivery.objects.filter(notification=notification)
        assert deliveries.count() == 1
        assert deliveries.first().channel == DeliveryChannel.PUSH

    def test_delivery_status_pending_when_enabled(
        self, user, notification_type_all_channels, mock_notification_tasks
    ):
        """Delivery status is PENDING when channel is enabled."""
        result = NotificationService.create_notification(
            recipient=user,
            type_key="all_channels",
        )

        notification = result.data
        for delivery in notification.deliveries.all():
            assert delivery.status == DeliveryStatus.PENDING
            assert delivery.skipped_reason == ""

    def test_delivery_status_skipped_when_global_disabled(
        self, user, notification_type_all_channels, mock_notification_tasks
    ):
        """Delivery status is SKIPPED when user has global disabled."""
        UserGlobalPreference.objects.create(user=user, all_disabled=True)

        result = NotificationService.create_notification(
            recipient=user,
            type_key="all_channels",
        )

        notification = result.data
        for delivery in notification.deliveries.all():
            assert delivery.status == DeliveryStatus.SKIPPED
            assert delivery.skipped_reason == SkipReason.GLOBAL_DISABLED

    def test_delivery_status_skipped_when_channel_disabled(
        self, user, notification_type_all_channels, mock_notification_tasks
    ):
        """Delivery status is SKIPPED when user disabled specific channel."""
        UserNotificationPreference.objects.create(
            user=user,
            notification_type=notification_type_all_channels,
            push_enabled=False,
            email_enabled=True,  # Keep email enabled
        )

        result = NotificationService.create_notification(
            recipient=user,
            type_key="all_channels",
        )

        notification = result.data

        push_delivery = notification.deliveries.get(channel=DeliveryChannel.PUSH)
        assert push_delivery.status == DeliveryStatus.SKIPPED
        assert push_delivery.skipped_reason == SkipReason.CHANNEL_DISABLED

        email_delivery = notification.deliveries.get(channel=DeliveryChannel.EMAIL)
        assert email_delivery.status == DeliveryStatus.PENDING


# =============================================================================
# Idempotency Tests
# =============================================================================


class TestIdempotency:
    """Tests for notification idempotency."""

    def test_idempotency_key_prevents_duplicate(
        self, user, notification_type_all_channels, mock_notification_tasks
    ):
        """Same idempotency_key prevents duplicate notification."""
        idempotency_key = "unique-key-123"

        result1 = NotificationService.create_notification(
            recipient=user,
            type_key="all_channels",
            idempotency_key=idempotency_key,
        )
        assert result1.success

        result2 = NotificationService.create_notification(
            recipient=user,
            type_key="all_channels",
            idempotency_key=idempotency_key,
        )
        assert not result2.success
        assert result2.error_code == "DUPLICATE"

        # Only one notification should exist
        assert Notification.objects.filter(idempotency_key=idempotency_key).count() == 1

    def test_different_idempotency_keys_create_separate(
        self, user, notification_type_all_channels, mock_notification_tasks
    ):
        """Different idempotency keys create separate notifications."""
        result1 = NotificationService.create_notification(
            recipient=user,
            type_key="all_channels",
            idempotency_key="key-1",
        )
        result2 = NotificationService.create_notification(
            recipient=user,
            type_key="all_channels",
            idempotency_key="key-2",
        )

        assert result1.success
        assert result2.success
        assert result1.data.id != result2.data.id

    def test_no_idempotency_key_creates_multiple(
        self, user, notification_type_all_channels, mock_notification_tasks
    ):
        """Without idempotency_key, multiple notifications can be created."""
        result1 = NotificationService.create_notification(
            recipient=user,
            type_key="all_channels",
        )
        result2 = NotificationService.create_notification(
            recipient=user,
            type_key="all_channels",
        )

        assert result1.success
        assert result2.success
        assert result1.data.id != result2.data.id


# =============================================================================
# Task Integration Tests
# =============================================================================


class TestTaskIntegration:
    """Tests for Celery task integration with delivery records."""

    def test_tasks_called_only_for_pending_deliveries(
        self, user, notification_type_all_channels, mocker
    ):
        """Tasks are only enqueued for PENDING deliveries."""
        mock_push = mocker.patch("notifications.tasks.send_push_notification.delay")
        mock_email = mocker.patch("notifications.tasks.send_email_notification.delay")
        mock_ws = mocker.patch(
            "notifications.tasks.broadcast_websocket_notification.delay"
        )

        # Disable push channel
        UserNotificationPreference.objects.create(
            user=user,
            notification_type=notification_type_all_channels,
            push_enabled=False,
        )

        result = NotificationService.create_notification(
            recipient=user,
            type_key="all_channels",
        )

        notification = result.data

        # Push should NOT be called (SKIPPED)
        mock_push.assert_not_called()

        # Email and WebSocket should be called
        email_delivery = notification.deliveries.get(channel=DeliveryChannel.EMAIL)
        ws_delivery = notification.deliveries.get(channel=DeliveryChannel.WEBSOCKET)

        mock_email.assert_called_once_with(str(email_delivery.id))
        mock_ws.assert_called_once_with(str(ws_delivery.id))

    def test_push_task_updates_delivery_status(
        self, user, notification_type_push_only, mock_notification_tasks
    ):
        """Push task updates delivery status to SENT."""
        result = NotificationService.create_notification(
            recipient=user,
            type_key="push_only",
        )

        notification = result.data
        delivery = notification.deliveries.first()

        # Run the task directly
        send_push_notification(str(delivery.id))

        delivery.refresh_from_db()
        assert delivery.status == DeliveryStatus.SENT
        assert delivery.sent_at is not None
        assert delivery.attempt_count == 1
        assert delivery.provider_message_id is not None

    def test_email_task_skips_no_email(
        self, user, notification_type_all_channels, mock_notification_tasks
    ):
        """Email task skips when recipient has no email."""
        # Remove user's email
        user.email = ""
        user.save()

        result = NotificationService.create_notification(
            recipient=user,
            type_key="all_channels",
        )

        notification = result.data
        delivery = notification.deliveries.get(channel=DeliveryChannel.EMAIL)

        # Run the task
        send_email_notification(str(delivery.id))

        delivery.refresh_from_db()
        assert delivery.status == DeliveryStatus.SKIPPED
        assert delivery.skipped_reason == SkipReason.NO_EMAIL

    def test_websocket_task_marks_delivered(
        self, user, notification_type_all_channels, mock_notification_tasks
    ):
        """WebSocket task marks as DELIVERED immediately."""
        result = NotificationService.create_notification(
            recipient=user,
            type_key="all_channels",
        )

        notification = result.data
        delivery = notification.deliveries.get(channel=DeliveryChannel.WEBSOCKET)

        # Run the task
        broadcast_websocket_notification(str(delivery.id))

        delivery.refresh_from_db()
        assert delivery.status == DeliveryStatus.DELIVERED
        assert delivery.sent_at is not None
        assert delivery.delivered_at is not None

    def test_task_skips_non_pending_delivery(
        self, user, notification_type_push_only, mock_notification_tasks
    ):
        """Task skips delivery if not in PENDING status."""
        result = NotificationService.create_notification(
            recipient=user,
            type_key="push_only",
        )

        notification = result.data
        delivery = notification.deliveries.first()

        # Manually set to SENT
        delivery.status = DeliveryStatus.SENT
        delivery.save()

        # Run the task - should not change anything
        result = send_push_notification(str(delivery.id))

        assert result is True  # Returns True (skipped)
        delivery.refresh_from_db()
        assert delivery.status == DeliveryStatus.SENT  # Unchanged
        assert delivery.attempt_count == 0  # Not incremented


# =============================================================================
# Webhook Tests
# =============================================================================


class TestFCMWebhook:
    """Tests for FCM webhook endpoint."""

    def test_valid_signature_accepted(
        self,
        user,
        notification_type_push_only,
        mock_notification_tasks,
        request_factory,
        settings,
    ):
        """Valid signature is accepted."""
        settings.FCM_WEBHOOK_SECRET = "test-secret"

        # Create notification and delivery
        result = NotificationService.create_notification(
            recipient=user,
            type_key="push_only",
        )
        delivery = result.data.deliveries.first()
        delivery.provider_message_id = "fcm-msg-123"
        delivery.status = DeliveryStatus.SENT
        delivery.save()

        # Build request
        payload = json.dumps(
            {
                "message_id": "fcm-msg-123",
                "status": "delivered",
            }
        ).encode()

        signature = hmac.new(
            b"test-secret",
            payload,
            hashlib.sha256,
        ).hexdigest()

        request = request_factory.post(
            "/api/v1/notifications/webhooks/fcm/",
            data=payload,
            content_type="application/json",
            HTTP_X_FCM_SIGNATURE=signature,
        )

        response = fcm_webhook(request)

        assert response.status_code == 200
        delivery.refresh_from_db()
        assert delivery.status == DeliveryStatus.DELIVERED

    def test_invalid_signature_rejected(self, request_factory, settings):
        """Invalid signature is rejected with 401."""
        settings.FCM_WEBHOOK_SECRET = "test-secret"

        payload = json.dumps(
            {
                "message_id": "fcm-msg-123",
                "status": "delivered",
            }
        ).encode()

        request = request_factory.post(
            "/api/v1/notifications/webhooks/fcm/",
            data=payload,
            content_type="application/json",
            HTTP_X_FCM_SIGNATURE="invalid-signature",
        )

        response = fcm_webhook(request)

        assert response.status_code == 401

    def test_missing_message_id_returns_400(self, request_factory, settings):
        """Missing message_id returns 400."""
        settings.FCM_WEBHOOK_SECRET = "test-secret"

        payload = json.dumps({"status": "delivered"}).encode()
        signature = hmac.new(
            b"test-secret",
            payload,
            hashlib.sha256,
        ).hexdigest()

        request = request_factory.post(
            "/api/v1/notifications/webhooks/fcm/",
            data=payload,
            content_type="application/json",
            HTTP_X_FCM_SIGNATURE=signature,
        )

        response = fcm_webhook(request)

        assert response.status_code == 400

    def test_unknown_message_id_returns_404(self, db, request_factory, settings):
        """Unknown message_id returns 404."""
        settings.FCM_WEBHOOK_SECRET = "test-secret"

        payload = json.dumps(
            {
                "message_id": "unknown-msg",
                "status": "delivered",
            }
        ).encode()
        signature = hmac.new(
            b"test-secret",
            payload,
            hashlib.sha256,
        ).hexdigest()

        request = request_factory.post(
            "/api/v1/notifications/webhooks/fcm/",
            data=payload,
            content_type="application/json",
            HTTP_X_FCM_SIGNATURE=signature,
        )

        response = fcm_webhook(request)

        assert response.status_code == 404

    def test_failed_status_marks_delivery_failed(
        self,
        user,
        notification_type_push_only,
        mock_notification_tasks,
        request_factory,
        settings,
    ):
        """Failed status marks delivery as FAILED."""
        settings.FCM_WEBHOOK_SECRET = "test-secret"

        result = NotificationService.create_notification(
            recipient=user,
            type_key="push_only",
        )
        delivery = result.data.deliveries.first()
        delivery.provider_message_id = "fcm-msg-fail"
        delivery.status = DeliveryStatus.SENT
        delivery.save()

        payload = json.dumps(
            {
                "message_id": "fcm-msg-fail",
                "status": "failed",
                "error_code": "unregistered",
                "error_message": "Token is no longer valid",
            }
        ).encode()
        signature = hmac.new(
            b"test-secret",
            payload,
            hashlib.sha256,
        ).hexdigest()

        request = request_factory.post(
            "/api/v1/notifications/webhooks/fcm/",
            data=payload,
            content_type="application/json",
            HTTP_X_FCM_SIGNATURE=signature,
        )

        response = fcm_webhook(request)

        assert response.status_code == 200
        delivery.refresh_from_db()
        assert delivery.status == DeliveryStatus.FAILED
        assert delivery.failure_code == "unregistered"
        assert delivery.is_permanent_failure is True


class TestEmailWebhook:
    """Tests for email webhook endpoint."""

    def test_delivered_event_updates_status(
        self,
        user,
        notification_type_all_channels,
        mock_notification_tasks,
        request_factory,
        settings,
    ):
        """Delivered event updates status to DELIVERED."""
        settings.EMAIL_WEBHOOK_SECRET = "email-secret"

        result = NotificationService.create_notification(
            recipient=user,
            type_key="all_channels",
        )
        delivery = result.data.deliveries.get(channel=DeliveryChannel.EMAIL)
        delivery.provider_message_id = "email-msg-123"
        delivery.status = DeliveryStatus.SENT
        delivery.save()

        payload = json.dumps(
            {
                "message_id": "email-msg-123",
                "event": "delivered",
            }
        ).encode()
        signature = hmac.new(
            b"email-secret",
            payload,
            hashlib.sha256,
        ).hexdigest()

        request = request_factory.post(
            "/api/v1/notifications/webhooks/email/",
            data=payload,
            content_type="application/json",
            HTTP_X_EMAIL_SIGNATURE=signature,
        )

        response = email_webhook(request)

        assert response.status_code == 200
        delivery.refresh_from_db()
        assert delivery.status == DeliveryStatus.DELIVERED

    def test_bounce_event_marks_failed(
        self,
        user,
        notification_type_all_channels,
        mock_notification_tasks,
        request_factory,
        settings,
    ):
        """Bounce event marks delivery as FAILED with permanent flag."""
        settings.EMAIL_WEBHOOK_SECRET = "email-secret"

        result = NotificationService.create_notification(
            recipient=user,
            type_key="all_channels",
        )
        delivery = result.data.deliveries.get(channel=DeliveryChannel.EMAIL)
        delivery.provider_message_id = "email-bounce"
        delivery.status = DeliveryStatus.SENT
        delivery.save()

        payload = json.dumps(
            {
                "message_id": "email-bounce",
                "event": "bounced",
                "error_code": "hard_bounce",
            }
        ).encode()
        signature = hmac.new(
            b"email-secret",
            payload,
            hashlib.sha256,
        ).hexdigest()

        request = request_factory.post(
            "/api/v1/notifications/webhooks/email/",
            data=payload,
            content_type="application/json",
            HTTP_X_EMAIL_SIGNATURE=signature,
        )

        response = email_webhook(request)

        assert response.status_code == 200
        delivery.refresh_from_db()
        assert delivery.status == DeliveryStatus.FAILED
        assert delivery.is_permanent_failure is True


# =============================================================================
# NotificationDelivery Model Tests
# =============================================================================


class TestNotificationDeliveryModel:
    """Tests for NotificationDelivery model."""

    def test_unique_constraint_notification_channel(
        self, user, notification_type_push_only, mock_notification_tasks
    ):
        """Cannot create duplicate deliveries for same notification+channel."""
        result = NotificationService.create_notification(
            recipient=user,
            type_key="push_only",
        )
        notification = result.data

        # Attempting to create another push delivery should fail
        from django.db import IntegrityError

        with pytest.raises(IntegrityError):
            NotificationDelivery.objects.create(
                notification=notification,
                channel=DeliveryChannel.PUSH,
                status=DeliveryStatus.PENDING,
            )

    def test_str_representation(
        self, user, notification_type_push_only, mock_notification_tasks
    ):
        """String representation is meaningful."""
        result = NotificationService.create_notification(
            recipient=user,
            type_key="push_only",
        )
        delivery = result.data.deliveries.first()

        # Just check it doesn't raise
        str_repr = str(delivery)
        assert "push" in str_repr.lower()
