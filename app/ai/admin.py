"""
Django admin configuration for AI app.

Registers:
    - AIProvider: AI provider configurations
    - PromptTemplate: Prompt templates
    - AIRequest: AI request logs
    - AIUsageQuota: User quotas

Usage:
    Access via /admin/ai/
"""


# TODO: Uncomment when models are implemented
# from .models import AIProvider, AIRequest, AIUsageQuota, PromptTemplate


# TODO: Implement admin classes
# @admin.register(AIProvider)
# class AIProviderAdmin(admin.ModelAdmin):
#     """Admin for AIProvider model."""
#
#     list_display = [
#         "name",
#         "provider_type",
#         "default_model",
#         "is_active",
#         "requests_per_minute",
#     ]
#     list_filter = ["provider_type", "is_active"]
#     search_fields = ["name"]
#     readonly_fields = ["created_at", "updated_at"]
#
#     fieldsets = (
#         (None, {
#             "fields": ("name", "provider_type", "is_active"),
#         }),
#         ("API Configuration", {
#             "fields": ("api_key_env_var", "base_url"),
#         }),
#         ("Models", {
#             "fields": ("models", "default_model"),
#         }),
#         ("Defaults", {
#             "fields": ("default_temperature", "default_max_tokens"),
#         }),
#         ("Rate Limits", {
#             "fields": ("requests_per_minute", "tokens_per_minute"),
#         }),
#         ("Timestamps", {
#             "fields": ("created_at", "updated_at"),
#             "classes": ("collapse",),
#         }),
#     )


# @admin.register(PromptTemplate)
# class PromptTemplateAdmin(admin.ModelAdmin):
#     """Admin for PromptTemplate model."""
#
#     list_display = [
#         "name",
#         "slug",
#         "category",
#         "version",
#         "is_active",
#     ]
#     list_filter = ["category", "is_active", "preferred_provider"]
#     search_fields = ["name", "slug", "system_prompt"]
#     readonly_fields = ["created_at", "updated_at"]
#     prepopulated_fields = {"slug": ("name",)}
#
#     fieldsets = (
#         (None, {
#             "fields": ("name", "slug", "category", "is_active", "version"),
#         }),
#         ("Prompts", {
#             "fields": ("system_prompt", "user_prompt_template"),
#         }),
#         ("Variables", {
#             "fields": ("variables",),
#         }),
#         ("Preferences", {
#             "fields": ("preferred_provider", "preferred_model", "temperature", "max_tokens"),
#         }),
#         ("Timestamps", {
#             "fields": ("created_at", "updated_at"),
#             "classes": ("collapse",),
#         }),
#     )


# @admin.register(AIRequest)
# class AIRequestAdmin(admin.ModelAdmin):
#     """Admin for AIRequest model."""
#
#     list_display = [
#         "id",
#         "user",
#         "model",
#         "status",
#         "total_tokens",
#         "latency_ms",
#         "cache_hit",
#         "created_at",
#     ]
#     list_filter = ["status", "model", "cache_hit", "provider"]
#     search_fields = ["user__email", "user_prompt", "response"]
#     readonly_fields = [
#         "user",
#         "provider",
#         "model",
#         "prompt_hash",
#         "system_prompt",
#         "user_prompt",
#         "response",
#         "prompt_tokens",
#         "completion_tokens",
#         "total_tokens",
#         "cost_microdollars",
#         "latency_ms",
#         "error_code",
#         "error_message",
#         "cache_key",
#         "cache_hit",
#         "created_at",
#         "updated_at",
#     ]
#     date_hierarchy = "created_at"
#
#     fieldsets = (
#         (None, {
#             "fields": ("user", "provider", "model", "prompt_template", "status"),
#         }),
#         ("Request", {
#             "fields": ("system_prompt", "user_prompt"),
#         }),
#         ("Response", {
#             "fields": ("response",),
#         }),
#         ("Usage", {
#             "fields": ("prompt_tokens", "completion_tokens", "total_tokens", "cost_microdollars"),
#         }),
#         ("Performance", {
#             "fields": ("latency_ms", "cache_hit", "cache_key", "prompt_hash"),
#         }),
#         ("Error", {
#             "fields": ("error_code", "error_message"),
#             "classes": ("collapse",),
#         }),
#         ("Timestamps", {
#             "fields": ("created_at", "updated_at"),
#             "classes": ("collapse",),
#         }),
#     )
#
#     def has_add_permission(self, request):
#         """Disable manual creation."""
#         return False
#
#     def has_change_permission(self, request, obj=None):
#         """Disable editing."""
#         return False


# @admin.register(AIUsageQuota)
# class AIUsageQuotaAdmin(admin.ModelAdmin):
#     """Admin for AIUsageQuota model."""
#
#     list_display = [
#         "user",
#         "tokens_used",
#         "monthly_token_limit",
#         "requests_used",
#         "monthly_request_limit",
#         "usage_period_end",
#     ]
#     search_fields = ["user__email"]
#     readonly_fields = ["user", "created_at", "updated_at"]
#
#     fieldsets = (
#         (None, {
#             "fields": ("user",),
#         }),
#         ("Limits", {
#             "fields": ("monthly_token_limit", "monthly_request_limit", "allow_overage"),
#         }),
#         ("Current Usage", {
#             "fields": ("tokens_used", "requests_used", "overage_tokens"),
#         }),
#         ("Period", {
#             "fields": ("usage_period_start", "usage_period_end"),
#         }),
#         ("Timestamps", {
#             "fields": ("created_at", "updated_at"),
#             "classes": ("collapse",),
#         }),
#     )
