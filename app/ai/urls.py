"""
URL configuration for AI app.

Routes:
    POST /complete/ - Generate completion
    POST /complete/template/ - Template completion
    GET /quota/ - Get quota status
    GET /usage/ - Get usage statistics
    GET /models/ - List available models
    GET /templates/ - List prompt templates

Usage in config/urls.py:
    path("api/v1/ai/", include("ai.urls")),
"""

from django.urls import path

from . import views

app_name = "ai"

urlpatterns = [
    # Completions
    path(
        "complete/",
        views.CompletionView.as_view(),
        name="complete",
    ),
    path(
        "complete/template/",
        views.TemplateCompletionView.as_view(),
        name="template-complete",
    ),
    # Quota and usage
    path(
        "quota/",
        views.QuotaView.as_view(),
        name="quota",
    ),
    path(
        "usage/",
        views.UsageStatsView.as_view(),
        name="usage",
    ),
    # Reference
    path(
        "models/",
        views.ModelsView.as_view(),
        name="models",
    ),
    path(
        "templates/",
        views.TemplatesView.as_view(),
        name="templates",
    ),
]
