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

    Biometric authentication (Face ID / Touch ID):
    /api/v1/auth/biometric/enroll/       - Enroll public key (POST, auth required)
    /api/v1/auth/biometric/challenge/    - Request challenge nonce (POST)
    /api/v1/auth/biometric/authenticate/ - Authenticate with signature (POST)
    /api/v1/auth/biometric/              - Disable biometric (DELETE, auth required)
    /api/v1/auth/biometric/status/       - Check if enabled (GET)

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
    LinkedInLoginView,
    ProfileView,
    ResendEmailView,
    BiometricEnrollView,
    BiometricChallengeView,
    BiometricAuthenticateView,
    BiometricDisableView,
    BiometricStatusView,
    CookieTokenRefreshView
)

app_name = "authentication"

urlpatterns = [
    path(
        "token/refresh/", CookieTokenRefreshView.as_view(), name="cookie_token_refresh"
    ),
    # Social authentication
    path("google/", GoogleLoginView.as_view(), name="google-login"),
    path("apple/", AppleLoginView.as_view(), name="apple-login"),
    path("linkedin/", LinkedInLoginView.as_view(), name="linkedin-login"),
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
    # Biometric authentication (Face ID / Touch ID)
    path("biometric/enroll/", BiometricEnrollView.as_view(), name="biometric-enroll"),
    path(
        "biometric/challenge/",
        BiometricChallengeView.as_view(),
        name="biometric-challenge",
    ),
    path(
        "biometric/authenticate/",
        BiometricAuthenticateView.as_view(),
        name="biometric-authenticate",
    ),
    path("biometric/", BiometricDisableView.as_view(), name="biometric-disable"),
    path("biometric/status/", BiometricStatusView.as_view(), name="biometric-status"),
]
