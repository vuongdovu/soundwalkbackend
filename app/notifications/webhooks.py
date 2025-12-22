"""
Webhook endpoints for notification delivery callbacks.

This module provides webhook endpoints for external providers (FCM, email)
to report delivery status updates.

Endpoints:
    POST /api/v1/notifications/webhooks/fcm/ - FCM delivery callback
    POST /api/v1/notifications/webhooks/email/ - Email delivery callback

Security:
    - All webhooks require HMAC-SHA256 signature verification
    - Signatures are validated against configured secrets
    - Invalid signatures return 401 Unauthorized

Usage:
    # In urls.py
    from notifications.webhooks import FCMWebhookView, EmailWebhookView

    urlpatterns = [
        path("webhooks/fcm/", FCMWebhookView.as_view(), name="fcm-webhook"),
        path("webhooks/email/", EmailWebhookView.as_view(), name="email-webhook"),
    ]

Configuration:
    Set in settings.py:
    - FCM_WEBHOOK_SECRET: Secret for FCM webhook signature verification
    - EMAIL_WEBHOOK_SECRET: Secret for email webhook signature verification
"""

from __future__ import annotations

import hashlib
import hmac
import logging

from django.conf import settings
from django.utils import timezone

from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_spectacular.utils import extend_schema, OpenApiResponse

from notifications.models import DeliveryStatus, NotificationDelivery


logger = logging.getLogger(__name__)


# =============================================================================
# Webhook Serializers
# =============================================================================


class FCMWebhookRequestSerializer(serializers.Serializer):
    """Request body for FCM delivery webhook."""

    message_id = serializers.CharField(help_text="Provider message ID for correlation")
    status = serializers.ChoiceField(
        choices=["delivered", "failed"],
        help_text="Delivery status",
    )
    error_code = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
        help_text="Error code if delivery failed",
    )
    error_message = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
        help_text="Error message if delivery failed",
    )


class EmailWebhookRequestSerializer(serializers.Serializer):
    """Request body for email delivery webhook."""

    message_id = serializers.CharField(help_text="Provider message ID for correlation")
    event = serializers.ChoiceField(
        choices=["delivered", "bounced", "complained", "dropped"],
        help_text="Email delivery event type",
    )
    error_code = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
        help_text="Error code for bounce/drop events",
    )
    error_message = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
        help_text="Error message for failed events",
    )


class WebhookSuccessResponseSerializer(serializers.Serializer):
    """Success response from webhooks."""

    success = serializers.BooleanField(default=True)


class WebhookErrorResponseSerializer(serializers.Serializer):
    """Error response from webhooks."""

    error = serializers.CharField(help_text="Error description")


# =============================================================================
# Helper Functions
# =============================================================================


def _verify_signature(
    payload: bytes,
    signature: str,
    secret: str,
) -> bool:
    """
    Verify HMAC-SHA256 signature.

    Args:
        payload: Raw request body
        signature: Signature from request header
        secret: Shared secret for HMAC

    Returns:
        True if signature is valid
    """
    if not secret:
        logger.warning("Webhook secret not configured, rejecting request")
        return False

    expected = hmac.new(
        secret.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature)


def _update_delivery_status(
    provider_message_id: str,
    delivery_status: str,
    error_code: str | None = None,
    error_message: str | None = None,
) -> bool:
    """
    Update delivery status based on webhook payload.

    Args:
        provider_message_id: Provider's message ID for correlation
        delivery_status: New status (delivered, failed)
        error_code: Optional error code on failure
        error_message: Optional error message on failure

    Returns:
        True if delivery was found and updated
    """
    try:
        delivery = NotificationDelivery.objects.get(
            provider_message_id=provider_message_id
        )
    except NotificationDelivery.DoesNotExist:
        logger.warning(
            f"Delivery not found for provider_message_id={provider_message_id}"
        )
        return False

    if delivery_status == "delivered":
        delivery.status = DeliveryStatus.DELIVERED
        delivery.delivered_at = timezone.now()
        delivery.save(update_fields=["status", "delivered_at", "updated_at"])
        logger.info(f"Marked delivery {delivery.id} as delivered")
    elif delivery_status == "failed":
        delivery.status = DeliveryStatus.FAILED
        delivery.failed_at = timezone.now()
        delivery.failure_code = error_code or ""
        delivery.failure_reason = error_message or ""
        delivery.is_permanent_failure = error_code in {
            "unregistered",
            "invalid_token",
            "invalid_email",
            "hard_bounce",
        }
        delivery.save(
            update_fields=[
                "status",
                "failed_at",
                "failure_code",
                "failure_reason",
                "is_permanent_failure",
                "updated_at",
            ]
        )
        logger.info(f"Marked delivery {delivery.id} as failed: {error_code}")
    else:
        logger.warning(f"Unknown status '{delivery_status}' for delivery {delivery.id}")
        return False

    return True


# =============================================================================
# Webhook Views
# =============================================================================


class FCMWebhookView(APIView):
    """
    FCM delivery callback webhook.

    Receives delivery status updates from Firebase Cloud Messaging.
    Requires HMAC-SHA256 signature verification via X-FCM-Signature header.
    """

    permission_classes = [AllowAny]
    authentication_classes = []  # No auth required - signature-based validation

    @extend_schema(
        operation_id="fcm_delivery_webhook",
        summary="FCM delivery callback",
        description=(
            "Webhook endpoint for FCM to report push notification delivery status. "
            "Requires HMAC-SHA256 signature verification using the X-FCM-Signature header."
        ),
        request=FCMWebhookRequestSerializer,
        responses={
            200: WebhookSuccessResponseSerializer,
            400: OpenApiResponse(
                response=WebhookErrorResponseSerializer,
                description="Invalid request payload",
            ),
            401: OpenApiResponse(
                response=WebhookErrorResponseSerializer,
                description="Invalid or missing signature",
            ),
            404: OpenApiResponse(
                response=WebhookErrorResponseSerializer,
                description="Delivery record not found",
            ),
        },
        tags=["Notifications - Webhooks"],
    )
    def post(self, request):
        """Handle FCM delivery status callback."""
        # Verify signature
        signature = request.headers.get("X-FCM-Signature", "")
        secret = getattr(settings, "FCM_WEBHOOK_SECRET", "")

        if not _verify_signature(request.body, signature, secret):
            logger.warning("FCM webhook signature verification failed")
            return Response(
                {"error": "Invalid signature"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # Validate request data
        serializer = FCMWebhookRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"error": "Invalid payload", "details": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = serializer.validated_data
        message_id = data["message_id"]
        delivery_status = data["status"]

        # Update delivery
        found = _update_delivery_status(
            provider_message_id=message_id,
            delivery_status=delivery_status,
            error_code=data.get("error_code"),
            error_message=data.get("error_message"),
        )

        if not found:
            return Response(
                {"error": "Delivery not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response({"success": True})


class EmailWebhookView(APIView):
    """
    Email delivery callback webhook.

    Receives delivery status updates from email providers (SendGrid, Mailgun, etc.).
    Requires HMAC-SHA256 signature verification via X-Email-Signature header.
    """

    permission_classes = [AllowAny]
    authentication_classes = []  # No auth required - signature-based validation

    @extend_schema(
        operation_id="email_delivery_webhook",
        summary="Email delivery callback",
        description=(
            "Webhook endpoint for email providers (SendGrid, Mailgun, etc.) to report "
            "delivery status. Requires HMAC-SHA256 signature verification using the "
            "X-Email-Signature header."
        ),
        request=EmailWebhookRequestSerializer,
        responses={
            200: WebhookSuccessResponseSerializer,
            400: OpenApiResponse(
                response=WebhookErrorResponseSerializer,
                description="Invalid request payload",
            ),
            401: OpenApiResponse(
                response=WebhookErrorResponseSerializer,
                description="Invalid or missing signature",
            ),
            404: OpenApiResponse(
                response=WebhookErrorResponseSerializer,
                description="Delivery record not found",
            ),
        },
        tags=["Notifications - Webhooks"],
    )
    def post(self, request):
        """Handle email delivery status callback."""
        # Verify signature
        signature = request.headers.get("X-Email-Signature", "")
        secret = getattr(settings, "EMAIL_WEBHOOK_SECRET", "")

        if not _verify_signature(request.body, signature, secret):
            logger.warning("Email webhook signature verification failed")
            return Response(
                {"error": "Invalid signature"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # Validate request data
        serializer = EmailWebhookRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"error": "Invalid payload", "details": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = serializer.validated_data
        message_id = data["message_id"]
        event = data["event"]

        # Map email events to delivery status
        event_to_status = {
            "delivered": "delivered",
            "bounced": "failed",
            "complained": "failed",
            "dropped": "failed",
        }

        delivery_status = event_to_status.get(event)
        if not delivery_status:
            logger.info(f"Ignoring email event: {event}")
            return Response({"success": True})  # Ignore unknown events

        # Set error code for bounce/complaint events
        error_code = None
        if event == "bounced":
            error_code = data.get("error_code") or "hard_bounce"
        elif event == "complained":
            error_code = "complaint"
        elif event == "dropped":
            error_code = data.get("error_code") or "dropped"

        # Update delivery
        found = _update_delivery_status(
            provider_message_id=message_id,
            delivery_status=delivery_status,
            error_code=error_code,
            error_message=data.get("error_message"),
        )

        if not found:
            return Response(
                {"error": "Delivery not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response({"success": True})


# =============================================================================
# Backwards compatibility: Function-based views that delegate to class-based
# =============================================================================


# Create view instances for use in urls.py
_fcm_webhook_view = FCMWebhookView.as_view()
_email_webhook_view = EmailWebhookView.as_view()


def fcm_webhook(request):
    """FCM webhook (backwards-compatible function)."""
    return _fcm_webhook_view(request)


def email_webhook(request):
    """Email webhook (backwards-compatible function)."""
    return _email_webhook_view(request)
