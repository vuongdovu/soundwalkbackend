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
from payments.models import PaymentOrder, Payout, Refund, WebhookEvent
from payments.state_machines import (
    OnboardingStatus,
    PaymentOrderState,
    PayoutState,
    RefundState,
    WebhookEventStatus,
)
from payments.webhooks.handlers import (
    WEBHOOK_HANDLERS,
    dispatch_webhook,
    handle_account_updated,
    handle_charge_refunded,
    handle_payment_intent_canceled,
    handle_payment_intent_failed,
    handle_payment_intent_succeeded,
    handle_transfer_created,
    handle_transfer_failed,
    handle_transfer_paid,
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


# =============================================================================
# Transfer Created Handler Tests
# =============================================================================


class TestHandleTransferCreated:
    """Tests for transfer.created handler."""

    def test_marks_processing_payout_as_scheduled(
        self, db, processing_payout_with_transfer_id
    ):
        """Should mark processing payout as scheduled."""
        payout = processing_payout_with_transfer_id

        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_tr_created_123",
            event_type="transfer.created",
            payload={
                "id": "evt_tr_created_123",
                "type": "transfer.created",
                "data": {
                    "object": {
                        "id": payout.stripe_transfer_id,
                        "object": "transfer",
                        "amount": payout.amount_cents,
                        "currency": payout.currency,
                    }
                },
            },
            status=WebhookEventStatus.PENDING,
        )

        result = handle_transfer_created(webhook_event)

        assert result.success is True

        # Reload payout from DB (use objects.get to avoid django-fsm refresh issue)
        payout = Payout.objects.get(id=payout.id)
        assert payout.state == PayoutState.SCHEDULED

    def test_payout_not_found_succeeds(self, db):
        """Should succeed when payout not found (external transfer)."""
        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_tr_created_notfound_123",
            event_type="transfer.created",
            payload={
                "id": "evt_tr_created_notfound_123",
                "type": "transfer.created",
                "data": {
                    "object": {
                        "id": "tr_nonexistent_xyz",
                        "object": "transfer",
                    }
                },
            },
            status=WebhookEventStatus.PENDING,
        )

        result = handle_transfer_created(webhook_event)

        # Should succeed gracefully (might be external transfer)
        assert result.success is True

    def test_already_scheduled_succeeds(self, db, scheduled_payout_with_transfer_id):
        """Should succeed when payout already scheduled (idempotent)."""
        payout = scheduled_payout_with_transfer_id

        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_tr_created_scheduled_123",
            event_type="transfer.created",
            payload={
                "id": "evt_tr_created_scheduled_123",
                "type": "transfer.created",
                "data": {
                    "object": {
                        "id": payout.stripe_transfer_id,
                        "object": "transfer",
                    }
                },
            },
            status=WebhookEventStatus.PENDING,
        )

        result = handle_transfer_created(webhook_event)

        assert result.success is True

        # State unchanged (use objects.get to avoid django-fsm refresh issue)
        payout = Payout.objects.get(id=payout.id)
        assert payout.state == PayoutState.SCHEDULED

    def test_already_paid_succeeds(self, db, paid_payout_with_transfer_id):
        """Should succeed when payout already paid (idempotent)."""
        payout = paid_payout_with_transfer_id

        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_tr_created_paid_123",
            event_type="transfer.created",
            payload={
                "id": "evt_tr_created_paid_123",
                "type": "transfer.created",
                "data": {
                    "object": {
                        "id": payout.stripe_transfer_id,
                        "object": "transfer",
                    }
                },
            },
            status=WebhookEventStatus.PENDING,
        )

        result = handle_transfer_created(webhook_event)

        assert result.success is True

        # State unchanged (use objects.get to avoid django-fsm refresh issue)
        payout = Payout.objects.get(id=payout.id)
        assert payout.state == PayoutState.PAID


# =============================================================================
# Transfer Paid Handler Tests
# =============================================================================


class TestHandleTransferPaid:
    """Tests for transfer.paid handler."""

    def test_completes_scheduled_payout(self, db, scheduled_payout_with_transfer_id):
        """Should complete scheduled payout."""
        payout = scheduled_payout_with_transfer_id

        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_tr_paid_scheduled_123",
            event_type="transfer.paid",
            payload={
                "id": "evt_tr_paid_scheduled_123",
                "type": "transfer.paid",
                "data": {
                    "object": {
                        "id": payout.stripe_transfer_id,
                        "object": "transfer",
                        "amount": payout.amount_cents,
                    }
                },
            },
            status=WebhookEventStatus.PENDING,
        )

        result = handle_transfer_paid(webhook_event)

        assert result.success is True

        # Reload payout from DB (use objects.get to avoid django-fsm refresh issue)
        payout = Payout.objects.get(id=payout.id)
        assert payout.state == PayoutState.PAID
        assert payout.paid_at is not None

    def test_completes_processing_payout(self, db, processing_payout_with_transfer_id):
        """Should complete processing payout (skipping scheduled)."""
        payout = processing_payout_with_transfer_id

        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_tr_paid_processing_123",
            event_type="transfer.paid",
            payload={
                "id": "evt_tr_paid_processing_123",
                "type": "transfer.paid",
                "data": {
                    "object": {
                        "id": payout.stripe_transfer_id,
                        "object": "transfer",
                    }
                },
            },
            status=WebhookEventStatus.PENDING,
        )

        result = handle_transfer_paid(webhook_event)

        assert result.success is True

        # Reload payout from DB (use objects.get to avoid django-fsm refresh issue)
        payout = Payout.objects.get(id=payout.id)
        assert payout.state == PayoutState.PAID

    def test_payout_not_found_fails(self, db):
        """Should fail when payout not found."""
        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_tr_paid_notfound_123",
            event_type="transfer.paid",
            payload={
                "id": "evt_tr_paid_notfound_123",
                "type": "transfer.paid",
                "data": {
                    "object": {
                        "id": "tr_nonexistent_paid",
                        "object": "transfer",
                    }
                },
            },
            status=WebhookEventStatus.PENDING,
        )

        result = handle_transfer_paid(webhook_event)

        assert result.success is False
        assert result.error_code == "PAYOUT_NOT_FOUND"

    def test_already_paid_idempotent(self, db, paid_payout_with_transfer_id):
        """Should succeed when payout already paid (idempotent)."""
        payout = paid_payout_with_transfer_id
        original_paid_at = payout.paid_at

        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_tr_paid_already_123",
            event_type="transfer.paid",
            payload={
                "id": "evt_tr_paid_already_123",
                "type": "transfer.paid",
                "data": {
                    "object": {
                        "id": payout.stripe_transfer_id,
                        "object": "transfer",
                    }
                },
            },
            status=WebhookEventStatus.PENDING,
        )

        result = handle_transfer_paid(webhook_event)

        assert result.success is True

        # Reload payout from DB (use objects.get to avoid django-fsm refresh issue)
        payout = Payout.objects.get(id=payout.id)
        assert payout.state == PayoutState.PAID
        # paid_at should not have changed
        assert payout.paid_at == original_paid_at

    def test_creates_ledger_entry_on_completion(
        self, db, scheduled_payout_with_transfer_id
    ):
        """Should create ledger entry when payout completes."""
        from payments.ledger import LedgerService
        from payments.ledger.models import LedgerEntry, EntryType, AccountType
        from payments.ledger.types import RecordEntryParams

        payout = scheduled_payout_with_transfer_id

        # Seed the USER_BALANCE account with funds (simulating prior payment release)
        # In the real flow, money is credited when hold is released
        recipient_profile_id = payout.connected_account.profile_id
        user_balance = LedgerService.get_or_create_account(
            AccountType.USER_BALANCE,
            owner_id=recipient_profile_id,
            currency=payout.currency,
        )
        external_account = LedgerService.get_or_create_account(
            AccountType.EXTERNAL_STRIPE,
            owner_id=None,
            currency=payout.currency,
            allow_negative=True,
        )
        # Seed balance by recording money coming in
        LedgerService.record_entry(
            RecordEntryParams(
                debit_account_id=external_account.id,
                credit_account_id=user_balance.id,
                amount_cents=payout.amount_cents,
                entry_type=EntryType.PAYMENT_RELEASED,
                idempotency_key=f"test_seed:{payout.id}",
                description="Test seed for payout ledger test",
                created_by="test",
            )
        )

        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_tr_paid_ledger_123",
            event_type="transfer.paid",
            payload={
                "id": "evt_tr_paid_ledger_123",
                "type": "transfer.paid",
                "data": {
                    "object": {
                        "id": payout.stripe_transfer_id,
                        "object": "transfer",
                        "amount": payout.amount_cents,
                    }
                },
            },
            status=WebhookEventStatus.PENDING,
        )

        result = handle_transfer_paid(webhook_event)

        assert result.success is True

        # Verify ledger entry was created
        entries = LedgerEntry.objects.filter(
            reference_type="payout",
            reference_id=payout.id,
        )
        assert entries.count() == 1

        entry = entries.first()
        assert entry.entry_type == EntryType.PAYOUT
        assert entry.amount_cents == payout.amount_cents
        assert entry.idempotency_key == f"payout:{payout.id}:completion"
        assert entry.created_by == "transfer_paid_handler"

    def test_ledger_entry_is_idempotent(self, db, scheduled_payout_with_transfer_id):
        """Should not create duplicate ledger entries on duplicate webhooks."""
        from payments.ledger import LedgerService
        from payments.ledger.models import LedgerEntry, AccountType, EntryType
        from payments.ledger.types import RecordEntryParams

        payout = scheduled_payout_with_transfer_id

        # Seed the USER_BALANCE account with funds
        recipient_profile_id = payout.connected_account.profile_id
        user_balance = LedgerService.get_or_create_account(
            AccountType.USER_BALANCE,
            owner_id=recipient_profile_id,
            currency=payout.currency,
        )
        external_account = LedgerService.get_or_create_account(
            AccountType.EXTERNAL_STRIPE,
            owner_id=None,
            currency=payout.currency,
            allow_negative=True,
        )
        LedgerService.record_entry(
            RecordEntryParams(
                debit_account_id=external_account.id,
                credit_account_id=user_balance.id,
                amount_cents=payout.amount_cents,
                entry_type=EntryType.PAYMENT_RELEASED,
                idempotency_key=f"test_seed_idempotent:{payout.id}",
                description="Test seed for payout idempotency test",
                created_by="test",
            )
        )

        # Create first webhook event
        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_tr_paid_idemp_1",
            event_type="transfer.paid",
            payload={
                "id": "evt_tr_paid_idemp_1",
                "type": "transfer.paid",
                "data": {
                    "object": {
                        "id": payout.stripe_transfer_id,
                        "object": "transfer",
                    }
                },
            },
            status=WebhookEventStatus.PENDING,
        )

        # First call
        result1 = handle_transfer_paid(webhook_event)
        assert result1.success is True

        # Count entries after first call
        entry_count_after_first = LedgerEntry.objects.filter(
            reference_type="payout",
            reference_id=payout.id,
        ).count()
        assert entry_count_after_first == 1

        # Create second webhook event (simulating duplicate delivery)
        # Note: The payout is now PAID, so we just verify no additional entries
        # The handler will return early for PAID state
        entries_before_second = LedgerEntry.objects.count()

        webhook_event2 = WebhookEvent.objects.create(
            stripe_event_id="evt_tr_paid_idemp_2",
            event_type="transfer.paid",
            payload={
                "id": "evt_tr_paid_idemp_2",
                "type": "transfer.paid",
                "data": {
                    "object": {
                        "id": payout.stripe_transfer_id,
                        "object": "transfer",
                    }
                },
            },
            status=WebhookEventStatus.PENDING,
        )

        result2 = handle_transfer_paid(webhook_event2)
        assert result2.success is True

        # No new entries created
        assert LedgerEntry.objects.count() == entries_before_second

    def test_settles_released_payment_order(
        self, db, released_payment_order_with_payout
    ):
        """Should settle PaymentOrder when payout completes (escrow flow)."""
        order = released_payment_order_with_payout["order"]
        payout = released_payment_order_with_payout["payout"]

        # Verify initial state
        assert order.state == PaymentOrderState.RELEASED
        assert payout.state == PayoutState.PROCESSING

        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_tr_paid_settle_123",
            event_type="transfer.paid",
            payload={
                "id": "evt_tr_paid_settle_123",
                "type": "transfer.paid",
                "data": {
                    "object": {
                        "id": payout.stripe_transfer_id,
                        "object": "transfer",
                        "amount": payout.amount_cents,
                    }
                },
            },
            status=WebhookEventStatus.PENDING,
        )

        result = handle_transfer_paid(webhook_event)

        assert result.success is True

        # Reload from DB
        payout = Payout.objects.get(id=payout.id)
        order = PaymentOrder.objects.get(id=order.id)

        # Payout should be PAID
        assert payout.state == PayoutState.PAID
        assert payout.paid_at is not None

        # PaymentOrder should be SETTLED
        assert order.state == PaymentOrderState.SETTLED
        assert order.settled_at is not None

    def test_does_not_settle_non_released_order(
        self, db, processing_payout_with_transfer_id
    ):
        """Should not attempt to settle order that is not in RELEASED state."""
        payout = processing_payout_with_transfer_id
        order = payout.payment_order

        # Order is in PENDING state (from fixture)
        assert order.state == PaymentOrderState.PENDING

        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_tr_paid_no_settle_123",
            event_type="transfer.paid",
            payload={
                "id": "evt_tr_paid_no_settle_123",
                "type": "transfer.paid",
                "data": {
                    "object": {
                        "id": payout.stripe_transfer_id,
                        "object": "transfer",
                    }
                },
            },
            status=WebhookEventStatus.PENDING,
        )

        result = handle_transfer_paid(webhook_event)

        assert result.success is True

        # Reload from DB
        payout = Payout.objects.get(id=payout.id)
        order = PaymentOrder.objects.get(id=order.id)

        # Payout should be PAID
        assert payout.state == PayoutState.PAID

        # Order should remain PENDING (not settled)
        assert order.state == PaymentOrderState.PENDING


# =============================================================================
# Transfer Failed Handler Tests
# =============================================================================


class TestHandleTransferFailed:
    """Tests for transfer.failed handler."""

    def test_fails_processing_payout(self, db, processing_payout_with_transfer_id):
        """Should fail processing payout with reason."""
        payout = processing_payout_with_transfer_id

        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_tr_failed_processing_123",
            event_type="transfer.failed",
            payload={
                "id": "evt_tr_failed_processing_123",
                "type": "transfer.failed",
                "data": {
                    "object": {
                        "id": payout.stripe_transfer_id,
                        "object": "transfer",
                        "failure_code": "account_closed",
                        "failure_message": "The bank account is closed.",
                    }
                },
            },
            status=WebhookEventStatus.PENDING,
        )

        result = handle_transfer_failed(webhook_event)

        assert result.success is True

        # Reload payout from DB (use objects.get to avoid django-fsm refresh issue)
        payout = Payout.objects.get(id=payout.id)
        assert payout.state == PayoutState.FAILED
        assert payout.failed_at is not None
        assert "account_closed" in payout.failure_reason

    def test_fails_scheduled_payout(self, db, scheduled_payout_with_transfer_id):
        """Should fail scheduled payout with reason."""
        payout = scheduled_payout_with_transfer_id

        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_tr_failed_scheduled_123",
            event_type="transfer.failed",
            payload={
                "id": "evt_tr_failed_scheduled_123",
                "type": "transfer.failed",
                "data": {
                    "object": {
                        "id": payout.stripe_transfer_id,
                        "object": "transfer",
                        "failure_code": "insufficient_funds",
                        "failure_message": "Insufficient funds on platform account.",
                    }
                },
            },
            status=WebhookEventStatus.PENDING,
        )

        result = handle_transfer_failed(webhook_event)

        assert result.success is True

        # Reload payout from DB (use objects.get to avoid django-fsm refresh issue)
        payout = Payout.objects.get(id=payout.id)
        assert payout.state == PayoutState.FAILED
        assert "insufficient_funds" in payout.failure_reason

    def test_payout_not_found_fails(self, db):
        """Should fail when payout not found."""
        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_tr_failed_notfound_123",
            event_type="transfer.failed",
            payload={
                "id": "evt_tr_failed_notfound_123",
                "type": "transfer.failed",
                "data": {
                    "object": {
                        "id": "tr_nonexistent_failed",
                        "object": "transfer",
                        "failure_code": "unknown",
                        "failure_message": "Unknown error",
                    }
                },
            },
            status=WebhookEventStatus.PENDING,
        )

        result = handle_transfer_failed(webhook_event)

        assert result.success is False
        assert result.error_code == "PAYOUT_NOT_FOUND"

    def test_already_failed_idempotent(self, db, failed_payout_with_transfer_id):
        """Should succeed when payout already failed (idempotent)."""
        payout = failed_payout_with_transfer_id

        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_tr_failed_already_123",
            event_type="transfer.failed",
            payload={
                "id": "evt_tr_failed_already_123",
                "type": "transfer.failed",
                "data": {
                    "object": {
                        "id": payout.stripe_transfer_id,
                        "object": "transfer",
                        "failure_code": "account_closed",
                        "failure_message": "Account closed.",
                    }
                },
            },
            status=WebhookEventStatus.PENDING,
        )

        result = handle_transfer_failed(webhook_event)

        assert result.success is True

        # Reload payout from DB (use objects.get to avoid django-fsm refresh issue)
        payout = Payout.objects.get(id=payout.id)
        assert payout.state == PayoutState.FAILED


# =============================================================================
# Charge Refunded Handler Tests
# =============================================================================


class TestHandleChargeRefunded:
    """Tests for charge.refunded handler."""

    def test_creates_refund_record(self, db, settled_payment_order):
        """Should create refund record for external refund."""
        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_ch_refunded_123",
            event_type="charge.refunded",
            payload={
                "id": "evt_ch_refunded_123",
                "type": "charge.refunded",
                "data": {
                    "object": {
                        "id": "ch_test_123",
                        "object": "charge",
                        "payment_intent": settled_payment_order.stripe_payment_intent_id,
                        "amount": 10000,
                        "amount_refunded": 5000,
                        "currency": "usd",
                        "refunds": {
                            "data": [
                                {
                                    "id": "re_test_partial_123",
                                    "amount": 5000,
                                    "reason": "requested_by_customer",
                                    "status": "succeeded",
                                }
                            ]
                        },
                    }
                },
            },
            status=WebhookEventStatus.PENDING,
        )

        result = handle_charge_refunded(webhook_event)

        assert result.success is True

        # Check refund was created
        refund = Refund.objects.filter(stripe_refund_id="re_test_partial_123").first()
        assert refund is not None
        assert refund.amount_cents == 5000
        assert refund.state == RefundState.COMPLETED

    def test_full_refund_updates_payment_order(self, db, settled_payment_order):
        """Should mark payment order as refunded for full refund."""
        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_ch_refunded_full_123",
            event_type="charge.refunded",
            payload={
                "id": "evt_ch_refunded_full_123",
                "type": "charge.refunded",
                "data": {
                    "object": {
                        "id": "ch_test_full_123",
                        "object": "charge",
                        "payment_intent": settled_payment_order.stripe_payment_intent_id,
                        "amount": 10000,
                        "amount_refunded": 10000,  # Full refund
                        "currency": "usd",
                        "refunds": {
                            "data": [
                                {
                                    "id": "re_test_full_123",
                                    "amount": 10000,
                                    "reason": "requested_by_customer",
                                    "status": "succeeded",
                                }
                            ]
                        },
                    }
                },
            },
            status=WebhookEventStatus.PENDING,
        )

        result = handle_charge_refunded(webhook_event)

        assert result.success is True

        # Check payment order state (use objects.get to avoid django-fsm refresh issue)
        order = PaymentOrder.objects.get(id=settled_payment_order.id)
        assert order.state == PaymentOrderState.REFUNDED

    def test_partial_refund_updates_payment_order(self, db, settled_payment_order):
        """Should mark payment order as partially refunded."""
        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_ch_refunded_partial_123",
            event_type="charge.refunded",
            payload={
                "id": "evt_ch_refunded_partial_123",
                "type": "charge.refunded",
                "data": {
                    "object": {
                        "id": "ch_test_partial_123",
                        "object": "charge",
                        "payment_intent": settled_payment_order.stripe_payment_intent_id,
                        "amount": 10000,
                        "amount_refunded": 3000,  # Partial refund
                        "currency": "usd",
                        "refunds": {
                            "data": [
                                {
                                    "id": "re_test_partial_456",
                                    "amount": 3000,
                                    "reason": "duplicate",
                                    "status": "succeeded",
                                }
                            ]
                        },
                    }
                },
            },
            status=WebhookEventStatus.PENDING,
        )

        result = handle_charge_refunded(webhook_event)

        assert result.success is True

        # Check payment order state (use objects.get to avoid django-fsm refresh issue)
        order = PaymentOrder.objects.get(id=settled_payment_order.id)
        assert order.state == PaymentOrderState.PARTIALLY_REFUNDED

    def test_existing_refund_idempotent(self, db, settled_payment_order):
        """Should skip existing completed refunds (idempotent)."""
        # Create existing refund
        existing_refund = Refund.objects.create(
            payment_order=settled_payment_order,
            amount_cents=5000,
            currency="usd",
            stripe_refund_id="re_existing_123",
            reason="Original refund",
        )
        existing_refund.process()
        existing_refund.complete()
        existing_refund.save()

        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_ch_refunded_existing_123",
            event_type="charge.refunded",
            payload={
                "id": "evt_ch_refunded_existing_123",
                "type": "charge.refunded",
                "data": {
                    "object": {
                        "id": "ch_test_existing_123",
                        "object": "charge",
                        "payment_intent": settled_payment_order.stripe_payment_intent_id,
                        "amount": 10000,
                        "amount_refunded": 5000,
                        "currency": "usd",
                        "refunds": {
                            "data": [
                                {
                                    "id": "re_existing_123",
                                    "amount": 5000,
                                    "reason": "duplicate_payload",
                                    "status": "succeeded",
                                }
                            ]
                        },
                    }
                },
            },
            status=WebhookEventStatus.PENDING,
        )

        result = handle_charge_refunded(webhook_event)

        assert result.success is True

        # Only one refund should exist
        assert Refund.objects.filter(stripe_refund_id="re_existing_123").count() == 1

    def test_payment_order_not_found_fails(self, db):
        """Should fail when payment order not found."""
        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_ch_refunded_notfound_123",
            event_type="charge.refunded",
            payload={
                "id": "evt_ch_refunded_notfound_123",
                "type": "charge.refunded",
                "data": {
                    "object": {
                        "id": "ch_test_notfound_123",
                        "object": "charge",
                        "payment_intent": "pi_nonexistent_refund_123",
                        "amount": 10000,
                        "amount_refunded": 5000,
                        "currency": "usd",
                        "refunds": {"data": []},
                    }
                },
            },
            status=WebhookEventStatus.PENDING,
        )

        result = handle_charge_refunded(webhook_event)

        assert result.success is False
        assert result.error_code == "PAYMENT_ORDER_NOT_FOUND"


# =============================================================================
# Account Updated Handler Tests
# =============================================================================


class TestHandleAccountUpdated:
    """Tests for account.updated handler."""

    def test_updates_payouts_enabled(self, db, connected_account_fixture):
        """Should update payouts_enabled status."""
        account = connected_account_fixture
        account.payouts_enabled = False
        account.save()

        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_acct_updated_payouts_123",
            event_type="account.updated",
            payload={
                "id": "evt_acct_updated_payouts_123",
                "type": "account.updated",
                "data": {
                    "object": {
                        "id": account.stripe_account_id,
                        "object": "account",
                        "payouts_enabled": True,
                        "charges_enabled": True,
                        "requirements": {
                            "currently_due": [],
                            "past_due": [],
                        },
                    }
                },
            },
            status=WebhookEventStatus.PENDING,
        )

        result = handle_account_updated(webhook_event)

        assert result.success is True

        account.refresh_from_db()
        assert account.payouts_enabled is True

    def test_updates_charges_enabled(self, db, connected_account_fixture):
        """Should update charges_enabled status."""
        account = connected_account_fixture
        account.charges_enabled = False
        account.save()

        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_acct_updated_charges_123",
            event_type="account.updated",
            payload={
                "id": "evt_acct_updated_charges_123",
                "type": "account.updated",
                "data": {
                    "object": {
                        "id": account.stripe_account_id,
                        "object": "account",
                        "payouts_enabled": True,
                        "charges_enabled": True,
                        "requirements": {
                            "currently_due": [],
                            "past_due": [],
                        },
                    }
                },
            },
            status=WebhookEventStatus.PENDING,
        )

        result = handle_account_updated(webhook_event)

        assert result.success is True

        account.refresh_from_db()
        assert account.charges_enabled is True

    def test_updates_onboarding_status_complete(self, db, connected_account_fixture):
        """Should mark onboarding as complete when no requirements."""
        account = connected_account_fixture
        account.onboarding_status = OnboardingStatus.IN_PROGRESS
        account.save()

        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_acct_updated_complete_123",
            event_type="account.updated",
            payload={
                "id": "evt_acct_updated_complete_123",
                "type": "account.updated",
                "data": {
                    "object": {
                        "id": account.stripe_account_id,
                        "object": "account",
                        "payouts_enabled": True,
                        "charges_enabled": True,
                        "requirements": {
                            "currently_due": [],
                            "past_due": [],
                        },
                    }
                },
            },
            status=WebhookEventStatus.PENDING,
        )

        result = handle_account_updated(webhook_event)

        assert result.success is True

        account.refresh_from_db()
        assert account.onboarding_status == OnboardingStatus.COMPLETE

    def test_updates_onboarding_status_in_progress(self, db, connected_account_fixture):
        """Should mark onboarding as in progress when requirements due."""
        account = connected_account_fixture
        account.onboarding_status = OnboardingStatus.NOT_STARTED
        account.save()

        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_acct_updated_inprogress_123",
            event_type="account.updated",
            payload={
                "id": "evt_acct_updated_inprogress_123",
                "type": "account.updated",
                "data": {
                    "object": {
                        "id": account.stripe_account_id,
                        "object": "account",
                        "payouts_enabled": False,
                        "charges_enabled": False,
                        "requirements": {
                            "currently_due": ["business_profile.url"],
                            "past_due": [],
                        },
                    }
                },
            },
            status=WebhookEventStatus.PENDING,
        )

        result = handle_account_updated(webhook_event)

        assert result.success is True

        account.refresh_from_db()
        assert account.onboarding_status == OnboardingStatus.IN_PROGRESS

    def test_updates_onboarding_status_rejected(self, db, connected_account_fixture):
        """Should mark onboarding as rejected when disabled."""
        account = connected_account_fixture

        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_acct_updated_rejected_123",
            event_type="account.updated",
            payload={
                "id": "evt_acct_updated_rejected_123",
                "type": "account.updated",
                "data": {
                    "object": {
                        "id": account.stripe_account_id,
                        "object": "account",
                        "payouts_enabled": False,
                        "charges_enabled": False,
                        "requirements": {
                            "currently_due": [],
                            "past_due": [],
                            "disabled_reason": "rejected.fraud",
                        },
                    }
                },
            },
            status=WebhookEventStatus.PENDING,
        )

        result = handle_account_updated(webhook_event)

        assert result.success is True

        account.refresh_from_db()
        assert account.onboarding_status == OnboardingStatus.REJECTED

    def test_account_not_found_succeeds(self, db):
        """Should succeed when account not found (external account)."""
        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_acct_updated_notfound_123",
            event_type="account.updated",
            payload={
                "id": "evt_acct_updated_notfound_123",
                "type": "account.updated",
                "data": {
                    "object": {
                        "id": "acct_external_xyz",
                        "object": "account",
                        "payouts_enabled": True,
                        "charges_enabled": True,
                        "requirements": {
                            "currently_due": [],
                            "past_due": [],
                        },
                    }
                },
            },
            status=WebhookEventStatus.PENDING,
        )

        result = handle_account_updated(webhook_event)

        # Should succeed gracefully
        assert result.success is True
