"""
Integration tests for the full subscription lifecycle.

Tests the complete subscription lifecycle:
1. Subscription creation creates local Subscription in PENDING state
2. First invoice.paid webhook activates subscription and creates PaymentOrder
3. Subsequent invoice.paid webhooks create renewal PaymentOrders
4. Ledger entries accumulate mentor's USER_BALANCE
5. Monthly payout aggregates subscription revenue
6. Subscription cancellation marks subscription as CANCELLED

These tests verify that all components work together correctly.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from payments.ledger import LedgerService
from payments.ledger.models import AccountType, LedgerEntry
from payments.models import PaymentOrder
from payments.state_machines import (
    OnboardingStatus,
    PaymentOrderState,
    PaymentStrategyType,
    WebhookEventStatus,
)
from payments.tests.factories import (
    ConnectedAccountFactory,
    UserFactory,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def subscriber(db):
    """Create a subscriber user."""
    return UserFactory(email="subscriber@integration.test")


@pytest.fixture
def mentor(db):
    """Create a mentor user."""
    return UserFactory(email="mentor@integration.test")


@pytest.fixture
def mentor_profile(db, mentor):
    """Get the mentor's profile."""
    from authentication.models import Profile

    return Profile.objects.get(user=mentor)


@pytest.fixture
def mentor_connected_account(db, mentor_profile):
    """Create a connected account for the mentor ready for payouts."""
    return ConnectedAccountFactory(
        profile=mentor_profile,
        onboarding_status=OnboardingStatus.COMPLETE,
        payouts_enabled=True,
        charges_enabled=True,
    )


@pytest.fixture
def mock_stripe_adapter():
    """Mock Stripe adapter for subscription operations."""
    mock = MagicMock()

    # Mock customer methods (use explicit attribute assignment to avoid MagicMock issues)
    customer_result = MagicMock()
    customer_result.id = "cus_integration_test_123"
    customer_result.email = "subscriber@integration.test"
    mock.create_customer.return_value = customer_result
    mock.get_or_create_customer.return_value = customer_result

    # Mock subscription creation (explicit attribute assignment)
    subscription_result = MagicMock()
    subscription_result.id = "sub_integration_test_123"
    subscription_result.status = "active"
    subscription_result.customer_id = "cus_integration_test_123"
    subscription_result.current_period_start = int(timezone.now().timestamp())
    subscription_result.current_period_end = int(
        (timezone.now() + timedelta(days=30)).timestamp()
    )
    subscription_result.cancel_at_period_end = False
    subscription_result.latest_invoice_id = "in_integration_first_123"
    mock.create_subscription.return_value = subscription_result

    # Mock subscription cancellation
    cancelled_result = MagicMock()
    cancelled_result.id = "sub_integration_test_123"
    cancelled_result.status = "canceled"
    cancelled_result.cancel_at_period_end = False
    cancelled_result.canceled_at = int(timezone.now().timestamp())
    mock.cancel_subscription.return_value = cancelled_result

    return mock


@pytest.fixture
def pending_subscription(db, subscriber, mentor_profile):
    """Create a subscription in PENDING state awaiting first payment."""
    from payments.models import Subscription

    return Subscription.objects.create(
        payer=subscriber,
        recipient_profile_id=mentor_profile.pk,
        stripe_subscription_id="sub_integration_test_123",
        stripe_customer_id="cus_integration_test_123",
        stripe_price_id="price_monthly_10000",
        amount_cents=10000,
        currency="usd",
        billing_interval="month",
        current_period_start=timezone.now(),
        current_period_end=timezone.now() + timedelta(days=30),
    )


@pytest.fixture
def active_subscription(db, subscriber, mentor_profile):
    """Create an active subscription."""
    from payments.models import Subscription

    subscription = Subscription.objects.create(
        payer=subscriber,
        recipient_profile_id=mentor_profile.pk,
        stripe_subscription_id="sub_integration_active_123",
        stripe_customer_id="cus_integration_test_123",
        stripe_price_id="price_monthly_10000",
        amount_cents=10000,
        currency="usd",
        billing_interval="month",
        current_period_start=timezone.now(),
        current_period_end=timezone.now() + timedelta(days=30),
    )
    subscription.activate()
    subscription.save()
    return subscription


# =============================================================================
# Full Lifecycle Integration Tests
# =============================================================================


@pytest.mark.django_db
class TestSubscriptionLifecycleIntegration:
    """Integration tests for the complete subscription lifecycle."""

    def test_full_subscription_lifecycle_create_renew_cancel(
        self,
        subscriber,
        mentor_profile,
        mentor_connected_account,
        mock_stripe_adapter,
    ):
        """
        Test complete subscription lifecycle: create → renew → cancel.

        Flow:
        1. Create subscription (PENDING state)
        2. First invoice.paid webhook activates subscription
        3. PaymentOrder created and settled
        4. Simulate 3 renewals (monthly billing cycles)
        5. Verify mentor balance accumulates correctly
        6. Cancel subscription
        7. Verify subscription is CANCELLED
        """
        from payments.models import Subscription
        from payments.state_machines import SubscriptionState
        from payments.strategies.subscription import (
            CreateSubscriptionParams,
            SubscriptionPaymentStrategy,
        )
        from payments.webhooks.handlers import (
            handle_invoice_paid,
            handle_subscription_deleted,
        )

        # Step 1: Create subscription
        strategy = SubscriptionPaymentStrategy(stripe_adapter=mock_stripe_adapter)

        create_params = CreateSubscriptionParams(
            payer=subscriber,
            recipient_profile_id=mentor_profile.pk,
            price_id="price_monthly_10000",
            amount_cents=10000,
            currency="usd",
            billing_interval="month",
        )

        result = strategy.create_subscription(create_params)

        assert result.success is True
        subscription = result.data.subscription
        assert subscription.state == SubscriptionState.PENDING

        # Step 2: Simulate first invoice.paid webhook
        from payments.models import WebhookEvent

        first_invoice_payload = {
            "id": "evt_first_invoice_paid",
            "type": "invoice.paid",
            "data": {
                "object": {
                    "id": "in_first_payment_123",
                    "object": "invoice",
                    "subscription": subscription.stripe_subscription_id,
                    "customer": subscription.stripe_customer_id,
                    "amount_paid": 10000,
                    "currency": "usd",
                    "status": "paid",
                    "billing_reason": "subscription_create",
                    "period_start": int(timezone.now().timestamp()),
                    "period_end": int(
                        (timezone.now() + timedelta(days=30)).timestamp()
                    ),
                    "lines": {"data": []},
                }
            },
        }

        webhook_event = WebhookEvent.objects.create(
            stripe_event_id="evt_first_invoice_paid",
            event_type="invoice.paid",
            payload=first_invoice_payload,
            status=WebhookEventStatus.PENDING,
        )

        invoice_result = handle_invoice_paid(webhook_event)
        assert invoice_result.success is True

        # Verify subscription is now ACTIVE (get fresh instance to avoid FSM issues)
        subscription = Subscription.objects.get(id=subscription.id)
        assert subscription.state == SubscriptionState.ACTIVE

        # Verify first PaymentOrder was created
        first_order = PaymentOrder.objects.filter(
            stripe_invoice_id="in_first_payment_123"
        ).first()
        assert first_order is not None
        assert first_order.state == PaymentOrderState.SETTLED
        assert first_order.subscription == subscription

        # Step 3: Simulate 2 more renewal payments
        for i in range(2):
            invoice_id = f"in_renewal_{i}_123"
            renewal_payload = {
                "id": f"evt_renewal_{i}",
                "type": "invoice.paid",
                "data": {
                    "object": {
                        "id": invoice_id,
                        "object": "invoice",
                        "subscription": subscription.stripe_subscription_id,
                        "customer": subscription.stripe_customer_id,
                        "amount_paid": 10000,
                        "currency": "usd",
                        "status": "paid",
                        "billing_reason": "subscription_cycle",
                        "period_start": int(
                            (timezone.now() + timedelta(days=30 * (i + 1))).timestamp()
                        ),
                        "period_end": int(
                            (timezone.now() + timedelta(days=30 * (i + 2))).timestamp()
                        ),
                        "lines": {"data": []},
                    }
                },
            }

            webhook = WebhookEvent.objects.create(
                stripe_event_id=f"evt_renewal_{i}",
                event_type="invoice.paid",
                payload=renewal_payload,
                status=WebhookEventStatus.PENDING,
            )

            renewal_result = handle_invoice_paid(webhook)
            assert renewal_result.success is True

        # Step 4: Verify all PaymentOrders created
        total_orders = PaymentOrder.objects.filter(subscription=subscription).count()
        assert total_orders == 3  # 1 first payment + 2 renewals

        # Step 5: Verify mentor's accumulated balance
        mentor_account = LedgerService.get_or_create_account(
            AccountType.USER_BALANCE,
            owner_id=mentor_profile.pk,
            currency="usd",
        )
        balance = LedgerService.get_balance(mentor_account.id)

        # 3 payments × $100 × 85% (after 15% fee) = $255.00 = 25500 cents
        assert balance.cents == 25500

        # Step 6: Cancel subscription
        cancel_payload = {
            "id": "evt_subscription_deleted",
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

        cancel_webhook = WebhookEvent.objects.create(
            stripe_event_id="evt_subscription_deleted",
            event_type="customer.subscription.deleted",
            payload=cancel_payload,
            status=WebhookEventStatus.PENDING,
        )

        cancel_result = handle_subscription_deleted(cancel_webhook)
        assert cancel_result.success is True

        # Step 7: Verify subscription is cancelled (fresh instance)
        subscription = Subscription.objects.get(id=subscription.id)
        assert subscription.state == SubscriptionState.CANCELLED
        assert subscription.cancelled_at is not None

    def test_multiple_renewals_accumulate_in_user_balance(
        self,
        active_subscription,
        mentor_profile,
        mentor_connected_account,
    ):
        """Test that subscription renewals accumulate in mentor's USER_BALANCE."""
        from payments.webhooks.handlers import handle_invoice_paid
        from payments.models import WebhookEvent

        # Process 5 monthly renewals
        for i in range(5):
            invoice_id = f"in_multi_renewal_{i}_123"
            payload = {
                "id": f"evt_multi_renewal_{i}",
                "type": "invoice.paid",
                "data": {
                    "object": {
                        "id": invoice_id,
                        "object": "invoice",
                        "subscription": active_subscription.stripe_subscription_id,
                        "customer": active_subscription.stripe_customer_id,
                        "amount_paid": 10000,
                        "currency": "usd",
                        "status": "paid",
                        "billing_reason": "subscription_cycle",
                        "period_start": int(
                            (timezone.now() + timedelta(days=30 * i)).timestamp()
                        ),
                        "period_end": int(
                            (timezone.now() + timedelta(days=30 * (i + 1))).timestamp()
                        ),
                        "lines": {"data": []},
                    }
                },
            }

            webhook = WebhookEvent.objects.create(
                stripe_event_id=f"evt_multi_renewal_{i}",
                event_type="invoice.paid",
                payload=payload,
                status=WebhookEventStatus.PENDING,
            )

            result = handle_invoice_paid(webhook)
            assert result.success is True

        # Verify accumulated balance
        mentor_account = LedgerService.get_or_create_account(
            AccountType.USER_BALANCE,
            owner_id=mentor_profile.pk,
            currency="usd",
        )
        balance = LedgerService.get_balance(mentor_account.id)

        # 5 payments × $100 × 85% = $425.00 = 42500 cents
        assert balance.cents == 42500

        # Verify ledger entries count
        # Each payment creates 3 entries: received, fee, release
        entries = LedgerEntry.objects.filter(
            reference_type="payment_order",
        ).count()
        assert entries == 15  # 5 payments × 3 entries each

    def test_monthly_payout_aggregates_subscription_revenue(
        self,
        active_subscription,
        mentor_profile,
        mentor_connected_account,
    ):
        """Test that monthly payout task aggregates subscription revenue."""
        from payments.webhooks.handlers import handle_invoice_paid
        from payments.models import Payout, WebhookEvent
        from payments.tasks import create_monthly_subscription_payouts

        # Process 3 subscription payments
        for i in range(3):
            invoice_id = f"in_payout_test_{i}_123"
            payload = {
                "id": f"evt_payout_test_{i}",
                "type": "invoice.paid",
                "data": {
                    "object": {
                        "id": invoice_id,
                        "object": "invoice",
                        "subscription": active_subscription.stripe_subscription_id,
                        "customer": active_subscription.stripe_customer_id,
                        "amount_paid": 10000,
                        "currency": "usd",
                        "status": "paid",
                        "billing_reason": "subscription_cycle",
                        "lines": {"data": []},
                    }
                },
            }

            webhook = WebhookEvent.objects.create(
                stripe_event_id=f"evt_payout_test_{i}",
                event_type="invoice.paid",
                payload=payload,
                status=WebhookEventStatus.PENDING,
            )

            result = handle_invoice_paid(webhook)
            assert result.success is True

        # Verify mentor balance before payout
        mentor_account = LedgerService.get_or_create_account(
            AccountType.USER_BALANCE,
            owner_id=mentor_profile.pk,
            currency="usd",
        )
        balance_before = LedgerService.get_balance(mentor_account.id)

        # 3 payments × $85 = $255.00 = 25500 cents
        assert balance_before.cents == 25500

        # Run monthly payout task (mock execute_single_payout to prevent Stripe call)
        with patch("payments.tasks.execute_single_payout") as mock_execute:
            result = create_monthly_subscription_payouts()

            # Verify payout was created for the mentor
            assert result["payouts_created"] >= 1

            # Verify execute_single_payout was queued
            assert mock_execute.delay.called

        # Verify Payout model was created with correct amount
        new_payouts = Payout.objects.filter(connected_account__profile=mentor_profile)
        assert new_payouts.count() > 0
        payout = new_payouts.first()
        assert payout.amount_cents == 25500


@pytest.mark.django_db
class TestSubscriptionPaymentFailureRecovery:
    """Integration tests for subscription payment failure and recovery."""

    def test_payment_failure_marks_past_due_and_recovery_reactivates(
        self,
        active_subscription,
        mentor_profile,
        mentor_connected_account,
    ):
        """
        Test payment failure recovery flow.

        Flow:
        1. Active subscription receives payment failure
        2. Subscription becomes PAST_DUE
        3. Stripe retries payment (Smart Retries)
        4. Payment succeeds, subscription reactivates
        """
        from payments.models import Subscription, WebhookEvent
        from payments.state_machines import SubscriptionState
        from payments.webhooks.handlers import (
            handle_invoice_paid,
            handle_invoice_payment_failed,
        )

        # Step 1: Simulate payment failure
        failed_payload = {
            "id": "evt_payment_failed_recovery",
            "type": "invoice.payment_failed",
            "data": {
                "object": {
                    "id": "in_failed_recovery_123",
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
                }
            },
        }

        failed_webhook = WebhookEvent.objects.create(
            stripe_event_id="evt_payment_failed_recovery",
            event_type="invoice.payment_failed",
            payload=failed_payload,
            status=WebhookEventStatus.PENDING,
        )

        fail_result = handle_invoice_payment_failed(failed_webhook)
        assert fail_result.success is True

        # Step 2: Verify subscription is PAST_DUE (fresh instance)
        active_subscription = Subscription.objects.get(id=active_subscription.id)
        assert active_subscription.state == SubscriptionState.PAST_DUE

        # Step 3: Simulate successful retry payment
        success_payload = {
            "id": "evt_payment_recovery",
            "type": "invoice.paid",
            "data": {
                "object": {
                    "id": "in_recovery_success_123",
                    "object": "invoice",
                    "subscription": active_subscription.stripe_subscription_id,
                    "customer": active_subscription.stripe_customer_id,
                    "amount_paid": 10000,
                    "currency": "usd",
                    "status": "paid",
                    "billing_reason": "subscription_cycle",
                    "lines": {"data": []},
                }
            },
        }

        success_webhook = WebhookEvent.objects.create(
            stripe_event_id="evt_payment_recovery",
            event_type="invoice.paid",
            payload=success_payload,
            status=WebhookEventStatus.PENDING,
        )

        success_result = handle_invoice_paid(success_webhook)
        assert success_result.success is True

        # Step 4: Verify subscription is reactivated (fresh instance)
        active_subscription = Subscription.objects.get(id=active_subscription.id)
        assert active_subscription.state == SubscriptionState.ACTIVE

        # Verify PaymentOrder was created
        order = PaymentOrder.objects.filter(
            stripe_invoice_id="in_recovery_success_123"
        ).first()
        assert order is not None
        assert order.state == PaymentOrderState.SETTLED


@pytest.mark.django_db
class TestSubscriptionEdgeCases:
    """Integration tests for subscription edge cases."""

    def test_renewal_with_inactive_connected_account(
        self,
        active_subscription,
        mentor_profile,
    ):
        """Test renewal handling when connected account is disabled."""
        from payments.models import WebhookEvent
        from payments.webhooks.handlers import handle_invoice_paid

        # Create disabled connected account (assigned to _ to indicate side-effect creation)
        ConnectedAccountFactory(
            profile=mentor_profile,
            onboarding_status=OnboardingStatus.REJECTED,
            payouts_enabled=False,
            charges_enabled=False,
        )

        payload = {
            "id": "evt_renewal_disabled_account",
            "type": "invoice.paid",
            "data": {
                "object": {
                    "id": "in_renewal_disabled_123",
                    "object": "invoice",
                    "subscription": active_subscription.stripe_subscription_id,
                    "customer": active_subscription.stripe_customer_id,
                    "amount_paid": 10000,
                    "currency": "usd",
                    "status": "paid",
                    "billing_reason": "subscription_cycle",
                    "lines": {"data": []},
                }
            },
        }

        webhook = WebhookEvent.objects.create(
            stripe_event_id="evt_renewal_disabled_account",
            event_type="invoice.paid",
            payload=payload,
            status=WebhookEventStatus.PENDING,
        )

        # Should still process the payment (ledger handles the funds)
        result = handle_invoice_paid(webhook)

        # Payment should succeed - funds go to USER_BALANCE
        # Payout will fail later when attempted
        assert result.success is True

        # PaymentOrder should be created
        order = PaymentOrder.objects.filter(
            stripe_invoice_id="in_renewal_disabled_123"
        ).first()
        assert order is not None
        assert order.state == PaymentOrderState.SETTLED

    def test_subscription_cancellation_mid_period(
        self,
        active_subscription,
        mentor_profile,
        mentor_connected_account,
    ):
        """Test subscription cancellation in the middle of billing period."""
        from payments.models import Subscription, WebhookEvent
        from payments.state_machines import SubscriptionState
        from payments.webhooks.handlers import (
            handle_invoice_paid,
            handle_subscription_updated,
            handle_subscription_deleted,
        )

        # Process a renewal payment first
        invoice_payload = {
            "id": "evt_mid_period_payment",
            "type": "invoice.paid",
            "data": {
                "object": {
                    "id": "in_mid_period_123",
                    "object": "invoice",
                    "subscription": active_subscription.stripe_subscription_id,
                    "customer": active_subscription.stripe_customer_id,
                    "amount_paid": 10000,
                    "currency": "usd",
                    "status": "paid",
                    "billing_reason": "subscription_cycle",
                    "lines": {"data": []},
                }
            },
        }

        invoice_webhook = WebhookEvent.objects.create(
            stripe_event_id="evt_mid_period_payment",
            event_type="invoice.paid",
            payload=invoice_payload,
            status=WebhookEventStatus.PENDING,
        )

        handle_invoice_paid(invoice_webhook)

        # User requests cancellation at period end
        update_payload = {
            "id": "evt_cancel_at_period_end",
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "id": active_subscription.stripe_subscription_id,
                    "object": "subscription",
                    "customer": active_subscription.stripe_customer_id,
                    "status": "active",
                    "cancel_at_period_end": True,
                    "current_period_start": int(timezone.now().timestamp()),
                    "current_period_end": int(
                        (timezone.now() + timedelta(days=15)).timestamp()
                    ),
                    "canceled_at": int(timezone.now().timestamp()),
                }
            },
        }

        update_webhook = WebhookEvent.objects.create(
            stripe_event_id="evt_cancel_at_period_end",
            event_type="customer.subscription.updated",
            payload=update_payload,
            status=WebhookEventStatus.PENDING,
        )

        handle_subscription_updated(update_webhook)

        # Verify subscription is still ACTIVE but flagged for cancellation (fresh instance)
        active_subscription = Subscription.objects.get(id=active_subscription.id)
        assert active_subscription.state == SubscriptionState.ACTIVE
        assert active_subscription.cancel_at_period_end is True

        # At period end, subscription is deleted
        delete_payload = {
            "id": "evt_period_end_delete",
            "type": "customer.subscription.deleted",
            "data": {
                "object": {
                    "id": active_subscription.stripe_subscription_id,
                    "object": "subscription",
                    "customer": active_subscription.stripe_customer_id,
                    "status": "canceled",
                    "canceled_at": int(timezone.now().timestamp()),
                }
            },
        }

        delete_webhook = WebhookEvent.objects.create(
            stripe_event_id="evt_period_end_delete",
            event_type="customer.subscription.deleted",
            payload=delete_payload,
            status=WebhookEventStatus.PENDING,
        )

        handle_subscription_deleted(delete_webhook)

        # Verify subscription is CANCELLED (fresh instance)
        active_subscription = Subscription.objects.get(id=active_subscription.id)
        assert active_subscription.state == SubscriptionState.CANCELLED

        # Mentor should still have their earned balance
        mentor_account = LedgerService.get_or_create_account(
            AccountType.USER_BALANCE,
            owner_id=mentor_profile.pk,
            currency="usd",
        )
        balance = LedgerService.get_balance(mentor_account.id)
        assert balance.cents == 8500  # $85 (85% of $100)

    def test_duplicate_webhook_handling(
        self,
        active_subscription,
        mentor_profile,
        mentor_connected_account,
    ):
        """Test that duplicate webhooks are handled idempotently."""
        from payments.models import WebhookEvent
        from payments.webhooks.handlers import handle_invoice_paid

        invoice_id = "in_duplicate_test_123"
        payload = {
            "id": "evt_duplicate_webhook",
            "type": "invoice.paid",
            "data": {
                "object": {
                    "id": invoice_id,
                    "object": "invoice",
                    "subscription": active_subscription.stripe_subscription_id,
                    "customer": active_subscription.stripe_customer_id,
                    "amount_paid": 10000,
                    "currency": "usd",
                    "status": "paid",
                    "billing_reason": "subscription_cycle",
                    "lines": {"data": []},
                }
            },
        }

        # First webhook
        webhook1 = WebhookEvent.objects.create(
            stripe_event_id="evt_duplicate_1",
            event_type="invoice.paid",
            payload=payload,
            status=WebhookEventStatus.PENDING,
        )

        result1 = handle_invoice_paid(webhook1)
        assert result1.success is True

        orders_after_first = PaymentOrder.objects.filter(
            stripe_invoice_id=invoice_id
        ).count()
        assert orders_after_first == 1

        # Second webhook (duplicate)
        webhook2 = WebhookEvent.objects.create(
            stripe_event_id="evt_duplicate_2",
            event_type="invoice.paid",
            payload=payload,
            status=WebhookEventStatus.PENDING,
        )

        result2 = handle_invoice_paid(webhook2)
        # Should succeed (idempotent)
        assert result2.success is True

        # No duplicate PaymentOrders
        orders_after_second = PaymentOrder.objects.filter(
            stripe_invoice_id=invoice_id
        ).count()
        assert orders_after_second == 1

        # No duplicate ledger entries
        order = PaymentOrder.objects.get(stripe_invoice_id=invoice_id)
        entries = LedgerEntry.objects.filter(
            reference_type="payment_order",
            reference_id=order.id,
        ).count()
        assert entries == 3  # received, fee, release


@pytest.mark.django_db
class TestSubscriptionLedgerConsistency:
    """Integration tests for ledger consistency in subscription payments."""

    def test_ledger_balances_after_multiple_subscriptions(
        self,
        mentor_profile,
        mentor_connected_account,
    ):
        """Test ledger consistency with multiple concurrent subscriptions."""
        from payments.models import Subscription, WebhookEvent
        from payments.webhooks.handlers import handle_invoice_paid

        # Create 3 subscribers with subscriptions to the same mentor
        subscriptions = []
        for i in range(3):
            subscriber = UserFactory(email=f"sub_{i}@test.com")
            subscription = Subscription.objects.create(
                payer=subscriber,
                recipient_profile_id=mentor_profile.pk,
                stripe_subscription_id=f"sub_multi_{i}_123",
                stripe_customer_id=f"cus_multi_{i}_123",
                stripe_price_id="price_monthly_10000",
                amount_cents=10000,
                currency="usd",
                billing_interval="month",
                current_period_start=timezone.now(),
                current_period_end=timezone.now() + timedelta(days=30),
            )
            subscription.activate()
            subscription.save()
            subscriptions.append(subscription)

        # Each subscriber makes 2 payments
        for subscription in subscriptions:
            for j in range(2):
                invoice_id = f"in_{subscription.id}_{j}"
                payload = {
                    "id": f"evt_{subscription.id}_{j}",
                    "type": "invoice.paid",
                    "data": {
                        "object": {
                            "id": invoice_id,
                            "object": "invoice",
                            "subscription": subscription.stripe_subscription_id,
                            "customer": subscription.stripe_customer_id,
                            "amount_paid": 10000,
                            "currency": "usd",
                            "status": "paid",
                            "billing_reason": "subscription_cycle",
                            "lines": {"data": []},
                        }
                    },
                }

                webhook = WebhookEvent.objects.create(
                    stripe_event_id=f"evt_{subscription.id}_{j}",
                    event_type="invoice.paid",
                    payload=payload,
                    status=WebhookEventStatus.PENDING,
                )

                result = handle_invoice_paid(webhook)
                assert result.success is True

        # Total payments: 3 subscribers × 2 payments = 6 payments
        total_orders = PaymentOrder.objects.filter(
            strategy_type=PaymentStrategyType.SUBSCRIPTION
        ).count()
        assert total_orders == 6

        # Verify mentor's total balance
        mentor_account = LedgerService.get_or_create_account(
            AccountType.USER_BALANCE,
            owner_id=mentor_profile.pk,
            currency="usd",
        )
        balance = LedgerService.get_balance(mentor_account.id)

        # 6 payments × $100 × 85% = $510.00 = 51000 cents
        assert balance.cents == 51000

        # Verify platform revenue
        platform_revenue = LedgerService.get_or_create_account(
            AccountType.PLATFORM_REVENUE,
            owner_id=None,
            currency="usd",
        )
        revenue_balance = LedgerService.get_balance(platform_revenue.id)

        # 6 payments × $100 × 15% = $90.00 = 9000 cents
        assert revenue_balance.cents == 9000

        # Verify total entries
        # Each payment: 3 entries (received, fee, release) = 6 × 3 = 18
        total_entries = LedgerEntry.objects.filter(
            reference_type="payment_order"
        ).count()
        assert total_entries == 18
