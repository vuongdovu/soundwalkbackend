"""
URL configuration for authentication app.

This module defines URL patterns for authentication-related endpoints.
Most auth endpoints are provided by dj-rest-auth; this adds custom endpoints.

URL structure:
    /api/v1/auth/profile/             - Profile management (GET/PUT/PATCH)
                                        Handles both initial completion and updates.
                                        Username required if not set.
    /api/v1/auth/verify-email/        - Email verification
    /api/v1/auth/resend-email/        - Resend verification email
    /api/v1/auth/deactivate/          - Account deactivation
    /api/v1/auth/google/              - Google OAuth2 login
    /api/v1/auth/apple/               - Apple Sign-In login

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
    AppleLoginView,
    DeactivateAccountView,
    EmailVerificationView,
    GoogleLoginView,
    ProfileView,
    ResendEmailView,
)

app_name = "authentication"

urlpatterns = [
    # Social authentication
    path("google/", GoogleLoginView.as_view(), name="google-login"),
    path("apple/", AppleLoginView.as_view(), name="apple-login"),
    # Profile management (handles both completion and updates)
    path("profile/", ProfileView.as_view(), name="profile"),
    # Email verification
    path("verify-email/", EmailVerificationView.as_view(), name="verify-email"),
    path(
        "resend-email/",
        ResendEmailView.as_view(),
        name="resend-email",
    ),
    # Account management
    path("deactivate/", DeactivateAccountView.as_view(), name="deactivate"),
]
