"""
Base provider protocol definition.

Defines the interface that all AI providers must implement.
Uses Python Protocol for structural subtyping.

Usage:
    from ai.providers.base import BaseProvider

    class MyProvider(BaseProvider):
        def complete(self, prompt, **kwargs):
            ...

        async def stream_complete(self, prompt, **kwargs):
            ...
"""

from __future__ import annotations

from typing import Any, AsyncGenerator, Protocol, runtime_checkable


@runtime_checkable
class BaseProvider(Protocol):
    """
    Protocol for AI provider implementations.

    All providers must implement these methods.
    Uses Protocol for duck typing compatibility.

    Required Methods:
        complete: Synchronous completion
        stream_complete: Streaming completion

    Response Format:
        complete() should return:
        {
            "content": str,  # Response text
            "model": str,  # Model used
            "usage": {
                "prompt_tokens": int,
                "completion_tokens": int,
            },
            "finish_reason": str,  # "stop", "length", etc.
        }
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
        """
        Generate AI completion.

        Args:
            prompt: User prompt
            system_prompt: Optional system message
            model: Model to use
            temperature: Sampling temperature (0-2)
            max_tokens: Maximum response tokens
            **kwargs: Provider-specific options

        Returns:
            Response dict with content, model, usage
        """
        ...

    async def stream_complete(
        self,
        prompt: str,
        system_prompt: str | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1000,
        **kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        """
        Stream AI completion tokens.

        Args:
            prompt: User prompt
            system_prompt: Optional system message
            model: Model to use
            temperature: Sampling temperature
            max_tokens: Maximum response tokens
            **kwargs: Provider-specific options

        Yields:
            Response tokens as strings
        """
        ...


class BaseProviderImpl:
    """
    Base implementation with shared functionality.

    Providers can inherit from this for common utilities.
    Not required, but helpful for code reuse.

    Attributes:
        api_key: API key for authentication
        base_url: Optional custom API endpoint
        default_model: Default model if not specified
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        default_model: str | None = None,
    ):
        """
        Initialize provider.

        Args:
            api_key: API key (or from env)
            base_url: Custom API endpoint
            default_model: Default model
        """
        self.api_key = api_key
        self.base_url = base_url
        self.default_model = default_model

    def _get_model(self, model: str | None) -> str:
        """Get model, using default if not specified."""
        return model or self.default_model or ""

    def _build_messages(
        self,
        prompt: str,
        system_prompt: str | None,
    ) -> list[dict]:
        """
        Build message list for chat completion.

        Args:
            prompt: User prompt
            system_prompt: Optional system message

        Returns:
            List of message dicts
        """
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return messages
