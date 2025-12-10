"""
Protocol definitions (interfaces) for type checking.

This module defines Protocol classes that specify interfaces
for dependency injection and type hints without runtime overhead.

Usage:
    from utils.protocols import NotificationSender

    def send_notification(sender: NotificationSender, user_id: int):
        sender.send(user_id, "Title", "Body")

Note:
    Protocols are for type checking only (TYPE_CHECKING).
    They have no runtime effect.
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
    """

    def send(
        self,
        user_id: int,
        title: str,
        body: str,
        **kwargs: Any,
    ) -> bool:
        """Send notification to user."""
        ...


@runtime_checkable
class EmailSender(Protocol):
    """
    Protocol for email sending services.

    Any class implementing this protocol can be used
    where EmailSender is expected.
    """

    def send(
        self,
        to: str | list[str],
        subject: str,
        body_text: str,
        body_html: str | None = None,
        **kwargs: Any,
    ) -> bool:
        """Send email."""
        ...


@runtime_checkable
class CacheBackend(Protocol):
    """
    Protocol for cache backends.

    Defines the interface for cache operations.
    """

    def get(self, key: str, default: Any = None) -> Any:
        """Get value from cache."""
        ...

    def set(self, key: str, value: Any, timeout: int | None = None) -> None:
        """Set value in cache."""
        ...

    def delete(self, key: str) -> bool:
        """Delete value from cache."""
        ...

    def clear(self) -> None:
        """Clear all cache."""
        ...


@runtime_checkable
class AIProvider(Protocol):
    """
    Protocol for AI provider implementations.

    Defines the interface for AI provider services.
    """

    def complete(
        self,
        prompt: str,
        system_prompt: str | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1000,
        **kwargs: Any,
    ) -> dict:
        """Generate AI completion."""
        ...

    async def stream_complete(
        self,
        prompt: str,
        system_prompt: str | None = None,
        **kwargs: Any,
    ):
        """Stream AI completion tokens."""
        ...


@runtime_checkable
class PaymentProcessor(Protocol):
    """
    Protocol for payment processing services.

    Defines the interface for payment operations.
    """

    def create_customer(self, user_id: int, email: str) -> str:
        """Create customer account. Returns customer ID."""
        ...

    def create_subscription(
        self,
        customer_id: str,
        price_id: str,
        **kwargs: Any,
    ) -> dict:
        """Create subscription. Returns subscription data."""
        ...

    def cancel_subscription(self, subscription_id: str) -> bool:
        """Cancel subscription."""
        ...

    def process_webhook(self, payload: bytes, signature: str) -> dict:
        """Process webhook event. Returns event data."""
        ...


@runtime_checkable
class MessageBroker(Protocol):
    """
    Protocol for message broker services.

    Defines the interface for pub/sub messaging.
    """

    def publish(self, channel: str, message: dict) -> None:
        """Publish message to channel."""
        ...

    def subscribe(self, channel: str) -> None:
        """Subscribe to channel."""
        ...

    def unsubscribe(self, channel: str) -> None:
        """Unsubscribe from channel."""
        ...
