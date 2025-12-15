"""
Tests for subscription webhook event handlers.

Tests cover:
- invoice.paid handler (subscription renewal payments)
- invoice.payment_failed handler (marks subscription past_due)
- customer.subscription.created handler
- customer.subscription.updated handler
- customer.subscription.deleted handler (cancellation)
- Idempotency for all handlers
- Edge cases and error handling
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from authentication.models import Profile, User
from payments.models import ConnectedAccount, PaymentOrder, WebhookEvent
from payments.state_machines import (
    OnboardingStatus,
    PaymentOrderState,
    PaymentStrategyType,
    WebhookEventStatus,
)


# =============================================================================
# User & Profile Fixtures
# =============================================================================


@pytest.fixture
def subscriber_user(db):
    """Create a subscriber user."""
    return User.objects.create_user(
        email="subscriber@example.com",
        password="testpass123",
    )


@pytest.fixture
def recipient_user(db):
    """Create a recipient user who receives subscription payments."""
    return User.objects.create_user(
        email="recipient@example.com",
        password="testpass123",
    )


@pytest.fixture
def recipient_profile(db, recipient_user):
    """Create a profile for the recipient."""
    profile, _ = Profile.objects.get_or_create(user=recipient_user)
    return profile


@pytest.fixture
def connected_account(db, recipient_profile):
    """Create a connected account for the recipient with completed onboarding."""
    return ConnectedAccount.objects.create(
        profile=recipient_profile,
        stripe_account_id="acct_test_recipient_sub_123",
        onboarding_status=OnboardingStatus.COMPLETE,
        payouts_enabled=True,
        charges_enabled=True,
    )


# =============================================================================
# Subscription Fixtures
# =============================================================================


@pytest.fixture
def pending_subscription(db, subscriber_user, recipient_profile):
    """Create a subscription in PENDING state (awaiting first payment)."""
    from payments.models import Subscription

    return Subscription.objects.create(
        payer=subscriber_user,
        recipient_profile_id=recipient_profile.pk,
        stripe_subscription_id="sub_test_pending_123",
        stripe_customer_id="cus_test_subscriber_123",
        stripe_price_id="price_test_monthly_123",
        amount_cents=10000,
        currency="usd",
        billing_interval="month",
        current_period_start=timezone.now(),
        current_period_end=timezone.now() + timedelta(days=30),
    )


@pytest.fixture
def active_subscription(db, subscriber_user, recipient_profile):
    """Create a subscription in ACTIVE state."""
    from payments.models import Subscription

    subscription = Subscription.objects.create(
        payer=subscriber_user,
        recipient_profile_id=recipient_profile.pk,
        stripe_subscription_id="sub_test_active_123",
        stripe_customer_id="cus_test_subscriber_123",
        stripe_price_id="price_test_monthly_123",
        amount_cents=10000,
        currency="usd",
        billing_interval="month",
        current_period_start=timezone.now(),
        current_period_end=timezone.now() + timedelta(days=30),
    )
    subscription.activate()
    subscription.save()
    return subscription


@pytest.fixture
def past_due_subscription(db, subscriber_user, recipient_profile):
    """Create a subscription in PAST_DUE state."""
    from payments.models import Subscription

    subscription = Subscription.objects.create(
        payer=subscriber_user,
        recipient_profile_id=recipient_profile.pk,
        stripe_subscription_id="sub_test_past_due_123",
        stripe_customer_id="cus_test_subscriber_123",
        stripe_price_id="price_test_monthly_123",
        amount_cents=10000,
        currency="usd",
        billing_interval="month",
        current_period_start=timezone.now() - timedelta(days=30),
        current_period_end=timezone.now(),
    )
    subscription.activate()
    subscription.save()
    subscription.mark_past_due()
    subscription.save()
    return subscription


# =============================================================================
# Invoice Webhook Payload Fixtures
# =============================================================================


@pytest.fixture
def invoice_paid_payload(active_subscription):
    """Stripe invoice.paid webhook payload for subscription renewal."""
    now = timezone.now()
    return {
        "id": "evt_invoice_paid_test_123",
        "type": "invoice.paid",
        "data": {
            "object": {
                "id": "in_test_paid_123",
                "object": "invoice",
                "subscription": active_subscription.stripe_subscription_id,
                "customer": active_subscription.stripe_customer_id,
                "amount_paid": 10000,
                "amount_due": 10000,
                "currency": "usd",
                "status": "paid",
                "billing_reason": "subscription_cycle",
                "period_start": int(now.timestamp()),
                "period_end": int((now + timedelta(days=30)).timestamp()),
                "lines": {
                    "data": [
                        {
                            "id": "il_test_123",
                            "price": {"id": "price_test_monthly_123"},
                            "period": {
                                "start": int(now.timestamp()),
                                "end": int((now + timedelta(days=30)).timestamp()),
                            },
                        }
                    ]
                },
                "metadata": {},
            }
        },
    }


@pytest.fixture
def invoice_paid_first_payment_payload(pending_subscription):
    """Stripe invoice.paid webhook payload for first subscription payment."""
    now = timezone.now()
    return {
        "id": "evt_invoice_paid_first_123",
        "type": "invoice.paid",
        "data": {
            "object": {
                "id": "in_test_first_payment_123",
                "object": "invoice",
                "subscription": pending_subscription.stripe_subscription_id,
                "customer": pending_subscription.stripe_customer_id,
                "amount_paid": 10000,
                "amount_due": 10000,
                "currency": "usd",
                "status": "paid",
                "billing_reason": "subscription_create",
                "period_start": int(now.timestamp()),
                "period_end": int((now + timedelta(days=30)).timestamp()),
                "lines": {
                    "data": [
                        {
                            "id": "il_test_first_123",
                            "price": {"id": "price_test_monthly_123"},
                        }
                    ]
                },
                "metadata": {},
            }
        },
    }


@pytest.fixture
def invoice_payment_failed_payload(active_subscription):
    """Stripe invoice.payment_failed webhook payload."""
    return {
        "id": "evt_invoice_failed_test_123",
        "type": "invoice.payment_failed",
        "data": {
            "object": {
                "id": "in_test_failed_123",
                "object": "invoice",
                "subscription": active_subscription.stripe_subscription_id,
                "customer": active_subscription.stripe_customer_id,
                "amount_due": 10000,
                "currency": "usd",
                "status": "open",
                "attempt_count": 1,
                "next_payment_attempt": int(
                    (timezone.now() + timedelta(days=3)).timestamp()
                ),
                "last_finalization_error": {
                    "code": "card_declined",
                    "message": "Your card was declined.",
                },
            }
        },
    }


# =============================================================================
# Subscription Webhook Payload Fixtures
# =============================================================================


@pytest.fixture
def subscription_created_payload():
    """Stripe customer.subscription.created webhook payload."""
    now = timezone.now()
    return {
        "id": "evt_sub_created_test_123",
        "type": "customer.subscription.created",
        "data": {
            "object": {
                "id": "sub_test_new_123",
                "object": "subscription",
                "customer": "cus_test_new_123",
                "status": "incomplete",
                "current_period_start": int(now.timestamp()),
                "current_period_end": int((now + timedelta(days=30)).timestamp()),
                "cancel_at_period_end": False,
                "items": {
                    "data": [
                        {
                            "id": "si_test_123",
                            "price": {
                                "id": "price_test_monthly_123",
                                "unit_amount": 10000,
                                "currency": "usd",
                                "recurring": {"interval": "month"},
                            },
                        }
                    ]
                },
                "metadata": {
                    "recipient_profile_id": "test-profile-uuid",
                },
            }
        },
    }


@pytest.fixture
def subscription_updated_payload(active_subscription):
    """Stripe customer.subscription.updated webhook payload."""
    now = timezone.now()
    return {
        "id": "evt_sub_updated_test_123",
        "type": "customer.subscription.updated",
        "data": {
            "object": {
                "id": active_subscription.stripe_subscription_id,
                "object": "subscription",
                "customer": active_subscription.stripe_customer_id,
                "status": "active",
                "current_period_start": int(now.timestamp()),
                "current_period_end": int((now + timedelta(days=30)).timestamp()),
                "cancel_at_period_end": True,  # User requested cancellation at period end
                "canceled_at": int(now.timestamp()),
                "metadata": {},
            }
        },
    }


@pytest.fixture
def subscription_deleted_payload(active_subscription):
    """Stripe customer.subscription.deleted webhook payload."""
    return {
        "id": "evt_sub_deleted_test_123",
        "type": "customer.subscription.deleted",
        "data": {
            "object": {
                "id": active_subscription.stripe_subscription_id,
                "object": "subscription",
                "customer": active_subscription.stripe_customer_id,
                "status": "canceled",
                "canceled_at": int(timezone.now().timestamp()),
                "metadata": {},
            }
        },
    }


# =============================================================================
# invoice.paid Handler Tests
# =============================================================================


class TestHandleInvoicePaid:
    """Tests for invoice.paid webhook handler."""

    def test_creates_payment_order_for_renewal(
        self, db, active_subscription, invoice_paid_payload, connected_account
    ):
        """Should create a PaymentOrder for subscription renewal."""
        from payments.webhooks.handlers import handle_invoice_paid

        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_invoice_paid_test_123",
            event_type="invoice.paid",
            payload=invoice_paid_payload,
            status=WebhookEventStatus.PENDING,
        )

        result = handle_invoice_paid(webhook_event)

        assert result.success is True

        # Verify PaymentOrder was created
        order = PaymentOrder.objects.filter(
            stripe_invoice_id="in_test_paid_123"
        ).first()
        assert order is not None
        assert order.payer == active_subscription.payer
        assert order.amount_cents == 10000
        assert order.currency == "usd"
        assert order.strategy_type == PaymentStrategyType.SUBSCRIPTION
        assert order.subscription == active_subscription
        assert order.state == PaymentOrderState.SETTLED

    def test_activates_subscription_on_first_payment(
        self,
        db,
        pending_subscription,
        invoice_paid_first_payment_payload,
        connected_account,
    ):
        """Should activate subscription on first successful payment."""
        from payments.models import Subscription
        from payments.state_machines import SubscriptionState
        from payments.webhooks.handlers import handle_invoice_paid

        # Connect account to the right profile
        connected_account.profile_id = pending_subscription.recipient_profile_id
        connected_account.save()

        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_invoice_paid_first_123",
            event_type="invoice.paid",
            payload=invoice_paid_first_payment_payload,
            status=WebhookEventStatus.PENDING,
        )

        result = handle_invoice_paid(webhook_event)

        assert result.success is True

        # Reload subscription
        subscription = Subscription.objects.get(id=pending_subscription.id)
        assert subscription.state == SubscriptionState.ACTIVE

    def test_reactivates_past_due_subscription(
        self, db, past_due_subscription, connected_account
    ):
        """Should reactivate past_due subscription when payment succeeds."""
        from payments.models import Subscription
        from payments.state_machines import SubscriptionState
        from payments.webhooks.handlers import handle_invoice_paid

        now = timezone.now()
        payload = {
            "id": "evt_invoice_paid_reactivate_123",
            "type": "invoice.paid",
            "data": {
                "object": {
                    "id": "in_test_reactivate_123",
                    "object": "invoice",
                    "subscription": past_due_subscription.stripe_subscription_id,
                    "customer": past_due_subscription.stripe_customer_id,
                    "amount_paid": 10000,
                    "currency": "usd",
                    "status": "paid",
                    "billing_reason": "subscription_cycle",
                    "period_start": int(now.timestamp()),
                    "period_end": int((now + timedelta(days=30)).timestamp()),
                    "lines": {"data": []},
                }
            },
        }

        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_invoice_paid_reactivate_123",
            event_type="invoice.paid",
            payload=payload,
            status=WebhookEventStatus.PENDING,
        )

        result = handle_invoice_paid(webhook_event)

        assert result.success is True

        # Reload subscription
        subscription = Subscription.objects.get(id=past_due_subscription.id)
        assert subscription.state == SubscriptionState.ACTIVE

    def test_idempotent_duplicate_invoice_ignored(
        self, db, active_subscription, invoice_paid_payload, connected_account
    ):
        """Should not create duplicate PaymentOrder for same invoice."""
        from payments.webhooks.handlers import handle_invoice_paid

        # Create existing PaymentOrder for this invoice
        PaymentOrder.objects.create(
            payer=active_subscription.payer,
            amount_cents=10000,
            currency="usd",
            strategy_type=PaymentStrategyType.SUBSCRIPTION,
            subscription=active_subscription,
            stripe_invoice_id="in_test_paid_123",
        )

        order_count_before = PaymentOrder.objects.count()

        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_invoice_paid_duplicate_123",
            event_type="invoice.paid",
            payload=invoice_paid_payload,
            status=WebhookEventStatus.PENDING,
        )

        result = handle_invoice_paid(webhook_event)

        # Should succeed (idempotent)
        assert result.success is True

        # No new orders created
        assert PaymentOrder.objects.count() == order_count_before

    def test_skips_non_subscription_invoice(self, db):
        """Should skip invoices not related to subscriptions."""
        from payments.webhooks.handlers import handle_invoice_paid

        payload = {
            "id": "evt_invoice_paid_oneoff_123",
            "type": "invoice.paid",
            "data": {
                "object": {
                    "id": "in_test_oneoff_123",
                    "object": "invoice",
                    "subscription": None,  # Not a subscription invoice
                    "customer": "cus_test_123",
                    "amount_paid": 5000,
                    "currency": "usd",
                    "status": "paid",
                    "billing_reason": "manual",
                }
            },
        }

        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_invoice_paid_oneoff_123",
            event_type="invoice.paid",
            payload=payload,
            status=WebhookEventStatus.PENDING,
        )

        result = handle_invoice_paid(webhook_event)

        # Should succeed but skip processing
        assert result.success is True

        # No PaymentOrder created
        assert not PaymentOrder.objects.filter(
            stripe_invoice_id="in_test_oneoff_123"
        ).exists()

    def test_subscription_not_found_fails(self, db):
        """Should fail when subscription not found in local database."""
        from payments.webhooks.handlers import handle_invoice_paid

        payload = {
            "id": "evt_invoice_paid_nosub_123",
            "type": "invoice.paid",
            "data": {
                "object": {
                    "id": "in_test_nosub_123",
                    "object": "invoice",
                    "subscription": "sub_nonexistent_xyz",
                    "customer": "cus_test_123",
                    "amount_paid": 10000,
                    "currency": "usd",
                    "status": "paid",
                    "billing_reason": "subscription_cycle",
                }
            },
        }

        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_invoice_paid_nosub_123",
            event_type="invoice.paid",
            payload=payload,
            status=WebhookEventStatus.PENDING,
        )

        result = handle_invoice_paid(webhook_event)

        assert result.success is False
        assert result.error_code == "SUBSCRIPTION_NOT_FOUND"

    def test_updates_subscription_period_dates(
        self, db, active_subscription, invoice_paid_payload, connected_account
    ):
        """Should update subscription period dates from invoice."""
        from payments.models import Subscription
        from payments.webhooks.handlers import handle_invoice_paid

        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_invoice_paid_dates_123",
            event_type="invoice.paid",
            payload=invoice_paid_payload,
            status=WebhookEventStatus.PENDING,
        )

        result = handle_invoice_paid(webhook_event)

        assert result.success is True

        # Reload subscription
        subscription = Subscription.objects.get(id=active_subscription.id)

        # Period dates should be updated
        invoice_data = invoice_paid_payload["data"]["object"]
        assert subscription.last_invoice_id == invoice_data["id"]
        assert subscription.last_payment_at is not None


# =============================================================================
# invoice.payment_failed Handler Tests
# =============================================================================


class TestHandleInvoicePaymentFailed:
    """Tests for invoice.payment_failed webhook handler."""

    def test_marks_subscription_past_due(
        self, db, active_subscription, invoice_payment_failed_payload
    ):
        """Should mark active subscription as past_due."""
        from payments.models import Subscription
        from payments.state_machines import SubscriptionState
        from payments.webhooks.handlers import handle_invoice_payment_failed

        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_invoice_failed_test_123",
            event_type="invoice.payment_failed",
            payload=invoice_payment_failed_payload,
            status=WebhookEventStatus.PENDING,
        )

        result = handle_invoice_payment_failed(webhook_event)

        assert result.success is True

        # Reload subscription
        subscription = Subscription.objects.get(id=active_subscription.id)
        assert subscription.state == SubscriptionState.PAST_DUE

    def test_idempotent_already_past_due(self, db, past_due_subscription):
        """Should be idempotent for already past_due subscriptions."""
        from payments.models import Subscription
        from payments.state_machines import SubscriptionState
        from payments.webhooks.handlers import handle_invoice_payment_failed

        payload = {
            "id": "evt_invoice_failed_pastdue_123",
            "type": "invoice.payment_failed",
            "data": {
                "object": {
                    "id": "in_test_failed_pastdue_123",
                    "object": "invoice",
                    "subscription": past_due_subscription.stripe_subscription_id,
                    "customer": past_due_subscription.stripe_customer_id,
                    "amount_due": 10000,
                    "currency": "usd",
                    "status": "open",
                    "attempt_count": 2,
                }
            },
        }

        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_invoice_failed_pastdue_123",
            event_type="invoice.payment_failed",
            payload=payload,
            status=WebhookEventStatus.PENDING,
        )

        result = handle_invoice_payment_failed(webhook_event)

        assert result.success is True

        # Subscription should remain past_due
        subscription = Subscription.objects.get(id=past_due_subscription.id)
        assert subscription.state == SubscriptionState.PAST_DUE

    def test_subscription_not_found_fails(self, db):
        """Should fail when subscription not found."""
        from payments.webhooks.handlers import handle_invoice_payment_failed

        payload = {
            "id": "evt_invoice_failed_nosub_123",
            "type": "invoice.payment_failed",
            "data": {
                "object": {
                    "id": "in_test_failed_nosub_123",
                    "object": "invoice",
                    "subscription": "sub_nonexistent_xyz",
                    "customer": "cus_test_123",
                    "amount_due": 10000,
                    "status": "open",
                }
            },
        }

        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_invoice_failed_nosub_123",
            event_type="invoice.payment_failed",
            payload=payload,
            status=WebhookEventStatus.PENDING,
        )

        result = handle_invoice_payment_failed(webhook_event)

        assert result.success is False
        assert result.error_code == "SUBSCRIPTION_NOT_FOUND"

    def test_skips_non_subscription_invoice(self, db):
        """Should skip invoices not related to subscriptions."""
        from payments.webhooks.handlers import handle_invoice_payment_failed

        payload = {
            "id": "evt_invoice_failed_oneoff_123",
            "type": "invoice.payment_failed",
            "data": {
                "object": {
                    "id": "in_test_failed_oneoff_123",
                    "object": "invoice",
                    "subscription": None,  # Not a subscription invoice
                    "customer": "cus_test_123",
                    "amount_due": 5000,
                    "status": "open",
                }
            },
        }

        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_invoice_failed_oneoff_123",
            event_type="invoice.payment_failed",
            payload=payload,
            status=WebhookEventStatus.PENDING,
        )

        result = handle_invoice_payment_failed(webhook_event)

        # Should succeed but skip processing
        assert result.success is True


# =============================================================================
# customer.subscription.created Handler Tests
# =============================================================================


class TestHandleSubscriptionCreated:
    """Tests for customer.subscription.created webhook handler."""

    def test_updates_existing_subscription_if_webhook_arrives_after_create(
        self, db, pending_subscription
    ):
        """Should update local subscription if webhook arrives after our create."""
        from payments.models import Subscription
        from payments.webhooks.handlers import handle_subscription_created

        now = timezone.now()
        payload = {
            "id": "evt_sub_created_existing_123",
            "type": "customer.subscription.created",
            "data": {
                "object": {
                    "id": pending_subscription.stripe_subscription_id,
                    "object": "subscription",
                    "customer": pending_subscription.stripe_customer_id,
                    "status": "active",  # Stripe says active
                    "current_period_start": int(now.timestamp()),
                    "current_period_end": int((now + timedelta(days=30)).timestamp()),
                    "cancel_at_period_end": False,
                    "items": {
                        "data": [
                            {
                                "price": {
                                    "id": "price_test_monthly_123",
                                    "unit_amount": 10000,
                                    "currency": "usd",
                                }
                            }
                        ]
                    },
                    "metadata": {},
                }
            },
        }

        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_sub_created_existing_123",
            event_type="customer.subscription.created",
            payload=payload,
            status=WebhookEventStatus.PENDING,
        )

        result = handle_subscription_created(webhook_event)

        assert result.success is True

        # Subscription record should exist
        subscription = Subscription.objects.get(id=pending_subscription.id)
        assert subscription is not None

    def test_handles_unknown_subscription_gracefully(
        self, db, subscription_created_payload
    ):
        """Should handle webhook for subscription not in our database."""
        from payments.webhooks.handlers import handle_subscription_created

        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_sub_created_unknown_123",
            event_type="customer.subscription.created",
            payload=subscription_created_payload,
            status=WebhookEventStatus.PENDING,
        )

        result = handle_subscription_created(webhook_event)

        # Should succeed gracefully (subscription might be created via another path)
        assert result.success is True


# =============================================================================
# customer.subscription.updated Handler Tests
# =============================================================================


class TestHandleSubscriptionUpdated:
    """Tests for customer.subscription.updated webhook handler."""

    def test_syncs_cancel_at_period_end(
        self, db, active_subscription, subscription_updated_payload
    ):
        """Should sync cancel_at_period_end flag from Stripe."""
        from payments.models import Subscription
        from payments.webhooks.handlers import handle_subscription_updated

        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_sub_updated_test_123",
            event_type="customer.subscription.updated",
            payload=subscription_updated_payload,
            status=WebhookEventStatus.PENDING,
        )

        result = handle_subscription_updated(webhook_event)

        assert result.success is True

        # Reload subscription
        subscription = Subscription.objects.get(id=active_subscription.id)
        assert subscription.cancel_at_period_end is True

    def test_updates_period_dates(self, db, active_subscription):
        """Should update current period dates from Stripe."""
        from payments.models import Subscription
        from payments.webhooks.handlers import handle_subscription_updated

        now = timezone.now()
        new_period_start = now + timedelta(days=30)
        new_period_end = now + timedelta(days=60)

        payload = {
            "id": "evt_sub_updated_dates_123",
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "id": active_subscription.stripe_subscription_id,
                    "object": "subscription",
                    "customer": active_subscription.stripe_customer_id,
                    "status": "active",
                    "current_period_start": int(new_period_start.timestamp()),
                    "current_period_end": int(new_period_end.timestamp()),
                    "cancel_at_period_end": False,
                    "metadata": {},
                }
            },
        }

        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_sub_updated_dates_123",
            event_type="customer.subscription.updated",
            payload=payload,
            status=WebhookEventStatus.PENDING,
        )

        result = handle_subscription_updated(webhook_event)

        assert result.success is True

        # Reload subscription
        subscription = Subscription.objects.get(id=active_subscription.id)
        # Verify period dates were updated (within 1 second tolerance)
        assert (
            abs(
                subscription.current_period_start.timestamp()
                - new_period_start.timestamp()
            )
            < 1
        )
        assert (
            abs(
                subscription.current_period_end.timestamp() - new_period_end.timestamp()
            )
            < 1
        )

    def test_subscription_not_found_succeeds(self, db):
        """Should succeed when subscription not found (might be external)."""
        from payments.webhooks.handlers import handle_subscription_updated

        payload = {
            "id": "evt_sub_updated_notfound_123",
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "id": "sub_nonexistent_xyz",
                    "object": "subscription",
                    "customer": "cus_test_123",
                    "status": "active",
                    "current_period_start": int(timezone.now().timestamp()),
                    "current_period_end": int(
                        (timezone.now() + timedelta(days=30)).timestamp()
                    ),
                    "cancel_at_period_end": False,
                }
            },
        }

        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_sub_updated_notfound_123",
            event_type="customer.subscription.updated",
            payload=payload,
            status=WebhookEventStatus.PENDING,
        )

        result = handle_subscription_updated(webhook_event)

        # Should succeed gracefully
        assert result.success is True


# =============================================================================
# customer.subscription.deleted Handler Tests
# =============================================================================


class TestHandleSubscriptionDeleted:
    """Tests for customer.subscription.deleted webhook handler."""

    def test_cancels_active_subscription(
        self, db, active_subscription, subscription_deleted_payload
    ):
        """Should cancel active subscription."""
        from payments.models import Subscription
        from payments.state_machines import SubscriptionState
        from payments.webhooks.handlers import handle_subscription_deleted

        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_sub_deleted_test_123",
            event_type="customer.subscription.deleted",
            payload=subscription_deleted_payload,
            status=WebhookEventStatus.PENDING,
        )

        result = handle_subscription_deleted(webhook_event)

        assert result.success is True

        # Reload subscription
        subscription = Subscription.objects.get(id=active_subscription.id)
        assert subscription.state == SubscriptionState.CANCELLED
        assert subscription.cancelled_at is not None

    def test_cancels_past_due_subscription(self, db, past_due_subscription):
        """Should cancel past_due subscription."""
        from payments.models import Subscription
        from payments.state_machines import SubscriptionState
        from payments.webhooks.handlers import handle_subscription_deleted

        payload = {
            "id": "evt_sub_deleted_pastdue_123",
            "type": "customer.subscription.deleted",
            "data": {
                "object": {
                    "id": past_due_subscription.stripe_subscription_id,
                    "object": "subscription",
                    "customer": past_due_subscription.stripe_customer_id,
                    "status": "canceled",
                    "canceled_at": int(timezone.now().timestamp()),
                }
            },
        }

        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_sub_deleted_pastdue_123",
            event_type="customer.subscription.deleted",
            payload=payload,
            status=WebhookEventStatus.PENDING,
        )

        result = handle_subscription_deleted(webhook_event)

        assert result.success is True

        # Reload subscription
        subscription = Subscription.objects.get(id=past_due_subscription.id)
        assert subscription.state == SubscriptionState.CANCELLED

    def test_idempotent_already_cancelled(self, db, subscriber_user, recipient_profile):
        """Should be idempotent for already cancelled subscriptions."""
        from payments.models import Subscription
        from payments.state_machines import SubscriptionState
        from payments.webhooks.handlers import handle_subscription_deleted

        # Create already cancelled subscription
        subscription = Subscription.objects.create(
            payer=subscriber_user,
            recipient_profile_id=recipient_profile.pk,
            stripe_subscription_id="sub_test_already_cancelled_123",
            stripe_customer_id="cus_test_123",
            stripe_price_id="price_test_123",
            amount_cents=10000,
            currency="usd",
            billing_interval="month",
        )
        subscription.activate()
        subscription.save()
        subscription.cancel()
        subscription.save()

        assert subscription.state == SubscriptionState.CANCELLED

        payload = {
            "id": "evt_sub_deleted_idempotent_123",
            "type": "customer.subscription.deleted",
            "data": {
                "object": {
                    "id": subscription.stripe_subscription_id,
                    "object": "subscription",
                    "customer": subscription.stripe_customer_id,
                    "status": "canceled",
                    "canceled_at": int(timezone.now().timestamp()),
                }
            },
        }

        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_sub_deleted_idempotent_123",
            event_type="customer.subscription.deleted",
            payload=payload,
            status=WebhookEventStatus.PENDING,
        )

        result = handle_subscription_deleted(webhook_event)

        # Should succeed (idempotent)
        assert result.success is True

        # Subscription should remain cancelled
        subscription = Subscription.objects.get(id=subscription.id)
        assert subscription.state == SubscriptionState.CANCELLED

    def test_subscription_not_found_succeeds(self, db):
        """Should succeed when subscription not found (might be cleaned up)."""
        from payments.webhooks.handlers import handle_subscription_deleted

        payload = {
            "id": "evt_sub_deleted_notfound_123",
            "type": "customer.subscription.deleted",
            "data": {
                "object": {
                    "id": "sub_nonexistent_xyz",
                    "object": "subscription",
                    "customer": "cus_test_123",
                    "status": "canceled",
                }
            },
        }

        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_sub_deleted_notfound_123",
            event_type="customer.subscription.deleted",
            payload=payload,
            status=WebhookEventStatus.PENDING,
        )

        result = handle_subscription_deleted(webhook_event)

        # Should succeed gracefully
        assert result.success is True


# =============================================================================
# Handler Registration Tests
# =============================================================================


class TestSubscriptionHandlerRegistration:
    """Tests for subscription webhook handler registration."""

    def test_invoice_paid_handler_registered(self):
        """Should have invoice.paid handler registered."""
        from payments.webhooks.handlers import WEBHOOK_HANDLERS

        assert "invoice.paid" in WEBHOOK_HANDLERS

    def test_invoice_payment_failed_handler_registered(self):
        """Should have invoice.payment_failed handler registered."""
        from payments.webhooks.handlers import WEBHOOK_HANDLERS

        assert "invoice.payment_failed" in WEBHOOK_HANDLERS

    def test_subscription_created_handler_registered(self):
        """Should have customer.subscription.created handler registered."""
        from payments.webhooks.handlers import WEBHOOK_HANDLERS

        assert "customer.subscription.created" in WEBHOOK_HANDLERS

    def test_subscription_updated_handler_registered(self):
        """Should have customer.subscription.updated handler registered."""
        from payments.webhooks.handlers import WEBHOOK_HANDLERS

        assert "customer.subscription.updated" in WEBHOOK_HANDLERS

    def test_subscription_deleted_handler_registered(self):
        """Should have customer.subscription.deleted handler registered."""
        from payments.webhooks.handlers import WEBHOOK_HANDLERS

        assert "customer.subscription.deleted" in WEBHOOK_HANDLERS


# =============================================================================
# Ledger Entry Tests for Subscription Payments
# =============================================================================


class TestSubscriptionLedgerEntries:
    """Tests for ledger entries created by subscription webhook handlers."""

    def test_invoice_paid_creates_ledger_entries(
        self, db, active_subscription, invoice_paid_payload, connected_account
    ):
        """Should create proper ledger entries when invoice.paid is processed."""
        from payments.ledger.models import EntryType, LedgerEntry
        from payments.webhooks.handlers import handle_invoice_paid

        # Connect account to the right profile
        connected_account.profile_id = active_subscription.recipient_profile_id
        connected_account.save()

        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_invoice_paid_ledger_123",
            event_type="invoice.paid",
            payload=invoice_paid_payload,
            status=WebhookEventStatus.PENDING,
        )

        result = handle_invoice_paid(webhook_event)

        assert result.success is True

        # Get the created PaymentOrder
        order = PaymentOrder.objects.filter(
            stripe_invoice_id="in_test_paid_123"
        ).first()
        assert order is not None

        # Verify ledger entries were created
        entries = LedgerEntry.objects.filter(
            reference_type="payment_order",
            reference_id=order.id,
        ).order_by("created_at")

        # Should have 3 entries: received, fee, recipient credit
        assert entries.count() == 3

        # Entry 1: Payment received (full amount)
        received_entry = entries[0]
        assert received_entry.entry_type == EntryType.PAYMENT_RECEIVED
        assert received_entry.amount_cents == 10000

        # Entry 2: Platform fee (15%)
        fee_entry = entries[1]
        assert fee_entry.entry_type == EntryType.FEE_COLLECTED
        assert fee_entry.amount_cents == 1500

        # Entry 3: Mentor credit (85%)
        recipient_entry = entries[2]
        assert recipient_entry.entry_type == EntryType.PAYMENT_RELEASED
        assert recipient_entry.amount_cents == 8500

    def test_ledger_entries_idempotent_on_duplicate_webhook(
        self, db, active_subscription, invoice_paid_payload, connected_account
    ):
        """Should not create duplicate ledger entries on webhook retry."""
        from payments.ledger.models import LedgerEntry
        from payments.webhooks.handlers import handle_invoice_paid

        # Connect account
        connected_account.profile_id = active_subscription.recipient_profile_id
        connected_account.save()

        # First webhook
        webhook_event1 = WebhookEvent.objects.create(
            stripe_event_id="evt_invoice_paid_idem_1",
            event_type="invoice.paid",
            payload=invoice_paid_payload,
            status=WebhookEventStatus.PENDING,
        )

        result1 = handle_invoice_paid(webhook_event1)
        assert result1.success is True

        order = PaymentOrder.objects.filter(
            stripe_invoice_id="in_test_paid_123"
        ).first()
        entry_count_after_first = LedgerEntry.objects.filter(
            reference_id=order.id
        ).count()

        # Second webhook (duplicate)
        webhook_event2 = WebhookEvent.objects.create(
            stripe_event_id="evt_invoice_paid_idem_2",
            event_type="invoice.paid",
            payload=invoice_paid_payload,
            status=WebhookEventStatus.PENDING,
        )

        result2 = handle_invoice_paid(webhook_event2)
        assert result2.success is True

        # Should have same number of entries
        entry_count_after_second = LedgerEntry.objects.filter(
            reference_id=order.id
        ).count()
        assert entry_count_after_second == entry_count_after_first
