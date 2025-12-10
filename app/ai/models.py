"""
AI models for provider integration.

This module defines models for:
- AIProvider: Provider configuration
- PromptTemplate: Reusable prompt templates
- AIRequest: Usage logging
- AIUsageQuota: Per-user quotas

Related files:
    - services.py: AIService for business logic
    - providers/: Provider implementations
    - tasks.py: Async AI processing

Model Relationships:
    AIProvider (1) ---> (*) PromptTemplate (preferred_provider)
    AIProvider (1) ---> (*) AIRequest
    User (1) ---> (*) AIRequest
    User (1) ---> (1) AIUsageQuota
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.conf import settings
from django.db import models

from core.models import BaseModel

if TYPE_CHECKING:
    pass


class ProviderType(models.TextChoices):
    """AI provider types."""

    OPENAI = "openai", "OpenAI"
    ANTHROPIC = "anthropic", "Anthropic"
    GOOGLE = "google", "Google (Gemini)"
    AZURE = "azure", "Azure OpenAI"
    LOCAL = "local", "Local/Self-hosted"


class PromptCategory(models.TextChoices):
    """Prompt template categories."""

    CHAT = "chat", "Chat"
    SUMMARIZATION = "summarization", "Summarization"
    EXTRACTION = "extraction", "Data Extraction"
    GENERATION = "generation", "Content Generation"
    ANALYSIS = "analysis", "Analysis"
    CUSTOM = "custom", "Custom"


class AIRequestStatus(models.TextChoices):
    """AI request processing status."""

    PENDING = "pending", "Pending"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"
    CACHED = "cached", "Cached"


class AIProvider(BaseModel):
    """
    AI provider configuration.

    Stores provider settings including API key reference,
    available models, and rate limits.

    Fields:
        name: Provider display name
        provider_type: Type of provider
        api_key_env_var: Environment variable name for API key
        base_url: Custom API endpoint (for Azure/local)
        models: List of available models (JSON)
        default_model: Default model to use
        default_temperature: Default temperature
        default_max_tokens: Default max tokens
        requests_per_minute: Rate limit (RPM)
        tokens_per_minute: Rate limit (TPM)
        is_active: Whether provider is enabled

    Security:
        API keys stored in environment variables, not database.

    Usage:
        provider = AIProvider.objects.get(provider_type="openai")
        api_key = os.environ.get(provider.api_key_env_var)
    """

    # TODO: Implement model fields
    # name = models.CharField(max_length=100, unique=True)
    # provider_type = models.CharField(
    #     max_length=20,
    #     choices=ProviderType.choices,
    # )
    # api_key_env_var = models.CharField(
    #     max_length=100,
    #     help_text="Environment variable name for API key",
    # )
    # base_url = models.URLField(
    #     blank=True,
    #     help_text="Custom API endpoint for Azure/local providers",
    # )
    # models = models.JSONField(
    #     default=list,
    #     help_text="List of available models",
    # )
    # default_model = models.CharField(max_length=100)
    # default_temperature = models.FloatField(default=0.7)
    # default_max_tokens = models.IntegerField(default=1000)
    # requests_per_minute = models.IntegerField(default=60)
    # tokens_per_minute = models.IntegerField(default=90000)
    # is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        verbose_name = "AI Provider"
        verbose_name_plural = "AI Providers"

    def __str__(self) -> str:
        # TODO: Implement
        # return f"{self.name} ({self.provider_type})"
        return "AIProvider"

    # TODO: Implement methods
    # def get_api_key(self) -> str:
    #     """Get API key from environment."""
    #     import os
    #     return os.environ.get(self.api_key_env_var, "")
    #
    # def get_client(self):
    #     """Get configured provider client."""
    #     from .providers import get_provider
    #     return get_provider(self.provider_type, api_key=self.get_api_key())


class PromptTemplate(BaseModel):
    """
    Reusable prompt template.

    Stores system prompts and user prompt templates
    with variable substitution support.

    Fields:
        name: Template display name
        slug: URL-safe identifier
        category: Template category
        system_prompt: System message for the AI
        user_prompt_template: User prompt with {placeholders}
        variables: Variable definitions (JSON)
        preferred_provider: Optional preferred provider
        preferred_model: Optional preferred model
        temperature: Optional temperature override
        max_tokens: Optional max tokens override
        version: Template version number
        is_active: Whether template is enabled

    Variables JSON structure:
        [
            {"name": "text", "type": "string", "required": true},
            {"name": "language", "type": "string", "required": false, "default": "English"},
        ]

    Usage:
        template = PromptTemplate.objects.get(slug="summarize")
        prompt = template.render(text="Lorem ipsum...")
    """

    # TODO: Implement model fields
    # name = models.CharField(max_length=100, unique=True)
    # slug = models.SlugField(max_length=100, unique=True)
    # category = models.CharField(
    #     max_length=20,
    #     choices=PromptCategory.choices,
    #     default=PromptCategory.CUSTOM,
    # )
    # system_prompt = models.TextField(
    #     help_text="System message for the AI",
    # )
    # user_prompt_template = models.TextField(
    #     help_text="User prompt with {placeholders}",
    # )
    # variables = models.JSONField(
    #     default=list,
    #     help_text="Variable definitions: name, type, required, default",
    # )
    # preferred_provider = models.ForeignKey(
    #     AIProvider,
    #     on_delete=models.SET_NULL,
    #     null=True,
    #     blank=True,
    #     related_name="preferred_templates",
    # )
    # preferred_model = models.CharField(max_length=100, blank=True)
    # temperature = models.FloatField(null=True, blank=True)
    # max_tokens = models.IntegerField(null=True, blank=True)
    # version = models.PositiveIntegerField(default=1)
    # is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        verbose_name = "Prompt Template"
        verbose_name_plural = "Prompt Templates"

    def __str__(self) -> str:
        # TODO: Implement
        # return f"{self.name} (v{self.version})"
        return "PromptTemplate"

    # TODO: Implement methods
    # def render(self, **variables) -> str:
    #     """Render user prompt with variables."""
    #     # Validate required variables
    #     for var_def in self.variables:
    #         if var_def.get("required") and var_def["name"] not in variables:
    #             if "default" not in var_def:
    #                 raise ValueError(f"Missing required variable: {var_def['name']}")
    #             variables[var_def["name"]] = var_def["default"]
    #
    #     return self.user_prompt_template.format(**variables)
    #
    # def get_variable_names(self) -> list[str]:
    #     """Get list of variable names."""
    #     return [v["name"] for v in self.variables]


class AIRequest(BaseModel):
    """
    AI request log for usage tracking.

    Records all AI completions for:
    - Usage tracking and billing
    - Audit trail
    - Cache lookup
    - Analytics

    Fields:
        user: Requesting user (null for anonymous)
        provider: AI provider used
        model: Model used
        prompt_template: Template used (if applicable)
        prompt_hash: Hash for cache lookup
        system_prompt: Actual system prompt
        user_prompt: Actual user prompt
        response: AI response text
        status: Request status
        prompt_tokens: Input token count
        completion_tokens: Output token count
        total_tokens: Total token count
        cost_microdollars: Cost in microdollars (0.000001)
        latency_ms: Response latency
        error_code: Error code if failed
        error_message: Error details if failed
        cache_key: Cache key if cached
        cache_hit: Whether this was a cache hit

    Indexes:
        - [user, created_at]
        - [status, created_at]
        - [model, status]
        - prompt_hash

    Usage:
        requests = AIRequest.objects.filter(
            user=user,
            status=AIRequestStatus.COMPLETED
        ).order_by("-created_at")[:10]
    """

    # TODO: Implement model fields
    # user = models.ForeignKey(
    #     settings.AUTH_USER_MODEL,
    #     on_delete=models.SET_NULL,
    #     null=True,
    #     related_name="ai_requests",
    # )
    # provider = models.ForeignKey(
    #     AIProvider,
    #     on_delete=models.SET_NULL,
    #     null=True,
    #     related_name="requests",
    # )
    # model = models.CharField(max_length=100, db_index=True)
    # prompt_template = models.ForeignKey(
    #     PromptTemplate,
    #     on_delete=models.SET_NULL,
    #     null=True,
    #     blank=True,
    #     related_name="requests",
    # )
    # prompt_hash = models.CharField(
    #     max_length=64,
    #     db_index=True,
    #     help_text="SHA256 hash for cache lookup",
    # )
    # system_prompt = models.TextField(blank=True)
    # user_prompt = models.TextField()
    # response = models.TextField(blank=True)
    # status = models.CharField(
    #     max_length=20,
    #     choices=AIRequestStatus.choices,
    #     default=AIRequestStatus.PENDING,
    # )
    # prompt_tokens = models.IntegerField(default=0)
    # completion_tokens = models.IntegerField(default=0)
    # total_tokens = models.IntegerField(default=0)
    # cost_microdollars = models.IntegerField(
    #     default=0,
    #     help_text="Cost in microdollars (0.000001 USD)",
    # )
    # latency_ms = models.IntegerField(null=True, blank=True)
    # error_code = models.CharField(max_length=100, blank=True)
    # error_message = models.TextField(blank=True)
    # cache_key = models.CharField(max_length=255, blank=True, db_index=True)
    # cache_hit = models.BooleanField(default=False)

    class Meta:
        verbose_name = "AI Request"
        verbose_name_plural = "AI Requests"
        ordering = ["-created_at"]
        # indexes = [
        #     models.Index(fields=["user", "-created_at"]),
        #     models.Index(fields=["status", "-created_at"]),
        #     models.Index(fields=["model", "status"]),
        # ]

    def __str__(self) -> str:
        # TODO: Implement
        # return f"AI Request {self.id} ({self.model}, {self.status})"
        return "AIRequest"

    # TODO: Implement properties
    # @property
    # def cost_display(self) -> str:
    #     """Format cost for display."""
    #     dollars = self.cost_microdollars / 1_000_000
    #     return f"${dollars:.6f}"


class AIUsageQuota(BaseModel):
    """
    Per-user AI usage quota.

    Tracks and limits user AI usage per billing period.

    Fields:
        user: User (OneToOne, primary key)
        monthly_token_limit: Token limit per period
        monthly_request_limit: Request limit per period
        tokens_used: Tokens used this period
        requests_used: Requests used this period
        usage_period_start: Current period start
        usage_period_end: Current period end
        allow_overage: Whether to allow exceeding quota
        overage_tokens: Tokens used beyond quota

    Usage:
        quota = AIUsageQuota.objects.get(user=user)
        if quota.can_make_request(estimated_tokens=1000):
            # Proceed with AI request
    """

    # TODO: Implement model fields
    # user = models.OneToOneField(
    #     settings.AUTH_USER_MODEL,
    #     on_delete=models.CASCADE,
    #     primary_key=True,
    #     related_name="ai_quota",
    # )
    # monthly_token_limit = models.IntegerField(default=100000)
    # monthly_request_limit = models.IntegerField(default=1000)
    # tokens_used = models.IntegerField(default=0)
    # requests_used = models.IntegerField(default=0)
    # usage_period_start = models.DateField()
    # usage_period_end = models.DateField()
    # allow_overage = models.BooleanField(default=False)
    # overage_tokens = models.IntegerField(default=0)

    class Meta:
        verbose_name = "AI Usage Quota"
        verbose_name_plural = "AI Usage Quotas"

    def __str__(self) -> str:
        # TODO: Implement
        # return f"AI Quota for {self.user.email}"
        return "AIUsageQuota"

    # TODO: Implement methods
    # def can_make_request(self, estimated_tokens: int = 1000) -> bool:
    #     """Check if user can make an AI request."""
    #     if self.allow_overage:
    #         return True
    #     return (
    #         self.tokens_used + estimated_tokens <= self.monthly_token_limit
    #         and self.requests_used < self.monthly_request_limit
    #     )
    #
    # def record_usage(self, tokens: int) -> None:
    #     """Record token usage."""
    #     if self.tokens_used + tokens > self.monthly_token_limit:
    #         overage = (self.tokens_used + tokens) - self.monthly_token_limit
    #         self.overage_tokens += overage
    #     self.tokens_used += tokens
    #     self.requests_used += 1
    #     self.save(update_fields=["tokens_used", "requests_used", "overage_tokens", "updated_at"])
    #
    # @property
    # def tokens_remaining(self) -> int:
    #     """Get remaining tokens in quota."""
    #     return max(0, self.monthly_token_limit - self.tokens_used)
    #
    # @property
    # def requests_remaining(self) -> int:
    #     """Get remaining requests in quota."""
    #     return max(0, self.monthly_request_limit - self.requests_used)
    #
    # @property
    # def usage_percentage(self) -> float:
    #     """Get usage as percentage of quota."""
    #     return (self.tokens_used / self.monthly_token_limit) * 100 if self.monthly_token_limit else 0
