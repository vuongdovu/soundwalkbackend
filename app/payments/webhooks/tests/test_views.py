"""
Tests for webhook views.

Tests cover:
- Stripe signature verification
- Webhook event creation and idempotency
- Task queuing
- Error handling
"""

import json
from unittest.mock import patch

import pytest
from django.test import RequestFactory

from payments.exceptions import StripeInvalidRequestError
from payments.models import WebhookEvent
from payments.state_machines import WebhookEventStatus
from payments.webhooks.views import stripe_webhook


# =============================================================================
# Setup
# =============================================================================


@pytest.fixture
def rf():
    """Request factory for creating test requests."""
    return RequestFactory()


def make_webhook_request(rf, payload: dict, signature: str = "test_sig"):
    """Create a POST request to the webhook endpoint."""
    request = rf.post(
        "/api/v1/payments/webhooks/stripe/",
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_STRIPE_SIGNATURE=signature,
    )
    return request


# =============================================================================
# Signature Verification Tests
# =============================================================================


class TestStripeWebhookSignature:
    """Tests for signature verification."""

    def test_missing_signature_returns_400(self, rf, db):
        """Should return 400 if Stripe-Signature header is missing."""
        request = rf.post(
            "/api/v1/payments/webhooks/stripe/",
            data=json.dumps({"id": "evt_test"}),
            content_type="application/json",
        )

        response = stripe_webhook(request)

        assert response.status_code == 400
        assert b"Missing signature" in response.content

    def test_invalid_signature_returns_400(self, rf, db):
        """Should return 400 if signature verification fails."""
        with patch(
            "payments.webhooks.views.StripeAdapter.verify_webhook_signature"
        ) as mock_verify:
            mock_verify.side_effect = StripeInvalidRequestError("Invalid signature")

            request = make_webhook_request(
                rf,
                {"id": "evt_test", "type": "payment_intent.succeeded"},
                signature="invalid_sig",
            )

            response = stripe_webhook(request)

            assert response.status_code == 400
            assert b"Invalid signature" in response.content

    def test_unexpected_verification_error_returns_400(self, rf, db):
        """Should return 400 for unexpected verification errors."""
        with patch(
            "payments.webhooks.views.StripeAdapter.verify_webhook_signature"
        ) as mock_verify:
            mock_verify.side_effect = Exception("Unexpected error")

            request = make_webhook_request(
                rf,
                {"id": "evt_test", "type": "payment_intent.succeeded"},
            )

            response = stripe_webhook(request)

            assert response.status_code == 400
            assert b"Verification error" in response.content


# =============================================================================
# Event Creation Tests
# =============================================================================


class TestStripeWebhookEventCreation:
    """Tests for webhook event creation."""

    def test_creates_new_webhook_event(self, rf, db):
        """Should create new WebhookEvent for new event."""
        payload = {
            "id": "evt_new_event_123",
            "type": "payment_intent.succeeded",
            "data": {"object": {"id": "pi_123"}},
        }

        with patch(
            "payments.webhooks.views.StripeAdapter.verify_webhook_signature"
        ) as mock_verify:
            mock_verify.return_value = payload

            with patch("payments.tasks.process_webhook_event.delay"):
                request = make_webhook_request(rf, payload)
                response = stripe_webhook(request)

        assert response.status_code == 200
        assert WebhookEvent.objects.filter(stripe_event_id="evt_new_event_123").exists()

        event = WebhookEvent.objects.get(stripe_event_id="evt_new_event_123")
        assert event.event_type == "payment_intent.succeeded"
        assert event.status == WebhookEventStatus.PENDING
        assert event.payload == payload

    def test_idempotent_for_processed_events(self, rf, db, processed_webhook_event):
        """Should return 200 without reprocessing for already processed events."""
        payload = processed_webhook_event.payload

        with patch(
            "payments.webhooks.views.StripeAdapter.verify_webhook_signature"
        ) as mock_verify:
            mock_verify.return_value = payload

            with patch("payments.tasks.process_webhook_event.delay") as mock_task:
                request = make_webhook_request(rf, payload)
                response = stripe_webhook(request)

        assert response.status_code == 200
        assert b"Already processed" in response.content
        # Task should not be queued for already processed events
        mock_task.assert_not_called()

    def test_queues_task_for_processing_events(self, rf, db, processing_webhook_event):
        """Should still return 200 for events being processed."""
        payload = processing_webhook_event.payload

        with patch(
            "payments.webhooks.views.StripeAdapter.verify_webhook_signature"
        ) as mock_verify:
            mock_verify.return_value = payload

            with patch("payments.tasks.process_webhook_event.delay"):
                request = make_webhook_request(rf, payload)
                response = stripe_webhook(request)

        # Should return success (don't block Stripe retries)
        assert response.status_code == 200

    def test_invalid_payload_missing_id(self, rf, db):
        """Should return 400 if payload missing event id."""
        payload = {
            # Missing "id"
            "type": "payment_intent.succeeded",
            "data": {"object": {"id": "pi_123"}},
        }

        with patch(
            "payments.webhooks.views.StripeAdapter.verify_webhook_signature"
        ) as mock_verify:
            mock_verify.return_value = payload

            request = make_webhook_request(rf, payload)
            response = stripe_webhook(request)

        assert response.status_code == 400
        assert b"Invalid event" in response.content

    def test_invalid_payload_missing_type(self, rf, db):
        """Should return 400 if payload missing event type."""
        payload = {
            "id": "evt_no_type_123",
            # Missing "type"
            "data": {"object": {"id": "pi_123"}},
        }

        with patch(
            "payments.webhooks.views.StripeAdapter.verify_webhook_signature"
        ) as mock_verify:
            mock_verify.return_value = payload

            request = make_webhook_request(rf, payload)
            response = stripe_webhook(request)

        assert response.status_code == 400
        assert b"Invalid event" in response.content


# =============================================================================
# Task Queuing Tests
# =============================================================================


class TestStripeWebhookTaskQueuing:
    """Tests for async task queuing."""

    def test_queues_task_for_new_event(self, rf, db):
        """Should queue Celery task for new event."""
        payload = {
            "id": "evt_queue_test_123",
            "type": "payment_intent.succeeded",
            "data": {"object": {"id": "pi_123"}},
        }

        with patch(
            "payments.webhooks.views.StripeAdapter.verify_webhook_signature"
        ) as mock_verify:
            mock_verify.return_value = payload

            with patch("payments.tasks.process_webhook_event.delay") as mock_task:
                request = make_webhook_request(rf, payload)
                response = stripe_webhook(request)

        assert response.status_code == 200
        mock_task.assert_called_once()

        # Verify the task was called with the correct webhook event ID
        event = WebhookEvent.objects.get(stripe_event_id="evt_queue_test_123")
        mock_task.assert_called_with(event.id)

    def test_task_queuing_failure_returns_200(self, rf, db):
        """Should return 200 even if task queuing fails (Stripe will retry)."""
        payload = {
            "id": "evt_queue_fail_123",
            "type": "payment_intent.succeeded",
            "data": {"object": {"id": "pi_123"}},
        }

        with patch(
            "payments.webhooks.views.StripeAdapter.verify_webhook_signature"
        ) as mock_verify:
            mock_verify.return_value = payload

            with patch("payments.tasks.process_webhook_event.delay") as mock_task:
                mock_task.side_effect = Exception("Celery connection error")

                request = make_webhook_request(rf, payload)
                response = stripe_webhook(request)

        # Should still return 200 - Stripe will retry
        assert response.status_code == 200
        assert b"Accepted" in response.content

        # Event should still be created
        assert WebhookEvent.objects.filter(
            stripe_event_id="evt_queue_fail_123"
        ).exists()


# =============================================================================
# HTTP Method Tests
# =============================================================================


class TestStripeWebhookHttpMethods:
    """Tests for HTTP method restrictions."""

    def test_get_not_allowed(self, rf, db):
        """GET requests should be rejected."""
        request = rf.get("/api/v1/payments/webhooks/stripe/")

        response = stripe_webhook(request)

        # Django's require_POST returns 405
        assert response.status_code == 405

    def test_put_not_allowed(self, rf, db):
        """PUT requests should be rejected."""
        request = rf.put(
            "/api/v1/payments/webhooks/stripe/",
            data="{}",
            content_type="application/json",
        )

        response = stripe_webhook(request)

        assert response.status_code == 405

    def test_delete_not_allowed(self, rf, db):
        """DELETE requests should be rejected."""
        request = rf.delete("/api/v1/payments/webhooks/stripe/")

        response = stripe_webhook(request)

        assert response.status_code == 405
