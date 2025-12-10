"""
AI provider implementations.

This package contains provider-specific implementations:
- base.py: BaseProvider protocol definition
- openai.py: OpenAI implementation
- anthropic.py: Anthropic (Claude) implementation

Provider Selection:
    Providers are selected by type string (e.g., "openai", "anthropic").
    Use get_provider() factory function.

Usage:
    from ai.providers import get_provider

    # Get OpenAI provider
    provider = get_provider("openai")
    response = provider.complete(
        prompt="Hello",
        model="gpt-4",
    )

    # Get Anthropic provider
    provider = get_provider("anthropic")
    response = provider.complete(
        prompt="Hello",
        model="claude-3-opus",
    )

Adding New Providers:
    1. Create new file (e.g., google.py)
    2. Implement BaseProvider protocol
    3. Register in PROVIDERS dict below
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import BaseProvider

logger = logging.getLogger(__name__)

# Provider registry
# Maps provider type string to provider class
PROVIDERS: dict[str, type] = {
    # TODO: Uncomment when implemented
    # "openai": OpenAIProvider,
    # "anthropic": AnthropicProvider,
}


def get_provider(provider_type: str, **kwargs) -> BaseProvider:
    """
    Get provider instance by type.

    Args:
        provider_type: Provider type string
        **kwargs: Provider configuration options

    Returns:
        Configured provider instance

    Raises:
        ValueError: If provider type unknown
    """
    # TODO: Implement
    # provider_class = PROVIDERS.get(provider_type)
    # if not provider_class:
    #     raise ValueError(f"Unknown provider type: {provider_type}")
    # return provider_class(**kwargs)
    logger.info(f"get_provider called for {provider_type} (not implemented)")
    raise NotImplementedError(f"Provider {provider_type} not implemented")


def list_providers() -> list[str]:
    """Get list of available provider types."""
    return list(PROVIDERS.keys())
