"""
Test configuration and fixtures for authentication tests.

This module provides:
- Reusable fixtures for common test scenarios
- API client helpers for authenticated requests
- Mocked external services (OAuth providers)
- Test data fixtures

Usage:
    def test_example(user, authenticated_client):
        response = authenticated_client.get('/api/v1/auth/profile/')
        assert response.status_code == 200
"""

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from authentication.models import (
    User,
    Profile,
    LinkedAccount,
    EmailVerificationToken,
    RESERVED_USERNAMES,
)
from authentication.tests.factories import (
    UserFactory,
    ProfileFactory,
    LinkedAccountFactory,
    EmailVerificationTokenFactory,
)


# =============================================================================
# User Fixtures
# =============================================================================


@pytest.fixture
def user(db):
    """
    Create a basic verified user with auto-created profile.

    The profile is automatically created via signals but has no username set.
    """
    return UserFactory(email_verified=True)


@pytest.fixture
def unverified_user(db):
    """Create an unverified user (email_verified=False)."""
    return UserFactory(email_verified=False)


@pytest.fixture
def superuser(db):
    """Create a superuser with admin privileges."""
    return User.objects.create_superuser(
        email="admin@example.com",
        password="AdminPass123!"
    )


@pytest.fixture
def deactivated_user(db):
    """Create a deactivated user (is_active=False)."""
    return UserFactory(is_active=False)


@pytest.fixture
def staff_user(db):
    """Create a staff user (is_staff=True)."""
    return UserFactory(is_staff=True, email_verified=True)


# =============================================================================
# Profile Fixtures
# =============================================================================


@pytest.fixture
def user_with_complete_profile(db):
    """
    Create a user with a complete profile (username set).

    This represents a user who has completed the onboarding flow.
    """
    user = UserFactory(email_verified=True)
    # Update the auto-created profile with username
    user.profile.username = "testuser"
    user.profile.first_name = "Test"
    user.profile.last_name = "User"
    user.profile.save()
    return user


@pytest.fixture
def user_with_incomplete_profile(db):
    """
    Create a user with incomplete profile (no username).

    This represents a user who hasn't completed onboarding.
    """
    user = UserFactory(email_verified=True)
    # Profile is auto-created but username is empty
    user.profile.username = ""
    user.profile.save()
    return user


@pytest.fixture
def profile(user):
    """Get the profile for the default user fixture."""
    return user.profile


# =============================================================================
# LinkedAccount Fixtures
# =============================================================================


@pytest.fixture
def linked_account_email(db, user):
    """Create email-linked account for user."""
    return LinkedAccountFactory(
        user=user,
        provider=LinkedAccount.Provider.EMAIL,
        provider_user_id=user.email
    )


@pytest.fixture
def linked_account_google(db, user):
    """Create Google-linked account for user."""
    return LinkedAccountFactory(
        user=user,
        provider=LinkedAccount.Provider.GOOGLE,
        provider_user_id="google-uid-123"
    )


@pytest.fixture
def linked_account_apple(db, user):
    """Create Apple-linked account for user."""
    return LinkedAccountFactory(
        user=user,
        provider=LinkedAccount.Provider.APPLE,
        provider_user_id="apple-uid-456"
    )


# =============================================================================
# Token Fixtures
# =============================================================================


@pytest.fixture
def valid_verification_token(db, user):
    """Create a valid email verification token (not used, not expired)."""
    return EmailVerificationTokenFactory(
        user=user,
        token_type=EmailVerificationToken.TokenType.EMAIL_VERIFICATION,
        expires_at=timezone.now() + timedelta(hours=24),
        used_at=None
    )


@pytest.fixture
def expired_verification_token(db, user):
    """Create an expired verification token."""
    return EmailVerificationTokenFactory(
        user=user,
        token_type=EmailVerificationToken.TokenType.EMAIL_VERIFICATION,
        expires_at=timezone.now() - timedelta(hours=1),
        used_at=None
    )


@pytest.fixture
def used_verification_token(db, user):
    """Create an already used verification token."""
    return EmailVerificationTokenFactory(
        user=user,
        token_type=EmailVerificationToken.TokenType.EMAIL_VERIFICATION,
        expires_at=timezone.now() + timedelta(hours=24),
        used_at=timezone.now() - timedelta(minutes=30)
    )


@pytest.fixture
def password_reset_token(db, user):
    """Create a valid password reset token (1-hour expiry)."""
    return EmailVerificationTokenFactory(
        user=user,
        token_type=EmailVerificationToken.TokenType.PASSWORD_RESET,
        expires_at=timezone.now() + timedelta(hours=1),
        used_at=None
    )


@pytest.fixture
def expired_password_reset_token(db, user):
    """Create an expired password reset token."""
    return EmailVerificationTokenFactory(
        user=user,
        token_type=EmailVerificationToken.TokenType.PASSWORD_RESET,
        expires_at=timezone.now() - timedelta(minutes=30),
        used_at=None
    )


# =============================================================================
# API Client Fixtures
# =============================================================================


@pytest.fixture
def api_client():
    """Unauthenticated API client for public endpoints."""
    return APIClient()


@pytest.fixture
def authenticated_client(user):
    """
    API client authenticated with JWT token for the default user fixture.

    Use this for tests that need a logged-in user.
    """
    client = APIClient()
    refresh = RefreshToken.for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


@pytest.fixture
def authenticated_client_factory(db):
    """
    Factory to create authenticated clients for any user.

    Usage:
        def test_example(authenticated_client_factory, some_user):
            client = authenticated_client_factory(some_user)
            response = client.get('/api/v1/auth/profile/')
    """
    def _make_client(user):
        client = APIClient()
        refresh = RefreshToken.for_user(user)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
        return client
    return _make_client


# =============================================================================
# Mock Fixtures for External Services
# =============================================================================


@pytest.fixture
def mock_google_oauth(mocker):
    """
    Mock Google OAuth2 token validation.

    Simulates a successful Google login with user data.
    """
    mock_response = mocker.patch(
        "allauth.socialaccount.providers.google.views.GoogleOAuth2Adapter.complete_login"
    )
    mock_response.return_value.account.uid = "google-uid-123"
    mock_response.return_value.account.extra_data = {
        "sub": "google-uid-123",
        "email": "googleuser@gmail.com",
        "email_verified": True,
        "given_name": "Google",
        "family_name": "User",
        "picture": "https://example.com/photo.jpg"
    }
    return mock_response


@pytest.fixture
def mock_google_oauth_invalid(mocker):
    """Mock invalid Google OAuth2 response."""
    mock_response = mocker.patch(
        "allauth.socialaccount.providers.google.views.GoogleOAuth2Adapter.complete_login"
    )
    mock_response.side_effect = Exception("Invalid token")
    return mock_response


@pytest.fixture
def mock_apple_oauth(mocker):
    """
    Mock Apple Sign-In token validation.

    Simulates a successful Apple login with user data.
    """
    mock_response = mocker.patch(
        "allauth.socialaccount.providers.apple.views.AppleOAuth2Adapter.complete_login"
    )
    mock_response.return_value.account.uid = "apple-uid-456"
    mock_response.return_value.account.extra_data = {
        "sub": "apple-uid-456",
        "email": "appleuser@privaterelay.apple.com",
        "email_verified": True,
        "is_private_email": True
    }
    return mock_response


@pytest.fixture
def mock_apple_oauth_invalid(mocker):
    """Mock invalid Apple OAuth response."""
    mock_response = mocker.patch(
        "allauth.socialaccount.providers.apple.views.AppleOAuth2Adapter.complete_login"
    )
    mock_response.side_effect = Exception("Invalid token")
    return mock_response


@pytest.fixture
def mock_celery_tasks(mocker):
    """
    Mock all Celery tasks to prevent actual task execution.

    Use this when testing views that trigger background tasks.
    """
    mocker.patch("authentication.tasks.send_verification_email.delay")
    mocker.patch("authentication.tasks.send_password_reset_email.delay")
    mocker.patch("authentication.tasks.send_welcome_email.delay")
    return mocker


# =============================================================================
# Test Data Fixtures
# =============================================================================


@pytest.fixture
def valid_registration_data():
    """Valid data for user registration endpoint."""
    return {
        "email": "newuser@example.com",
        "password": "SecurePass123!",
        "password_confirm": "SecurePass123!"
    }


@pytest.fixture
def valid_profile_data():
    """Valid data for profile completion/update endpoint."""
    return {
        "username": "validuser",
        "first_name": "Test",
        "last_name": "User",
        "timezone": "America/New_York",
        "preferences": {"theme": "dark", "language": "en"}
    }


@pytest.fixture
def reserved_usernames():
    """List of reserved usernames for testing validation."""
    return list(RESERVED_USERNAMES)[:10]  # First 10 reserved names


@pytest.fixture
def invalid_usernames():
    """List of invalid usernames for format validation testing."""
    return [
        "ab",              # Too short (2 chars)
        "a" * 31,          # Too long (31 chars)
        "user name",       # Contains space
        "user@name",       # Contains @
        "user.name",       # Contains period
        "user!name",       # Contains exclamation
        "",                # Empty string
        "user/name",       # Contains slash
        "user#name",       # Contains hash
    ]


@pytest.fixture
def valid_usernames():
    """List of valid usernames for format validation testing."""
    return [
        "abc",             # Minimum 3 chars
        "user123",         # Alphanumeric
        "user_name",       # With underscore
        "user-name",       # With hyphen
        "a" * 30,          # Maximum 30 chars
        "User123",         # Mixed case (will be normalized)
        "123user",         # Starting with number
        "_user_",          # Starting/ending with underscore
        "---",             # All hyphens
        "___",             # All underscores
    ]
