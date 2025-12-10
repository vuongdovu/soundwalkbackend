"""
AI app for AI provider integration.

This app handles:
- Multi-provider AI abstraction (OpenAI, Anthropic)
- Prompt template management
- Usage tracking and quotas
- Response caching
- AI chat integration

Related apps:
    - authentication: User model for usage tracking
    - chat: AI conversations

Provider Architecture:
    Uses protocol-based abstraction for AI providers.
    See providers/ for implementations.

Usage:
    from ai.services import AIService

    # Simple completion
    response = AIService.complete(
        user=user,
        prompt="Explain quantum computing",
        model="gpt-4",
    )

    # Template-based completion
    response = AIService.complete_with_template(
        user=user,
        template_slug="summarize",
        variables={"text": article_text},
    )

    # Streaming completion
    async for chunk in AIService.stream_complete(user, prompt):
        print(chunk, end="")
"""
