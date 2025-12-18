"""
URL configuration for the Django application.

The `urlpatterns` list routes URLs to views. This is the root URL configuration
that includes all app-specific routes.

URL Structure:
    /                              - ReDoc API documentation
    /admin/                        - Django admin interface
    /health/                       - Health check endpoint (for load balancers, Docker)
    /schema/                       - OpenAPI schema (YAML)
    /api/v1/auth/                  - Authentication endpoints (dj-rest-auth)
        login/                     - Email/password login
        logout/                    - Logout
        registration/              - User registration
        user/                      - Current user (GET/PUT/PATCH)
        password/reset/            - Request password reset
        password/change/           - Change password
        profile/                   - User profile (custom)
        verify-email/              - Email verification (custom)
        resend-email/              - Resend verification email (custom)
        deactivate/                - Account deactivation (custom)
        google/                    - Google OAuth2 login (custom)
        apple/                     - Apple Sign-In login (custom)
    /api/v1/chat/                  - Chat endpoints
        conversations/             - Conversation list/create
        conversations/{id}/        - Conversation detail/update/delete
        conversations/{id}/read/   - Mark conversation as read
        conversations/{id}/leave/  - Leave conversation
        conversations/{id}/transfer-ownership/ - Transfer group ownership
        conversations/{id}/participants/ - Participant list/add
        conversations/{id}/participants/{pk}/ - Participant update/remove
        conversations/{id}/messages/ - Message list/send
        conversations/{id}/messages/{pk}/ - Message delete
    /api/v1/payments/              - Payment endpoints
        webhooks/stripe/           - Stripe webhook endpoint (POST)
    /api/v1/media/                 - Media endpoints
        upload/                    - Upload media file
        files/{id}/                - Get file details
        files/{id}/download/       - Download file
        files/{id}/view/           - View file inline
        files/{id}/shares/         - List/create file shares
        files/{id}/shares/{user}/ - Revoke file share
        files/{id}/tags/           - List/apply file tags
        files/{id}/tags/{tag}/     - Remove tag from file
        shared-with-me/            - List files shared with current user
        search/                    - Full-text search with filters
        quota/                     - Get storage quota status
        tags/                      - List/create user tags
        tags/{id}/                 - Get/delete user tag
        files/by-tags/             - Query files by tags
        chunked/sessions/          - Create chunked upload session
        chunked/sessions/{id}/     - Get/abort upload session
        chunked/sessions/{id}/parts/{num}/  - Upload chunk
        chunked/sessions/{id}/finalize/     - Finalize upload

For more information, see:
https://docs.djangoproject.com/en/5.2/topics/http/urls/
"""

from django.contrib import admin
from django.urls import include, path
from django.views.generic import TemplateView
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView

from core.views import health_check

# =============================================================================
# API v1 Routes
# =============================================================================
# All routes here are prefixed with /api/v1/ automatically
api_v1_patterns = [
    # Authentication (dj-rest-auth)
    path("auth/", include("dj_rest_auth.urls")),
    path("accounts/", include("allauth.urls")),
    # Custom authentication (profile, verify-email, etc.)
    path("auth/", include("authentication.urls")),
    # Registration
    path("auth/registration/", include("dj_rest_auth.registration.urls")),
    # Chat
    path("chat/", include("chat.urls")),
    # Notifications
    path("notifications/", include("notifications.urls")),
    # Payments
    path("payments/", include("payments.urls")),
    # Media
    path("media/", include("media.urls")),
]

urlpatterns = [
    # Documentation
    path("", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    path("schema/", SpectacularAPIView.as_view(), name="schema"),
    # Admin
    path("admin/", admin.site.urls),
    # Health check (Docker, Kubernetes, load balancers)
    path("health/", health_check, name="health_check"),
    # Password reset confirm URL (required by dj-rest-auth for email generation)
    # This URL is included in password reset emails and should point to your frontend
    path(
        "password/reset/confirm/<uidb64>/<token>/",
        TemplateView.as_view(template_name="password_reset_confirm.html"),
        name="password_reset_confirm",
    ),
    # API v1
    path("api/v1/", include(api_v1_patterns)),
]

# =============================================================================
# Admin Site Customization
# =============================================================================
admin.site.site_header = "Application Admin"
admin.site.site_title = "Admin Portal"
admin.site.index_title = "Welcome to the Admin Portal"
