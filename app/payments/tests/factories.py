"""
Factory Boy factories for payment test data.

This module provides factories for creating test instances of payment models.
Factories generate realistic test data while allowing easy customization.

Usage:
    from payments.tests.factories import (
        ConnectedAccountFactory,
        PaymentOrderFactory,
        PayoutFactory,
        RefundFactory,
        FundHoldFactory,
        WebhookEventFactory,
    )

    # Create a basic payment order
    order = PaymentOrderFactory()

    # Create a payment order in a specific state
    order = PaymentOrderFactory(state=PaymentOrderState.CAPTURED)

    # Create with specific payer
    order = PaymentOrderFactory(payer=user)
"""

import uuid

import factory
from django.utils import timezone

from payments.models import (
    ConnectedAccount,
    FundHold,
    PaymentOrder,
    Payout,
    Refund,
    WebhookEvent,
)
from payments.state_machines import (
    OnboardingStatus,
    PaymentStrategyType,
    WebhookEventStatus,
)


class UserFactory(factory.django.DjangoModelFactory):
    """
    Factory for creating User instances for payment tests.

    Note: This is a minimal factory for testing. In production tests,
    import from authentication.tests.factories if available.
    """

    class Meta:
        model = "authentication.User"
        skip_postgeneration_save = True

    email = factory.Sequence(lambda n: f"user{n}@example.com")
    password = factory.PostGenerationMethodCall("set_password", "testpass123")
    is_active = True


class ProfileFactory(factory.django.DjangoModelFactory):
    """
    Factory for creating Profile instances.

    Creates the associated user automatically.
    """

    class Meta:
        model = "authentication.Profile"
        skip_postgeneration_save = True

    user = factory.SubFactory(UserFactory)


class ConnectedAccountFactory(factory.django.DjangoModelFactory):
    """
    Factory for creating ConnectedAccount instances.

    Default creates a connected account with COMPLETE onboarding status.

    Example:
        # Default complete account
        account = ConnectedAccountFactory()

        # In-progress onboarding
        account = ConnectedAccountFactory(
            onboarding_status=OnboardingStatus.IN_PROGRESS,
            payouts_enabled=False,
        )
    """

    class Meta:
        model = ConnectedAccount
        skip_postgeneration_save = True

    profile = factory.SubFactory(ProfileFactory)
    stripe_account_id = factory.Sequence(
        lambda n: f"acct_test_{n}_{uuid.uuid4().hex[:8]}"
    )
    onboarding_status = OnboardingStatus.COMPLETE
    payouts_enabled = True
    charges_enabled = True
    metadata = factory.LazyFunction(dict)


class PaymentOrderFactory(factory.django.DjangoModelFactory):
    """
    Factory for creating PaymentOrder instances.

    Default creates a DRAFT payment order for $100 USD.

    Example:
        # Default draft order
        order = PaymentOrderFactory()

        # Escrow order
        order = PaymentOrderFactory(
            strategy_type=PaymentStrategyType.ESCROW,
        )

        # Order with specific state (use with caution - use fixtures for state testing)
        order = PaymentOrderFactory(state=PaymentOrderState.PENDING)
    """

    class Meta:
        model = PaymentOrder
        skip_postgeneration_save = True

    payer = factory.SubFactory(UserFactory)
    amount_cents = 10000  # $100.00
    currency = "usd"
    strategy_type = PaymentStrategyType.DIRECT
    # Note: state is managed by FSM, default is DRAFT
    # Only use for initial creation, not for testing state transitions
    metadata = factory.LazyFunction(dict)


class PayoutFactory(factory.django.DjangoModelFactory):
    """
    Factory for creating Payout instances.

    Default creates a PENDING payout for $90 USD.

    Example:
        # Default pending payout
        payout = PayoutFactory()

        # Payout with specific order and account
        payout = PayoutFactory(
            payment_order=order,
            connected_account=account,
            amount_cents=5000,
        )
    """

    class Meta:
        model = Payout
        skip_postgeneration_save = True

    payment_order = factory.SubFactory(PaymentOrderFactory)
    connected_account = factory.SubFactory(ConnectedAccountFactory)
    amount_cents = 9000  # $90.00 (after platform fee)
    currency = "usd"
    # Note: state is managed by FSM, default is PENDING
    metadata = factory.LazyFunction(dict)


class RefundFactory(factory.django.DjangoModelFactory):
    """
    Factory for creating Refund instances.

    Default creates a REQUESTED refund for $50 USD.

    Example:
        # Default requested refund
        refund = RefundFactory()

        # Full refund
        refund = RefundFactory(
            payment_order=order,
            amount_cents=order.amount_cents,
            reason="Customer request",
        )
    """

    class Meta:
        model = Refund
        skip_postgeneration_save = True

    payment_order = factory.SubFactory(PaymentOrderFactory)
    amount_cents = 5000  # $50.00 partial refund
    currency = "usd"
    reason = factory.Faker("sentence")
    # Note: state is managed by FSM, default is REQUESTED
    metadata = factory.LazyFunction(dict)


class FundHoldFactory(factory.django.DjangoModelFactory):
    """
    Factory for creating FundHold instances.

    Default creates an unreleased fund hold expiring in 7 days.

    Example:
        # Default fund hold
        hold = FundHoldFactory()

        # Released fund hold
        hold = FundHoldFactory(
            released=True,
            released_at=timezone.now(),
        )

        # Expired hold
        hold = FundHoldFactory(
            expires_at=timezone.now() - timezone.timedelta(days=1),
        )
    """

    class Meta:
        model = FundHold
        skip_postgeneration_save = True

    payment_order = factory.SubFactory(PaymentOrderFactory)
    amount_cents = 10000  # $100.00
    currency = "usd"
    expires_at = factory.LazyFunction(
        lambda: timezone.now() + timezone.timedelta(days=7)
    )
    released = False
    metadata = factory.LazyFunction(dict)


class WebhookEventFactory(factory.django.DjangoModelFactory):
    """
    Factory for creating WebhookEvent instances.

    Default creates a PENDING payment_intent.succeeded webhook.

    Example:
        # Default webhook
        event = WebhookEventFactory()

        # Specific event type
        event = WebhookEventFactory(
            event_type="refund.created",
            payload={"data": {"object": {...}}},
        )

        # Failed webhook
        event = WebhookEventFactory(
            status=WebhookEventStatus.FAILED,
            error_message="Processing error",
            retry_count=3,
        )
    """

    class Meta:
        model = WebhookEvent
        skip_postgeneration_save = True

    stripe_event_id = factory.Sequence(lambda n: f"evt_test_{n}_{uuid.uuid4().hex[:8]}")
    event_type = "payment_intent.succeeded"
    payload = factory.LazyFunction(
        lambda: {
            "id": f"evt_{uuid.uuid4().hex}",
            "type": "payment_intent.succeeded",
            "data": {
                "object": {
                    "id": f"pi_{uuid.uuid4().hex}",
                    "object": "payment_intent",
                    "amount": 10000,
                    "currency": "usd",
                    "status": "succeeded",
                }
            },
        }
    )
    status = WebhookEventStatus.PENDING
