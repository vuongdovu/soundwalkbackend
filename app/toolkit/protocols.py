"""
Protocol definitions (interfaces) for domain-specific services.

This module defines Protocol classes that specify interfaces
for domain-specific services like email, notifications, payments.

Protocols define contracts that services must fulfill, enabling:
- Duck typing with static type checking
- Dependency inversion (depend on abstractions, not concretions)
- Easy mocking in tests

Available Protocols:
    NotificationSender: Push notification interface
    EmailSender: Email sending interface
    PaymentProcessor: Payment operations interface
    MessageBroker: Pub/sub messaging interface

Usage:
    from toolkit.protocols import NotificationSender, EmailSender

    def send_notification(sender: NotificationSender, user_id: int):
        sender.send(user_id, "Title", "Body")

    class MyNotificationService:
        def send(self, user_id: int, title: str, body: str, **kwargs) -> bool:
            # Implementation
            return True

    # MyNotificationService is a valid NotificationSender
    # even without explicit inheritance (duck typing)
    sender: NotificationSender = MyNotificationService()

Note:
    - Protocols are primarily for type checking
    - @runtime_checkable allows isinstance() checks
    - AIProvider protocol is in ai/providers/base.py (AI-domain-specific)
    - These protocols are domain-specific contracts (email, payments, etc.)
    - For generic infrastructure protocols (CacheBackend), see core.protocols
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from typing import Any


@runtime_checkable
class NotificationSender(Protocol):
    """
    Protocol for notification sending services.

    Any class implementing this protocol can be used
    where NotificationSender is expected.

    Example:
        class FCMNotificationService:
            def send(self, user_id: int, title: str, body: str, **kwargs) -> bool:
                # Send via Firebase Cloud Messaging
                return True

        def notify_user(sender: NotificationSender, user_id: int):
            sender.send(user_id, "Hello", "You have a notification")
    """

    def send(
        self,
        user_id: int,
        title: str,
        body: str,
        **kwargs: Any,
    ) -> bool:
        """
        Send notification to user.

        Args:
            user_id: Recipient user ID
            title: Notification title
            body: Notification body
            **kwargs: Additional provider-specific options

        Returns:
            True if notification was sent successfully
        """
        ...


@runtime_checkable
class EmailSender(Protocol):
    """
    Protocol for email sending services.

    Any class implementing this protocol can be used
    where EmailSender is expected.

    Example:
        class SMTPEmailService:
            def send(self, to, subject, body_text, body_html=None, **kwargs) -> bool:
                # Send via SMTP
                return True

        def send_welcome_email(sender: EmailSender, email: str):
            sender.send(email, "Welcome!", "Thanks for signing up")
    """

    def send(
        self,
        to: str | list[str],
        subject: str,
        body_text: str,
        body_html: str | None = None,
        **kwargs: Any,
    ) -> bool:
        """
        Send email.

        Args:
            to: Recipient email address(es)
            subject: Email subject
            body_text: Plain text body
            body_html: HTML body (optional)
            **kwargs: Additional options (from_email, reply_to, etc.)

        Returns:
            True if email was sent successfully
        """
        ...


@runtime_checkable
class PaymentProcessor(Protocol):
    """
    Protocol for payment processing services.

    Defines the interface for payment operations.

    Example:
        class StripeProcessor:
            def create_customer(self, user_id, email): ...
            def create_subscription(self, customer_id, price_id, **kwargs): ...
            def cancel_subscription(self, subscription_id): ...
            def process_webhook(self, payload, signature): ...

        def setup_subscription(processor: PaymentProcessor, user, price_id):
            customer_id = processor.create_customer(user.id, user.email)
            return processor.create_subscription(customer_id, price_id)
    """

    def create_customer(self, user_id: int, email: str) -> str:
        """
        Create customer account.

        Args:
            user_id: Internal user ID
            email: Customer email

        Returns:
            External customer ID (e.g., Stripe customer ID)
        """
        ...

    def create_subscription(
        self,
        customer_id: str,
        price_id: str,
        **kwargs: Any,
    ) -> dict:
        """
        Create subscription.

        Args:
            customer_id: External customer ID
            price_id: Price/plan ID
            **kwargs: Additional options

        Returns:
            Subscription data dict
        """
        ...

    def cancel_subscription(self, subscription_id: str) -> bool:
        """
        Cancel subscription.

        Args:
            subscription_id: Subscription ID to cancel

        Returns:
            True if cancelled successfully
        """
        ...

    def process_webhook(self, payload: bytes, signature: str) -> dict:
        """
        Process webhook event.

        Args:
            payload: Raw webhook payload
            signature: Webhook signature for verification

        Returns:
            Parsed event data
        """
        ...


@runtime_checkable
class MessageBroker(Protocol):
    """
    Protocol for message broker services.

    Defines the interface for pub/sub messaging.

    Example:
        class RedisPubSub:
            def publish(self, channel, message): ...
            def subscribe(self, channel): ...
            def unsubscribe(self, channel): ...

        def broadcast_update(broker: MessageBroker, channel: str, data: dict):
            broker.publish(channel, data)
    """

    def publish(self, channel: str, message: dict) -> None:
        """
        Publish message to channel.

        Args:
            channel: Channel/topic name
            message: Message data (must be JSON-serializable)
        """
        ...

    def subscribe(self, channel: str) -> None:
        """
        Subscribe to channel.

        Args:
            channel: Channel/topic name to subscribe to
        """
        ...

    def unsubscribe(self, channel: str) -> None:
        """
        Unsubscribe from channel.

        Args:
            channel: Channel/topic name to unsubscribe from
        """
        ...
