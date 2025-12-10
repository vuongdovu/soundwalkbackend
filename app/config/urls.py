"""
URL configuration for the Django application.

The `urlpatterns` list routes URLs to views. This is the root URL configuration
that includes all app-specific routes.

URL Structure:
    /                         - Swagger UI (API documentation)
    /admin/                   - Django admin interface
    /health/                  - Health check endpoint (for load balancers, Docker)
    /schema/                  - OpenAPI schema (YAML)
    /redoc/                   - ReDoc documentation (alternative UI)
    /api/v1/auth/             - Authentication endpoints (dj-rest-auth)
    /api/v1/auth/social/      - Social authentication (Google, Apple)
    /api/v1/payments/         - Payment and subscription endpoints
    /api/v1/notifications/    - Notification endpoints
    /api/v1/chat/             - Chat and messaging endpoints
    /api/v1/ai/               - AI completion endpoints

For more information, see:
https://docs.djangoproject.com/en/5.2/topics/http/urls/
"""

from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

from core.views import health_check

urlpatterns = [
    # -------------------------------------------------------------------------
    # API Documentation (Swagger/OpenAPI)
    # -------------------------------------------------------------------------
    # Interactive API documentation using Swagger UI
    path("", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("schema/", SpectacularAPIView.as_view(), name="schema"),
    path("redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    # -------------------------------------------------------------------------
    # Admin Interface
    # -------------------------------------------------------------------------
    # Django's built-in admin for managing users, models, and settings
    path("admin/", admin.site.urls),
    # -------------------------------------------------------------------------
    # Health Check
    # -------------------------------------------------------------------------
    # Used by Docker, Kubernetes, and load balancers to verify service health
    # Returns JSON with service status and database connectivity
    path("health/", health_check, name="health_check"),
    # -------------------------------------------------------------------------
    # Authentication API (dj-rest-auth)
    # -------------------------------------------------------------------------
    # REST endpoints for authentication:
    # - POST /api/v1/auth/login/          - Login with email/password
    # - POST /api/v1/auth/logout/         - Logout (invalidate token)
    # - POST /api/v1/auth/password/reset/ - Request password reset
    # - POST /api/v1/auth/password/change/- Change password
    # - GET  /api/v1/auth/user/           - Get current user info
    path("api/v1/auth/", include("dj_rest_auth.urls")),
    # -------------------------------------------------------------------------
    # Social Authentication (django-allauth via dj-rest-auth)
    # -------------------------------------------------------------------------
    # REST endpoints for social login:
    # - POST /api/v1/auth/social/google/  - Login with Google token
    # - POST /api/v1/auth/social/apple/   - Login with Apple token
    path("api/v1/auth/social/", include("authentication.urls")),
    # -------------------------------------------------------------------------
    # Registration (optional - enable if you want email/password registration)
    # -------------------------------------------------------------------------
    # Uncomment to enable registration endpoints:
    # - POST /api/v1/auth/registration/   - Register new user
    # - POST /api/v1/auth/registration/verify-email/ - Verify email
    # path("api/v1/auth/registration/", include("dj_rest_auth.registration.urls")),
    # -------------------------------------------------------------------------
    # Application APIs
    # -------------------------------------------------------------------------
    # Payments API - Stripe subscriptions, billing, webhooks
    path("api/v1/payments/", include("payments.urls")),
    # Notifications API - Push notifications, preferences, device tokens
    path("api/v1/notifications/", include("notifications.urls")),
    # Chat API - Conversations, messages (REST fallback for WebSocket)
    path("api/v1/chat/", include("chat.urls")),
    # AI API - Completions, templates, usage tracking
    path("api/v1/ai/", include("ai.urls")),
]

# =============================================================================
# Admin Site Customization
# =============================================================================
admin.site.site_header = "Application Admin"
admin.site.site_title = "Admin Portal"
admin.site.index_title = "Welcome to the Admin Portal"
