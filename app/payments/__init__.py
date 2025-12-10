"""
Payments app for Stripe integration.

This app handles:
- Stripe customer management
- Subscription lifecycle (create, update, cancel)
- Payment processing and transactions
- Webhook event handling
- Billing portal integration

Related apps:
    - authentication: User model for customer association
    - notifications: Payment event notifications

Usage:
    from payments.services import StripeService

    # Create subscription
    subscription = StripeService.create_subscription(user, price_id)

    # Handle webhook
    StripeService.process_webhook_event(event_id, event_type, payload)
"""
