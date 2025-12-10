"""
URL configuration for authentication app.

This module defines URL patterns for authentication-related endpoints.
Most auth endpoints are provided by dj-rest-auth; this adds custom endpoints.

URL structure:
    /api/v1/auth/profile/           - Profile management
    /api/v1/auth/verify-email/      - Email verification
    /api/v1/auth/resend-verification/ - Resend verification email
    /api/v1/auth/deactivate/        - Account deactivation

Note:
    The base auth URLs from dj-rest-auth are included in config/urls.py:
    - /api/v1/auth/login/
    - /api/v1/auth/logout/
    - /api/v1/auth/user/
    - /api/v1/auth/password/reset/
    - /api/v1/auth/password/change/
    - /api/v1/auth/registration/
"""

from django.urls import path

from authentication.views import (
    ProfileView,
    EmailVerificationView,
    ResendVerificationView,
    DeactivateAccountView,
)

app_name = "authentication"

urlpatterns = [
    # Profile management
    path("profile/", ProfileView.as_view(), name="profile"),
    # Email verification
    path("verify-email/", EmailVerificationView.as_view(), name="verify-email"),
    path(
        "resend-verification/",
        ResendVerificationView.as_view(),
        name="resend-verification",
    ),
    # Account management
    path("deactivate/", DeactivateAccountView.as_view(), name="deactivate"),
]
