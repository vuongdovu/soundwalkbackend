"""
DRF serializers for AI app.

This module provides serializers for:
- AI completion requests and responses
- Prompt templates
- Usage statistics

Related files:
    - models.py: AIProvider, PromptTemplate, AIRequest
    - views.py: AI API views

Usage:
    serializer = CompletionRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    response = AIService.complete(user=request.user, **serializer.validated_data)
"""

from __future__ import annotations

from rest_framework import serializers


class CompletionRequestSerializer(serializers.Serializer):
    """
    Serializer for AI completion requests.

    Fields:
        prompt: User prompt (required)
        system_prompt: Optional system message
        model: Model to use
        provider: Provider to use
        temperature: Sampling temperature
        max_tokens: Maximum response tokens

    Usage:
        serializer = CompletionRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        response = AIService.complete(**serializer.validated_data)
    """

    prompt = serializers.CharField(
        max_length=50000,
        help_text="User prompt",
    )
    system_prompt = serializers.CharField(
        max_length=10000,
        required=False,
        allow_blank=True,
        help_text="System message",
    )
    model = serializers.CharField(
        max_length=100,
        required=False,
        allow_blank=True,
        help_text="Model to use (e.g., gpt-4, claude-3-opus)",
    )
    provider = serializers.ChoiceField(
        choices=["openai", "anthropic"],
        required=False,
        help_text="AI provider",
    )
    temperature = serializers.FloatField(
        min_value=0,
        max_value=2,
        required=False,
        default=0.7,
        help_text="Sampling temperature (0-2)",
    )
    max_tokens = serializers.IntegerField(
        min_value=1,
        max_value=32000,
        required=False,
        default=1000,
        help_text="Maximum response tokens",
    )


class TemplateCompletionRequestSerializer(serializers.Serializer):
    """
    Serializer for template-based completion requests.

    Fields:
        template_slug: Template identifier
        variables: Template variable values
        model: Optional model override
        provider: Optional provider override

    Usage:
        serializer = TemplateCompletionRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        response = AIService.complete_with_template(**serializer.validated_data)
    """

    template_slug = serializers.SlugField(
        help_text="Template slug identifier",
    )
    variables = serializers.JSONField(
        default=dict,
        help_text="Template variable values",
    )
    model = serializers.CharField(
        max_length=100,
        required=False,
        allow_blank=True,
        help_text="Model override",
    )
    provider = serializers.ChoiceField(
        choices=["openai", "anthropic"],
        required=False,
        help_text="Provider override",
    )


class CompletionResponseSerializer(serializers.Serializer):
    """
    Serializer for AI completion responses.

    Fields:
        content: Response text
        model: Model used
        usage: Token counts
        cached: Whether response was cached
        request_id: AIRequest ID for reference

    Usage:
        response = AIService.complete(...)
        serializer = CompletionResponseSerializer(response)
        return Response(serializer.data)
    """

    content = serializers.CharField(read_only=True)
    model = serializers.CharField(read_only=True)
    usage = serializers.DictField(read_only=True)
    cached = serializers.BooleanField(read_only=True)
    request_id = serializers.IntegerField(read_only=True, allow_null=True)


class PromptTemplateSerializer(serializers.Serializer):
    """
    Serializer for prompt templates.

    Fields:
        id: Template ID
        name: Display name
        slug: URL identifier
        category: Template category
        description: Template description
        variables: Variable definitions
        version: Template version

    Usage:
        templates = PromptTemplate.objects.filter(is_active=True)
        serializer = PromptTemplateSerializer(templates, many=True)
    """

    # TODO: Implement serializer fields
    # id = serializers.IntegerField(read_only=True)
    # name = serializers.CharField(read_only=True)
    # slug = serializers.SlugField(read_only=True)
    # category = serializers.CharField(read_only=True)
    # variables = serializers.JSONField(read_only=True)
    # version = serializers.IntegerField(read_only=True)
    pass


class QuotaStatusSerializer(serializers.Serializer):
    """
    Serializer for quota status.

    Fields:
        tokens_remaining: Tokens left in quota
        requests_remaining: Requests left in quota
        reset_date: When quota resets
        can_proceed: Whether user can make request
        usage_percentage: Current usage percentage

    Usage:
        quota = AIService.check_quota(user)
        serializer = QuotaStatusSerializer(quota)
    """

    tokens_remaining = serializers.IntegerField(read_only=True)
    requests_remaining = serializers.IntegerField(read_only=True)
    reset_date = serializers.CharField(read_only=True)
    can_proceed = serializers.BooleanField(read_only=True)
    usage_percentage = serializers.FloatField(read_only=True)


class UsageStatsSerializer(serializers.Serializer):
    """
    Serializer for usage statistics.

    Fields:
        period_days: Number of days in period
        total_requests: Total requests made
        total_tokens: Total tokens used
        avg_latency_ms: Average response latency
        total_cost_usd: Total cost in USD

    Usage:
        stats = AIService.get_usage_stats(user, days=30)
        serializer = UsageStatsSerializer(stats)
    """

    period_days = serializers.IntegerField(read_only=True)
    total_requests = serializers.IntegerField(read_only=True)
    total_tokens = serializers.IntegerField(read_only=True)
    avg_latency_ms = serializers.IntegerField(read_only=True)
    total_cost_usd = serializers.FloatField(read_only=True)


class ModelInfoSerializer(serializers.Serializer):
    """
    Serializer for available model information.

    Fields:
        name: Model name
        provider: Provider type
        provider_name: Provider display name

    Usage:
        models = AIService.get_available_models()
        serializer = ModelInfoSerializer(models, many=True)
    """

    name = serializers.CharField(read_only=True)
    provider = serializers.CharField(read_only=True)
    provider_name = serializers.CharField(read_only=True)
