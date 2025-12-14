"""
Tests for webhook event handlers.

Tests cover:
- Handler registration
- Handler dispatch
- payment_intent.succeeded handling
- payment_intent.payment_failed handling
- payment_intent.canceled handling
- Unknown event handling
"""

from core.services import ServiceResult
from payments.models import PaymentOrder, WebhookEvent
from payments.state_machines import PaymentOrderState, WebhookEventStatus
from payments.webhooks.handlers import (
    WEBHOOK_HANDLERS,
    dispatch_webhook,
    handle_payment_intent_canceled,
    handle_payment_intent_failed,
    handle_payment_intent_succeeded,
    register_handler,
)


# =============================================================================
# Handler Registration Tests
# =============================================================================


class TestRegisterHandler:
    """Tests for handler registration decorator."""

    def test_register_handler_adds_to_registry(self):
        """Should add handler to WEBHOOK_HANDLERS registry."""
        # The handlers are registered at import time
        assert "payment_intent.succeeded" in WEBHOOK_HANDLERS
        assert "payment_intent.payment_failed" in WEBHOOK_HANDLERS
        assert "payment_intent.canceled" in WEBHOOK_HANDLERS

    def test_register_handler_maps_to_function(self):
        """Should map event type to correct handler function."""
        assert (
            WEBHOOK_HANDLERS["payment_intent.succeeded"]
            == handle_payment_intent_succeeded
        )
        assert (
            WEBHOOK_HANDLERS["payment_intent.payment_failed"]
            == handle_payment_intent_failed
        )
        assert (
            WEBHOOK_HANDLERS["payment_intent.canceled"]
            == handle_payment_intent_canceled
        )

    def test_register_new_handler(self):
        """Should register a new handler."""

        @register_handler("test.event.type")
        def test_handler(webhook_event):
            return ServiceResult.success(None)

        assert "test.event.type" in WEBHOOK_HANDLERS
        assert WEBHOOK_HANDLERS["test.event.type"] == test_handler

        # Cleanup
        del WEBHOOK_HANDLERS["test.event.type"]


# =============================================================================
# Dispatch Tests
# =============================================================================


class TestDispatchWebhook:
    """Tests for webhook dispatch function."""

    def test_dispatch_to_registered_handler(self, db):
        """Should dispatch to correct handler for registered event type."""
        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_dispatch_test_123",
            event_type="payment_intent.succeeded",
            payload={
                "id": "evt_dispatch_test_123",
                "type": "payment_intent.succeeded",
                "data": {
                    "object": {
                        "id": "pi_nonexistent_123",
                        "object": "payment_intent",
                    }
                },
            },
            status=WebhookEventStatus.PENDING,
        )

        # Dispatch will fail because no PaymentOrder exists, but it should call handler
        result = dispatch_webhook(webhook_event)

        # Handler was called (will fail with not found)
        assert result.success is False
        assert "not found" in result.error.lower()

    def test_dispatch_unknown_event_type(self, db):
        """Should return success for unknown event types."""
        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_unknown_type_123",
            event_type="customer.subscription.deleted",
            payload={
                "id": "evt_unknown_type_123",
                "type": "customer.subscription.deleted",
                "data": {"object": {"id": "sub_123"}},
            },
            status=WebhookEventStatus.PENDING,
        )

        result = dispatch_webhook(webhook_event)

        # Should succeed (gracefully ignore unknown events)
        assert result.success is True


# =============================================================================
# Payment Intent Succeeded Handler Tests
# =============================================================================


class TestHandlePaymentIntentSucceeded:
    """Tests for payment_intent.succeeded handler."""

    def test_success_with_pending_order(
        self, db, pending_payment_order, mock_ledger_service
    ):
        """Should process pending order to settled."""
        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_pi_succeeded_pending_123",
            event_type="payment_intent.succeeded",
            payload={
                "id": "evt_pi_succeeded_pending_123",
                "type": "payment_intent.succeeded",
                "data": {
                    "object": {
                        "id": pending_payment_order.stripe_payment_intent_id,
                        "object": "payment_intent",
                        "amount": 10000,
                        "amount_received": 10000,
                        "currency": "usd",
                        "status": "succeeded",
                    }
                },
            },
            status=WebhookEventStatus.PENDING,
        )

        result = handle_payment_intent_succeeded(webhook_event)

        assert result.success is True

        # Reload order from DB (avoid django-fsm refresh_from_db issue)
        order = PaymentOrder.objects.get(id=pending_payment_order.id)
        assert order.state == PaymentOrderState.SETTLED

    def test_success_idempotent_for_settled_order(
        self, db, test_user, mock_ledger_service
    ):
        """Should be idempotent for already settled orders."""
        # Create a settled order
        order = PaymentOrder.objects.create(
            payer=test_user,
            amount_cents=10000,
            currency="usd",
            stripe_payment_intent_id="pi_test_already_settled_123",
        )
        order.submit()
        order.save()
        order.process()
        order.save()
        order.capture()
        order.save()
        order.settle_from_captured()
        order.save()

        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_pi_succeeded_settled_123",
            event_type="payment_intent.succeeded",
            payload={
                "id": "evt_pi_succeeded_settled_123",
                "type": "payment_intent.succeeded",
                "data": {
                    "object": {
                        "id": order.stripe_payment_intent_id,
                        "object": "payment_intent",
                        "amount": 10000,
                        "amount_received": 10000,
                        "currency": "usd",
                        "status": "succeeded",
                    }
                },
            },
            status=WebhookEventStatus.PENDING,
        )

        result = handle_payment_intent_succeeded(webhook_event)

        # Should succeed (idempotent)
        assert result.success is True

        # Order remains settled
        order = PaymentOrder.objects.get(id=order.id)
        assert order.state == PaymentOrderState.SETTLED

    def test_order_not_found(self, db):
        """Should fail if PaymentOrder not found."""
        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_pi_succeeded_notfound_123",
            event_type="payment_intent.succeeded",
            payload={
                "id": "evt_pi_succeeded_notfound_123",
                "type": "payment_intent.succeeded",
                "data": {
                    "object": {
                        "id": "pi_nonexistent_xyz_789",
                        "object": "payment_intent",
                    }
                },
            },
            status=WebhookEventStatus.PENDING,
        )

        result = handle_payment_intent_succeeded(webhook_event)

        assert result.success is False
        assert result.error_code == "PAYMENT_ORDER_NOT_FOUND"

    def test_missing_payment_intent_id(self, db):
        """Should fail if payment_intent_id not in payload."""
        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_pi_succeeded_noid_123",
            event_type="payment_intent.succeeded",
            payload={
                "id": "evt_pi_succeeded_noid_123",
                "type": "payment_intent.succeeded",
                "data": {
                    "object": {
                        # Missing 'id' field
                        "object": "payment_intent",
                    }
                },
            },
            status=WebhookEventStatus.PENDING,
        )

        result = handle_payment_intent_succeeded(webhook_event)

        assert result.success is False
        assert result.error_code == "INVALID_WEBHOOK_PAYLOAD"


# =============================================================================
# Payment Intent Failed Handler Tests
# =============================================================================


class TestHandlePaymentIntentFailed:
    """Tests for payment_intent.payment_failed handler."""

    def test_fail_pending_order(self, db, pending_payment_order):
        """Should fail pending order."""
        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_pi_failed_pending_123",
            event_type="payment_intent.payment_failed",
            payload={
                "id": "evt_pi_failed_pending_123",
                "type": "payment_intent.payment_failed",
                "data": {
                    "object": {
                        "id": pending_payment_order.stripe_payment_intent_id,
                        "object": "payment_intent",
                        "amount": 10000,
                        "currency": "usd",
                        "status": "requires_payment_method",
                        "last_payment_error": {
                            "code": "card_declined",
                            "message": "Your card was declined.",
                        },
                    }
                },
            },
            status=WebhookEventStatus.PENDING,
        )

        result = handle_payment_intent_failed(webhook_event)

        assert result.success is True

        # Reload order from DB (avoid django-fsm refresh_from_db issue)
        order = PaymentOrder.objects.get(id=pending_payment_order.id)
        assert order.state == PaymentOrderState.FAILED
        assert "declined" in order.failure_reason.lower()

    def test_fail_processing_order_returns_invalid_state(
        self, db, processing_payment_order
    ):
        """Should return invalid state error for processing order (already advanced)."""
        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_pi_failed_processing_123",
            event_type="payment_intent.payment_failed",
            payload={
                "id": "evt_pi_failed_processing_123",
                "type": "payment_intent.payment_failed",
                "data": {
                    "object": {
                        "id": processing_payment_order.stripe_payment_intent_id,
                        "object": "payment_intent",
                        "amount": 10000,
                        "currency": "usd",
                        "status": "requires_payment_method",
                        "last_payment_error": {
                            "code": "insufficient_funds",
                            "message": "Your card has insufficient funds.",
                        },
                    }
                },
            },
            status=WebhookEventStatus.PENDING,
        )

        result = handle_payment_intent_failed(webhook_event)

        # Processing orders cannot be failed - they've already moved past PENDING
        assert result.success is False
        assert result.error_code == "INVALID_STATE"

    def test_order_not_found(self, db):
        """Should fail if PaymentOrder not found."""
        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_pi_failed_notfound_123",
            event_type="payment_intent.payment_failed",
            payload={
                "id": "evt_pi_failed_notfound_123",
                "type": "payment_intent.payment_failed",
                "data": {
                    "object": {
                        "id": "pi_nonexistent_failed_123",
                        "object": "payment_intent",
                        "last_payment_error": {
                            "message": "Card declined",
                        },
                    }
                },
            },
            status=WebhookEventStatus.PENDING,
        )

        result = handle_payment_intent_failed(webhook_event)

        assert result.success is False
        assert result.error_code == "PAYMENT_ORDER_NOT_FOUND"

    def test_default_failure_message(self, db, pending_payment_order):
        """Should use default message if last_payment_error missing."""
        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_pi_failed_noerror_123",
            event_type="payment_intent.payment_failed",
            payload={
                "id": "evt_pi_failed_noerror_123",
                "type": "payment_intent.payment_failed",
                "data": {
                    "object": {
                        "id": pending_payment_order.stripe_payment_intent_id,
                        "object": "payment_intent",
                        "amount": 10000,
                        "currency": "usd",
                        "status": "requires_payment_method",
                        # No last_payment_error
                    }
                },
            },
            status=WebhookEventStatus.PENDING,
        )

        result = handle_payment_intent_failed(webhook_event)

        assert result.success is True

        # Reload order from DB
        order = PaymentOrder.objects.get(id=pending_payment_order.id)
        assert order.failure_reason == "Payment failed"


# =============================================================================
# Payment Intent Canceled Handler Tests
# =============================================================================


class TestHandlePaymentIntentCanceled:
    """Tests for payment_intent.canceled handler."""

    def test_cancel_draft_order(self, db, draft_payment_order):
        """Should cancel draft order."""
        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_pi_canceled_draft_123",
            event_type="payment_intent.canceled",
            payload={
                "id": "evt_pi_canceled_draft_123",
                "type": "payment_intent.canceled",
                "data": {
                    "object": {
                        "id": draft_payment_order.stripe_payment_intent_id,
                        "object": "payment_intent",
                        "status": "canceled",
                    }
                },
            },
            status=WebhookEventStatus.PENDING,
        )

        result = handle_payment_intent_canceled(webhook_event)

        assert result.success is True

        # Reload order from DB
        order = PaymentOrder.objects.get(id=draft_payment_order.id)
        assert order.state == PaymentOrderState.CANCELLED

    def test_cancel_pending_order(self, db, pending_payment_order):
        """Should cancel pending order."""
        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_pi_canceled_pending_123",
            event_type="payment_intent.canceled",
            payload={
                "id": "evt_pi_canceled_pending_123",
                "type": "payment_intent.canceled",
                "data": {
                    "object": {
                        "id": pending_payment_order.stripe_payment_intent_id,
                        "object": "payment_intent",
                        "status": "canceled",
                    }
                },
            },
            status=WebhookEventStatus.PENDING,
        )

        result = handle_payment_intent_canceled(webhook_event)

        assert result.success is True

        # Reload order from DB
        order = PaymentOrder.objects.get(id=pending_payment_order.id)
        assert order.state == PaymentOrderState.CANCELLED

    def test_cancel_nonexistent_order_succeeds(self, db):
        """Should succeed if PaymentOrder not found (might be cleaned up)."""
        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_pi_canceled_notfound_123",
            event_type="payment_intent.canceled",
            payload={
                "id": "evt_pi_canceled_notfound_123",
                "type": "payment_intent.canceled",
                "data": {
                    "object": {
                        "id": "pi_nonexistent_canceled_123",
                        "object": "payment_intent",
                        "status": "canceled",
                    }
                },
            },
            status=WebhookEventStatus.PENDING,
        )

        result = handle_payment_intent_canceled(webhook_event)

        # Should succeed gracefully
        assert result.success is True

    def test_cancel_already_settled_order(self, db, test_user, mock_ledger_service):
        """Should not cancel already settled order."""
        # Create settled order
        order = PaymentOrder.objects.create(
            payer=test_user,
            amount_cents=10000,
            currency="usd",
            stripe_payment_intent_id="pi_settled_cancel_test",
        )
        order.submit()
        order.save()
        order.process()
        order.save()
        order.capture()
        order.save()
        order.settle_from_captured()
        order.save()

        assert order.state == PaymentOrderState.SETTLED

        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_pi_canceled_settled_123",
            event_type="payment_intent.canceled",
            payload={
                "id": "evt_pi_canceled_settled_123",
                "type": "payment_intent.canceled",
                "data": {
                    "object": {
                        "id": "pi_settled_cancel_test",
                        "object": "payment_intent",
                        "status": "canceled",
                    }
                },
            },
            status=WebhookEventStatus.PENDING,
        )

        result = handle_payment_intent_canceled(webhook_event)

        # Should succeed but order should remain settled
        assert result.success is True

        # Reload order from DB
        reloaded_order = PaymentOrder.objects.get(id=order.id)
        assert reloaded_order.state == PaymentOrderState.SETTLED
