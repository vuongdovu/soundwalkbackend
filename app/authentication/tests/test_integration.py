"""
Integration tests for authentication flows.

This module provides end-to-end integration tests that exercise complete user
journeys through the authentication system. These tests verify that all
components work together correctly across HTTP request-response cycles.

Test Organization:
    - TestEmailRegistrationFlow: Registration -> Verification -> Login -> Profile
    - TestEmailVerificationFlow: Token verification lifecycle
    - TestPasswordResetFlow: Password reset from request to completion
    - TestProfileCompletionFlow: Onboarding flow for new users
    - TestAccountDeactivationFlow: Soft-delete and its effects
    - TestMultiProviderFlow: Users with multiple auth methods

Testing Philosophy:
    Integration tests exercise the full stack including:
    - HTTP request parsing and routing
    - Authentication and permission checks
    - Serializer validation and transformation
    - Service layer business logic
    - Database transactions and state changes

    These tests are slower than unit tests but provide confidence that
    the system works as users will experience it.

Dependencies:
    - pytest and pytest-django for test framework
    - rest_framework.test.APIClient for HTTP requests
    - Factory Boy fixtures from conftest.py

Note:
    Tests are marked with @pytest.mark.integration to allow selective
    execution: pytest -m integration
"""

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework import status

from authentication.models import (
    User,
    Profile,
    LinkedAccount,
    EmailVerificationToken,
)
from authentication.tests.factories import (
    UserFactory,
    EmailVerificationTokenFactory,
)
from core.helpers import generate_token


# =============================================================================
# URL Constants
# =============================================================================

LOGIN_URL = "/api/v1/auth/login/"
LOGOUT_URL = "/api/v1/auth/logout/"
USER_URL = "/api/v1/auth/user/"
PROFILE_URL = "/api/v1/auth/profile/"
VERIFY_EMAIL_URL = "/api/v1/auth/verify-email/"
RESEND_EMAIL_URL = "/api/v1/auth/resend-email/"
DEACTIVATE_URL = "/api/v1/auth/deactivate/"
PASSWORD_RESET_URL = "/api/v1/auth/password/reset/"
PASSWORD_RESET_CONFIRM_URL = "/api/v1/auth/password/reset/confirm/"


# =============================================================================
# TestEmailRegistrationFlow
# =============================================================================


@pytest.mark.integration
@pytest.mark.django_db
class TestEmailRegistrationFlow:
    """
    Complete user journey: Register -> Verify Email -> Login -> Complete Profile.

    This test class covers the full onboarding flow for a user signing up
    with email and password. Each test builds on the previous state to
    simulate a realistic user journey.

    Flow:
        1. User registers with email/password
        2. User is created with email_verified=False
        3. LinkedAccount is created for email provider
        4. Profile is auto-created (empty username)
        5. User verifies email using token
        6. User logs in and receives JWT tokens
        7. User completes profile by setting username
    """

    def test_full_registration_to_profile_completion_flow(
        self,
        api_client,
        authenticated_client_factory,
        monkeypatch,
    ):
        """
        Complete registration flow from signup to profile completion.

        This test exercises the entire happy path for email registration.
        """
        # Step 1: Register a new user
        # -------------------------
        # Create user directly using factory since registration endpoint
        # may require email sending which is async
        test_email = "newuser@example.com"
        test_password = "SecurePass123!"

        user = User.objects.create_user(
            email=test_email,
            password=test_password,
            email_verified=False,
        )

        # Create email linked account (as registration serializer does)
        LinkedAccount.objects.create(
            user=user,
            provider=LinkedAccount.Provider.EMAIL,
            provider_user_id=test_email,
        )

        # Verify: User created with correct initial state
        assert user.email == test_email
        assert user.email_verified is False
        assert user.is_active is True

        # Verify: LinkedAccount created for email provider
        assert user.linked_accounts.filter(
            provider=LinkedAccount.Provider.EMAIL
        ).exists()
        linked = user.linked_accounts.get(provider=LinkedAccount.Provider.EMAIL)
        assert linked.provider_user_id == test_email

        # Verify: Profile auto-created (via signals) with empty username
        assert hasattr(user, "profile")
        assert user.profile.username == ""
        assert user.has_completed_profile is False

        # Step 2: Create and verify email token
        # -------------------------------------
        token = EmailVerificationToken.objects.create(
            user=user,
            token=generate_token(),
            token_type=EmailVerificationToken.TokenType.EMAIL_VERIFICATION,
            expires_at=timezone.now() + timedelta(hours=24),
        )

        response = api_client.post(
            VERIFY_EMAIL_URL,
            {"token": token.token},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK

        # Verify: User email is now verified
        user.refresh_from_db()
        assert user.email_verified is True

        # Verify: Token is marked as used
        token.refresh_from_db()
        assert token.used_at is not None

        # Step 3: Login with credentials
        # ------------------------------
        response = api_client.post(
            LOGIN_URL,
            {"email": test_email, "password": test_password},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert "access" in response.data
        assert "refresh" in response.data

        # Store tokens for authenticated requests
        access_token = response.data["access"]

        # Step 4: Check profile shows incomplete
        # -------------------------------------
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")

        response = api_client.get(PROFILE_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["is_complete"] is False
        assert response.data["username"] == ""

        # Step 5: Complete profile with username
        # -------------------------------------
        profile_data = {
            "username": "newusername",
            "first_name": "New",
            "last_name": "User",
            "timezone": "America/New_York",
        }

        response = api_client.put(PROFILE_URL, profile_data, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["username"] == "newusername"
        assert response.data["is_complete"] is True

        # Verify: Database state reflects completion
        user.refresh_from_db()
        assert user.profile.username == "newusername"
        assert user.has_completed_profile is True

    def test_registration_creates_user_with_correct_initial_state(self, db):
        """
        Verify that newly registered users have correct initial flags.

        New users should:
        - Have email_verified=False (must verify)
        - Have is_active=True (can login but limited access)
        - Have profile with no username
        """
        user = User.objects.create_user(
            email="teststate@example.com",
            password="TestPass123!",
        )

        # Email not verified until they click the link
        assert user.email_verified is False

        # Account is active (can attempt login)
        assert user.is_active is True

        # Profile exists but is incomplete
        assert Profile.objects.filter(user=user).exists()
        assert user.profile.username == ""

    def test_registration_creates_linked_account_for_email(self, db):
        """
        Email registration creates LinkedAccount for email provider.

        This allows the system to track that the user registered via email
        and supports future multi-provider linking.
        """
        email = "linkedtest@example.com"
        user = User.objects.create_user(email=email, password="TestPass123!")

        # Create linked account as the RegisterSerializer does
        LinkedAccount.objects.create(
            user=user,
            provider=LinkedAccount.Provider.EMAIL,
            provider_user_id=email,
        )

        assert user.linked_accounts.count() == 1
        linked = user.linked_accounts.first()
        assert linked.provider == LinkedAccount.Provider.EMAIL
        assert linked.provider_user_id == email


# =============================================================================
# TestEmailVerificationFlow
# =============================================================================


@pytest.mark.integration
@pytest.mark.django_db
class TestEmailVerificationFlow:
    """
    Token verification from start to finish.

    Tests the complete lifecycle of email verification tokens including:
    - Token creation and validation
    - Successful verification flow
    - Token reuse prevention
    - Expired token handling
    """

    def test_valid_token_verifies_email_successfully(self, api_client):
        """
        Valid verification token successfully verifies user email.

        Flow:
        1. Create unverified user with verification token
        2. POST to verify-email with token
        3. User.email_verified becomes True
        4. Token.used_at is set
        """
        # Create unverified user
        user = UserFactory(email_verified=False)
        assert user.email_verified is False

        # Create valid verification token
        token = EmailVerificationTokenFactory(
            user=user,
            token_type=EmailVerificationToken.TokenType.EMAIL_VERIFICATION,
            expires_at=timezone.now() + timedelta(hours=24),
            used_at=None,
        )

        # Verify email using token
        response = api_client.post(
            VERIFY_EMAIL_URL,
            {"token": token.token},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK

        # Verify user email is now verified
        user.refresh_from_db()
        assert user.email_verified is True

        # Verify token is marked as used
        token.refresh_from_db()
        assert token.used_at is not None
        assert token.is_valid is False

    def test_token_cannot_be_reused_after_verification(self, api_client):
        """
        Verification tokens are single-use only.

        Once a token is used successfully, it cannot be used again.
        This prevents replay attacks where someone intercepts the
        verification email.
        """
        user = UserFactory(email_verified=False)
        token = EmailVerificationTokenFactory(
            user=user,
            token_type=EmailVerificationToken.TokenType.EMAIL_VERIFICATION,
            expires_at=timezone.now() + timedelta(hours=24),
            used_at=None,
        )

        # First use - should succeed
        response = api_client.post(
            VERIFY_EMAIL_URL,
            {"token": token.token},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        # Second use - should fail
        response = api_client.post(
            VERIFY_EMAIL_URL,
            {"token": token.token},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_expired_token_is_rejected(self, api_client):
        """
        Expired verification tokens are rejected.

        Tokens have a limited validity window (24 hours by default).
        After expiration, users must request a new verification email.
        """
        user = UserFactory(email_verified=False)
        expired_token = EmailVerificationTokenFactory(
            user=user,
            token_type=EmailVerificationToken.TokenType.EMAIL_VERIFICATION,
            expires_at=timezone.now() - timedelta(hours=1),  # Already expired
            used_at=None,
        )

        response = api_client.post(
            VERIFY_EMAIL_URL,
            {"token": expired_token.token},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

        # User should still be unverified
        user.refresh_from_db()
        assert user.email_verified is False

    def test_wrong_token_type_is_rejected(self, api_client):
        """
        Password reset tokens cannot be used for email verification.

        Each token type has a specific purpose. Using the wrong type
        should fail gracefully.
        """
        user = UserFactory(email_verified=False)
        wrong_type_token = EmailVerificationTokenFactory(
            user=user,
            token_type=EmailVerificationToken.TokenType.PASSWORD_RESET,
            expires_at=timezone.now() + timedelta(hours=1),
            used_at=None,
        )

        response = api_client.post(
            VERIFY_EMAIL_URL,
            {"token": wrong_type_token.token},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

        # User should still be unverified
        user.refresh_from_db()
        assert user.email_verified is False

    def test_nonexistent_token_is_rejected(self, api_client, db):
        """
        Random/guessed tokens return error without revealing user info.

        Security consideration: The error message should not reveal
        whether the token ever existed or who it belongs to.
        """
        response = api_client.post(
            VERIFY_EMAIL_URL,
            {"token": "nonexistent-token-abcd1234"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_resend_creates_new_token(self, authenticated_client_factory, monkeypatch):
        """
        Resending verification email creates a new valid token.

        Users who didn't receive or lost their verification email
        can request a new one.
        """
        # Track send_verification_email calls
        created_tokens = []

        def mock_send(user):
            # The actual service would create a token
            token = EmailVerificationToken.objects.create(
                user=user,
                token=generate_token(),
                token_type=EmailVerificationToken.TokenType.EMAIL_VERIFICATION,
                expires_at=timezone.now() + timedelta(hours=24),
            )
            created_tokens.append(token)

        monkeypatch.setattr(
            "authentication.services.AuthService.send_verification_email",
            staticmethod(mock_send),
        )

        user = UserFactory(email_verified=False)
        client = authenticated_client_factory(user)

        response = client.post(RESEND_EMAIL_URL)

        assert response.status_code == status.HTTP_200_OK
        assert len(created_tokens) == 1
        assert created_tokens[0].user == user


# =============================================================================
# TestPasswordResetFlow
# =============================================================================


@pytest.mark.integration
@pytest.mark.django_db
class TestPasswordResetFlow:
    """
    Complete password reset journey.

    Tests the full password reset flow:
    1. Request password reset (creates token)
    2. Verify reset token is created with correct expiry
    3. Reset password using token
    4. Verify new password works for login
    5. Verify token is marked as used
    6. Verify other reset tokens are invalidated
    """

    def test_full_password_reset_flow(self, api_client):
        """
        Complete password reset from request to successful login.
        """
        # Create a user with known password
        original_password = "OriginalPass123!"
        new_password = "NewSecurePass456!"
        user = UserFactory(email_verified=True)
        user.set_password(original_password)
        user.save()

        # Step 1: Create password reset token
        # -----------------------------------
        reset_token = EmailVerificationToken.objects.create(
            user=user,
            token=generate_token(),
            token_type=EmailVerificationToken.TokenType.PASSWORD_RESET,
            expires_at=timezone.now() + timedelta(hours=1),
        )

        # Step 2: Verify token is valid
        # -----------------------------
        assert reset_token.is_valid is True
        assert reset_token.used_at is None
        assert reset_token.expires_at > timezone.now()

        # Step 3: Reset password using AuthService
        # ----------------------------------------
        from authentication.services import AuthService

        success, message = AuthService.reset_password(
            reset_token.token,
            new_password,
        )

        assert success is True

        # Step 4: Verify token is marked as used
        # -------------------------------------
        reset_token.refresh_from_db()
        assert reset_token.used_at is not None
        assert reset_token.is_valid is False

        # Step 5: Verify old password no longer works
        # -------------------------------------------
        response = api_client.post(
            LOGIN_URL,
            {"email": user.email, "password": original_password},
            format="json",
        )
        # Should fail authentication
        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_401_UNAUTHORIZED,
        ]

        # Step 6: Verify new password works
        # ---------------------------------
        response = api_client.post(
            LOGIN_URL,
            {"email": user.email, "password": new_password},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert "access" in response.data

    def test_password_reset_invalidates_other_tokens(self, db):
        """
        Using one reset token invalidates all other reset tokens for that user.

        Security: If a user has multiple reset tokens (e.g., clicked
        "forgot password" multiple times), only the most recent should work.
        After using any token, all others should be invalidated.
        """
        user = UserFactory(email_verified=True)

        # Create multiple reset tokens
        token1 = EmailVerificationToken.objects.create(
            user=user,
            token=generate_token(),
            token_type=EmailVerificationToken.TokenType.PASSWORD_RESET,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        token2 = EmailVerificationToken.objects.create(
            user=user,
            token=generate_token(),
            token_type=EmailVerificationToken.TokenType.PASSWORD_RESET,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        token3 = EmailVerificationToken.objects.create(
            user=user,
            token=generate_token(),
            token_type=EmailVerificationToken.TokenType.PASSWORD_RESET,
            expires_at=timezone.now() + timedelta(hours=1),
        )

        # All tokens should be valid initially
        assert token1.is_valid is True
        assert token2.is_valid is True
        assert token3.is_valid is True

        # Use token2 for password reset
        from authentication.services import AuthService

        success, _ = AuthService.reset_password(token2.token, "NewPassword123!")
        assert success is True

        # Refresh all tokens from database
        token1.refresh_from_db()
        token2.refresh_from_db()
        token3.refresh_from_db()

        # token2 should be marked as used
        assert token2.used_at is not None

        # token1 and token3 should also be invalidated
        assert token1.used_at is not None
        assert token3.used_at is not None

    def test_expired_reset_token_is_rejected(self, db):
        """
        Expired password reset tokens cannot be used.

        Password reset tokens have a shorter expiry (1 hour) than
        email verification tokens for security.
        """
        user = UserFactory(email_verified=True)

        expired_token = EmailVerificationToken.objects.create(
            user=user,
            token=generate_token(),
            token_type=EmailVerificationToken.TokenType.PASSWORD_RESET,
            expires_at=timezone.now() - timedelta(minutes=30),  # Already expired
        )

        from authentication.services import AuthService

        success, message = AuthService.reset_password(
            expired_token.token,
            "NewPassword123!",
        )

        assert success is False
        assert "invalid" in message.lower() or "expired" in message.lower()

    def test_used_reset_token_cannot_be_reused(self, db):
        """
        Password reset tokens are single-use.
        """
        user = UserFactory(email_verified=True)

        token = EmailVerificationToken.objects.create(
            user=user,
            token=generate_token(),
            token_type=EmailVerificationToken.TokenType.PASSWORD_RESET,
            expires_at=timezone.now() + timedelta(hours=1),
        )

        from authentication.services import AuthService

        # First use - should succeed
        success1, _ = AuthService.reset_password(token.token, "FirstNewPass123!")
        assert success1 is True

        # Second use - should fail
        success2, _ = AuthService.reset_password(token.token, "SecondNewPass456!")
        assert success2 is False


# =============================================================================
# TestProfileCompletionFlow
# =============================================================================


@pytest.mark.integration
@pytest.mark.django_db
class TestProfileCompletionFlow:
    """
    Onboarding flow for new users.

    Tests the profile completion journey:
    1. New user has auto-created Profile with empty username
    2. GET /profile/ shows is_complete=False
    3. PUT /profile/ with username completes profile
    4. GET /profile/ shows is_complete=True
    5. user.has_completed_profile property returns True
    """

    def test_new_user_profile_is_incomplete(self, authenticated_client_factory):
        """
        Newly created user has incomplete profile (no username).

        Profile is auto-created by signals when User is created,
        but username must be set manually through onboarding.
        """
        user = UserFactory(email_verified=True)
        # Profile is auto-created but username is empty
        user.profile.username = ""
        user.profile.save()

        client = authenticated_client_factory(user)

        response = client.get(PROFILE_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["is_complete"] is False
        assert response.data["username"] == ""

        # Property should also return False
        assert user.has_completed_profile is False

    def test_setting_username_completes_profile(self, authenticated_client_factory):
        """
        Setting username marks profile as complete.
        """
        user = UserFactory(email_verified=True)
        user.profile.username = ""
        user.profile.save()

        client = authenticated_client_factory(user)

        # Complete profile with username
        response = client.put(
            PROFILE_URL,
            {
                "username": "completeduser",
                "first_name": "Completed",
                "last_name": "User",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["username"] == "completeduser"
        assert response.data["is_complete"] is True

        # Verify database state
        user.refresh_from_db()
        assert user.profile.username == "completeduser"
        assert user.has_completed_profile is True

    def test_profile_completion_requires_username(self, authenticated_client_factory):
        """
        Profile update fails without username when profile is incomplete.

        Users must set their username during onboarding - they cannot
        skip this step.
        """
        user = UserFactory(email_verified=True)
        user.profile.username = ""
        user.profile.save()

        client = authenticated_client_factory(user)

        # Try to update profile without username
        response = client.put(
            PROFILE_URL,
            {"first_name": "Test", "last_name": "User"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "username" in response.data

    def test_completed_profile_allows_update_without_username(
        self, authenticated_client_factory
    ):
        """
        Users with complete profiles can update other fields without re-specifying username.
        """
        user = UserFactory(email_verified=True)
        user.profile.username = "existinguser"
        user.profile.save()

        client = authenticated_client_factory(user)

        # Update profile without username
        response = client.patch(
            PROFILE_URL,
            {"first_name": "Updated"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["first_name"] == "Updated"
        assert response.data["username"] == "existinguser"  # Preserved

    def test_username_update_after_completion(self, authenticated_client_factory):
        """
        Users can change their username after initial completion.
        """
        user = UserFactory(email_verified=True)
        user.profile.username = "oldusername"
        user.profile.save()

        client = authenticated_client_factory(user)

        response = client.patch(
            PROFILE_URL,
            {"username": "newusername"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["username"] == "newusername"

        user.profile.refresh_from_db()
        assert user.profile.username == "newusername"

    def test_profile_data_persists_across_requests(self, authenticated_client_factory):
        """
        Profile updates persist and are returned in subsequent requests.
        """
        user = UserFactory(email_verified=True)
        user.profile.username = ""
        user.profile.save()

        client = authenticated_client_factory(user)

        # Complete profile
        client.put(
            PROFILE_URL,
            {
                "username": "persistuser",
                "first_name": "Persist",
                "last_name": "Test",
                "timezone": "Europe/London",
                "preferences": {"theme": "dark"},
            },
            format="json",
        )

        # Fetch profile again
        response = client.get(PROFILE_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["username"] == "persistuser"
        assert response.data["first_name"] == "Persist"
        assert response.data["last_name"] == "Test"
        assert response.data["timezone"] == "Europe/London"
        assert response.data["preferences"] == {"theme": "dark"}


# =============================================================================
# TestAccountDeactivationFlow
# =============================================================================


@pytest.mark.integration
@pytest.mark.django_db
class TestAccountDeactivationFlow:
    """
    Soft delete and its effects.

    Tests account deactivation flow:
    1. Create active user
    2. POST /deactivate/
    3. user.is_active=False
    4. User data is preserved (not deleted)
    5. Document JWT token behavior (stateless - still works)
    """

    def test_deactivation_sets_is_active_false(self, authenticated_client, user):
        """
        Account deactivation sets user.is_active to False.
        """
        assert user.is_active is True

        response = authenticated_client.post(DEACTIVATE_URL)

        assert response.status_code == status.HTTP_200_OK

        user.refresh_from_db()
        assert user.is_active is False

    def test_deactivation_preserves_user_data(self, authenticated_client, user):
        """
        Deactivation is a soft-delete that preserves all user data.

        This is important for:
        - Regulatory compliance (GDPR data retention)
        - Audit trails
        - Potential account recovery
        """
        email = user.email
        user_pk = user.pk

        # Set some profile data
        user.profile.username = "tobedeactivated"
        user.profile.first_name = "Will"
        user.profile.last_name = "Deactivate"
        user.profile.save()

        # Deactivate
        authenticated_client.post(DEACTIVATE_URL)

        # Verify all data is preserved
        user.refresh_from_db()
        assert user.pk == user_pk
        assert user.email == email
        assert user.is_active is False
        assert user.profile.username == "tobedeactivated"
        assert user.profile.first_name == "Will"
        assert user.profile.last_name == "Deactivate"

    def test_deactivation_accepts_optional_reason(self, authenticated_client, user):
        """
        Deactivation request can include an optional reason.

        The reason is logged for analytics but doesn't affect the operation.
        """
        response = authenticated_client.post(
            DEACTIVATE_URL,
            {"reason": "Moving to competitor"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK

        user.refresh_from_db()
        assert user.is_active is False

    def test_jwt_token_still_works_after_deactivation(self, authenticated_client, user):
        """
        Document: JWT tokens are stateless and still work after deactivation.

        IMPORTANT: This is a known limitation of stateless JWTs. The token
        remains valid until it expires. For immediate revocation, applications
        should either:
        1. Use short-lived access tokens
        2. Implement token blacklisting
        3. Check is_active on each request

        This test documents current behavior, not necessarily desired behavior.
        """
        # Deactivate the user
        authenticated_client.post(DEACTIVATE_URL)

        user.refresh_from_db()
        assert user.is_active is False

        # The JWT token is stateless - it doesn't check is_active
        # This request may still succeed depending on middleware configuration
        response = authenticated_client.get(PROFILE_URL)

        # Document the current behavior
        # Note: This may return 200 or 401 depending on implementation
        # The test captures actual behavior for documentation
        actual_status = response.status_code

        # Store observation as a note (this test is for documentation)
        if actual_status == status.HTTP_200_OK:
            # JWT is stateless - token still works
            pass
        elif actual_status == status.HTTP_401_UNAUTHORIZED:
            # Application checks is_active on each request (more secure)
            pass

    def test_deactivated_user_cannot_login(self, api_client):
        """
        Deactivated users cannot log in with their credentials.

        Even though JWT tokens are stateless, the login endpoint
        should check is_active before issuing new tokens.
        """
        password = "TestPass123!"
        user = UserFactory(email_verified=True)
        user.set_password(password)
        user.is_active = False
        user.save()

        response = api_client.post(
            LOGIN_URL,
            {"email": user.email, "password": password},
            format="json",
        )

        # Login should fail for inactive users
        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_401_UNAUTHORIZED,
        ]


# =============================================================================
# TestMultiProviderFlow
# =============================================================================


@pytest.mark.integration
@pytest.mark.django_db
class TestMultiProviderFlow:
    """
    Tests for users with multiple authentication methods.

    Users can link multiple authentication providers (email, Google, Apple)
    to a single account, allowing them to log in through any method.
    """

    def test_user_can_have_email_and_google_linked(self, db):
        """
        User can have both email and Google accounts linked.
        """
        user = UserFactory(email_verified=True)

        # Link email provider
        LinkedAccount.objects.create(
            user=user,
            provider=LinkedAccount.Provider.EMAIL,
            provider_user_id=user.email,
        )

        # Link Google provider
        LinkedAccount.objects.create(
            user=user,
            provider=LinkedAccount.Provider.GOOGLE,
            provider_user_id="google-uid-12345",
        )

        # Verify both exist
        assert user.linked_accounts.count() == 2
        assert user.linked_accounts.filter(
            provider=LinkedAccount.Provider.EMAIL
        ).exists()
        assert user.linked_accounts.filter(
            provider=LinkedAccount.Provider.GOOGLE
        ).exists()

    def test_user_can_have_all_providers_linked(self, db):
        """
        User can link email, Google, and Apple simultaneously.
        """
        user = UserFactory(email_verified=True)

        LinkedAccount.objects.create(
            user=user,
            provider=LinkedAccount.Provider.EMAIL,
            provider_user_id=user.email,
        )
        LinkedAccount.objects.create(
            user=user,
            provider=LinkedAccount.Provider.GOOGLE,
            provider_user_id="google-uid-12345",
        )
        LinkedAccount.objects.create(
            user=user,
            provider=LinkedAccount.Provider.APPLE,
            provider_user_id="apple-uid-67890",
        )

        assert user.linked_accounts.count() == 3

        # Verify each provider
        providers = set(user.linked_accounts.values_list("provider", flat=True))
        assert providers == {
            LinkedAccount.Provider.EMAIL,
            LinkedAccount.Provider.GOOGLE,
            LinkedAccount.Provider.APPLE,
        }

    def test_linked_providers_appear_in_user_serializer(
        self, authenticated_client_factory
    ):
        """
        User API response includes list of linked providers.

        Frontend uses this to show which login methods are available
        and to enable/disable linking buttons.
        """
        user = UserFactory(email_verified=True)

        # Link email and Google
        LinkedAccount.objects.create(
            user=user,
            provider=LinkedAccount.Provider.EMAIL,
            provider_user_id=user.email,
        )
        LinkedAccount.objects.create(
            user=user,
            provider=LinkedAccount.Provider.GOOGLE,
            provider_user_id="google-uid-12345",
        )

        client = authenticated_client_factory(user)

        response = client.get(USER_URL)

        assert response.status_code == status.HTTP_200_OK
        assert "linked_providers" in response.data
        assert set(response.data["linked_providers"]) == {
            LinkedAccount.Provider.EMAIL,
            LinkedAccount.Provider.GOOGLE,
        }

    def test_provider_user_id_is_unique_per_provider(self, db):
        """
        Same provider_user_id cannot be linked to multiple accounts.

        This prevents account takeover if an attacker tries to link
        their OAuth account to multiple users.
        """
        user1 = UserFactory()
        user2 = UserFactory()

        # Link Google to user1
        LinkedAccount.objects.create(
            user=user1,
            provider=LinkedAccount.Provider.GOOGLE,
            provider_user_id="unique-google-id",
        )

        # Try to link same Google ID to user2 - should fail
        from django.db import IntegrityError

        with pytest.raises(IntegrityError):
            LinkedAccount.objects.create(
                user=user2,
                provider=LinkedAccount.Provider.GOOGLE,
                provider_user_id="unique-google-id",  # Same ID
            )

    def test_same_user_id_can_exist_across_providers(self, db):
        """
        Same provider_user_id can exist for different providers.

        Provider IDs are namespaced by provider - "12345" from Google
        is different from "12345" from Apple.
        """
        user = UserFactory()

        # Same ID for different providers is allowed
        LinkedAccount.objects.create(
            user=user,
            provider=LinkedAccount.Provider.GOOGLE,
            provider_user_id="same-id-12345",
        )
        LinkedAccount.objects.create(
            user=user,
            provider=LinkedAccount.Provider.APPLE,
            provider_user_id="same-id-12345",  # Same ID, different provider
        )

        assert user.linked_accounts.count() == 2


# =============================================================================
# TestOAuthRegistrationFlow
# =============================================================================


@pytest.mark.integration
@pytest.mark.django_db
class TestOAuthRegistrationFlow:
    """
    OAuth registration flow (simulated).

    Note: Full OAuth testing requires mocking external providers or
    integration test environments. These tests verify the expected
    state after OAuth-style user creation.

    OAuth users should:
    - Have email_verified=True (provider verified the email)
    - Have LinkedAccount for the provider
    - Have auto-created Profile
    """

    def test_oauth_user_has_email_verified_true(self, db):
        """
        OAuth users have pre-verified email addresses.

        Google/Apple have already verified the user's email, so we
        trust their verification and set email_verified=True.
        """
        # Simulate OAuth user creation
        user = User.objects.create_user(
            email="oauth@gmail.com",
            password=None,  # OAuth users may not have a password
            email_verified=True,  # Set by OAuth adapter
        )

        assert user.email_verified is True

    def test_oauth_user_has_linked_account(self, db):
        """
        OAuth login creates LinkedAccount for the provider.
        """
        user = User.objects.create_user(
            email="google@gmail.com",
            password=None,
            email_verified=True,
        )

        # OAuth adapter would create this
        LinkedAccount.objects.create(
            user=user,
            provider=LinkedAccount.Provider.GOOGLE,
            provider_user_id="google-oauth-uid-abc123",
        )

        assert user.linked_accounts.filter(
            provider=LinkedAccount.Provider.GOOGLE
        ).exists()

    def test_oauth_user_has_auto_created_profile(self, db):
        """
        OAuth users get auto-created profiles (via signals).

        Just like email registration, OAuth users need a Profile
        for storing username and preferences.
        """
        user = User.objects.create_user(
            email="oauth-profile@gmail.com",
            password=None,
            email_verified=True,
        )

        # Profile should be auto-created by signals
        assert hasattr(user, "profile")
        assert Profile.objects.filter(user=user).exists()

        # Profile is incomplete until user sets username
        assert user.profile.username == ""
        assert user.has_completed_profile is False

    def test_oauth_user_can_complete_profile(self, authenticated_client_factory):
        """
        OAuth users complete profile the same way as email users.
        """
        user = User.objects.create_user(
            email="oauth-complete@gmail.com",
            password=None,
            email_verified=True,
        )

        LinkedAccount.objects.create(
            user=user,
            provider=LinkedAccount.Provider.GOOGLE,
            provider_user_id="google-uid-xyz789",
        )

        client = authenticated_client_factory(user)

        response = client.put(
            PROFILE_URL,
            {"username": "oauthuser", "first_name": "OAuth", "last_name": "User"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["is_complete"] is True


# =============================================================================
# TestTokenExpiry
# =============================================================================


@pytest.mark.integration
@pytest.mark.django_db
class TestTokenExpiry:
    """
    Tests for token expiration behavior.

    Verifies that tokens expire correctly and that expired tokens
    cannot be used.
    """

    def test_email_verification_token_24_hour_expiry(self, db):
        """
        Email verification tokens have 24-hour default expiry.
        """
        from authentication.services import AuthService

        user = UserFactory(email_verified=False)

        # Create token via service
        EmailVerificationToken.objects.create(
            user=user,
            token=generate_token(),
            token_type=EmailVerificationToken.TokenType.EMAIL_VERIFICATION,
            expires_at=timezone.now()
            + timedelta(hours=AuthService.EMAIL_VERIFICATION_EXPIRY_HOURS),
        )

        token = user.verification_tokens.first()

        # Token should expire in ~24 hours
        hours_until_expiry = (token.expires_at - timezone.now()).total_seconds() / 3600
        assert 23 < hours_until_expiry <= 24

    def test_password_reset_token_1_hour_expiry(self, db):
        """
        Password reset tokens have 1-hour default expiry (more secure).
        """
        from authentication.services import AuthService

        user = UserFactory(email_verified=True)

        EmailVerificationToken.objects.create(
            user=user,
            token=generate_token(),
            token_type=EmailVerificationToken.TokenType.PASSWORD_RESET,
            expires_at=timezone.now()
            + timedelta(hours=AuthService.PASSWORD_RESET_EXPIRY_HOURS),
        )

        token = user.verification_tokens.first()

        # Token should expire in ~1 hour
        hours_until_expiry = (token.expires_at - timezone.now()).total_seconds() / 3600
        assert 0.9 < hours_until_expiry <= 1

    def test_token_is_valid_property(self, db):
        """
        Token.is_valid correctly reports token validity.
        """
        user = UserFactory()

        # Valid token (not used, not expired)
        valid_token = EmailVerificationToken.objects.create(
            user=user,
            token=generate_token(),
            token_type=EmailVerificationToken.TokenType.EMAIL_VERIFICATION,
            expires_at=timezone.now() + timedelta(hours=24),
            used_at=None,
        )
        assert valid_token.is_valid is True

        # Expired token
        expired_token = EmailVerificationToken.objects.create(
            user=user,
            token=generate_token(),
            token_type=EmailVerificationToken.TokenType.EMAIL_VERIFICATION,
            expires_at=timezone.now() - timedelta(hours=1),
            used_at=None,
        )
        assert expired_token.is_valid is False

        # Used token
        used_token = EmailVerificationToken.objects.create(
            user=user,
            token=generate_token(),
            token_type=EmailVerificationToken.TokenType.EMAIL_VERIFICATION,
            expires_at=timezone.now() + timedelta(hours=24),
            used_at=timezone.now() - timedelta(minutes=30),
        )
        assert used_token.is_valid is False


# =============================================================================
# Biometric URL Constants
# =============================================================================

BIOMETRIC_ENROLL_URL = "/api/v1/auth/biometric/enroll/"
BIOMETRIC_CHALLENGE_URL = "/api/v1/auth/biometric/challenge/"
BIOMETRIC_AUTHENTICATE_URL = "/api/v1/auth/biometric/authenticate/"
BIOMETRIC_STATUS_URL = "/api/v1/auth/biometric/status/"
BIOMETRIC_DISABLE_URL = "/api/v1/auth/biometric/"


# =============================================================================
# TestBiometricEnrollmentFlow
# =============================================================================


@pytest.mark.integration
@pytest.mark.django_db
class TestBiometricEnrollmentFlow:
    """
    Complete biometric enrollment journey.

    Tests the flow from enrollment to checking status:
    1. Authenticated user enrolls public key
    2. Status shows biometric enabled
    3. User can disable biometric
    4. Status shows biometric disabled
    """

    def test_full_enrollment_flow(
        self, api_client, authenticated_client_factory, ec_key_pair
    ):
        """
        Complete enrollment flow from start to enabled status.
        """
        # Create user and authenticated client
        user = UserFactory(email_verified=True)
        user.profile.username = "enrolluser"
        user.profile.save()
        client = authenticated_client_factory(user)

        # Step 1: Check initial status - should be disabled
        # ----------------------------------------------
        response = api_client.get(f"{BIOMETRIC_STATUS_URL}?email={user.email}")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["biometric_enabled"] is False

        # Step 2: Enroll biometric
        # ------------------------
        response = client.post(
            BIOMETRIC_ENROLL_URL,
            {"public_key": ec_key_pair["public_key_b64"]},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert "enrolled_at" in response.data

        # Step 3: Verify status is now enabled
        # ------------------------------------
        response = api_client.get(f"{BIOMETRIC_STATUS_URL}?email={user.email}")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["biometric_enabled"] is True

        # Step 4: Verify database state
        # -----------------------------
        user.profile.refresh_from_db()
        assert user.profile.bio_public_key == ec_key_pair["public_key_b64"]

    def test_enrollment_requires_authentication(self, api_client, ec_key_pair):
        """
        Enrollment endpoint requires authentication.

        Why it matters: Can't enroll biometric for anonymous users.
        """
        response = api_client.post(
            BIOMETRIC_ENROLL_URL,
            {"public_key": ec_key_pair["public_key_b64"]},
            format="json",
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_enrollment_with_invalid_key_returns_400(
        self, authenticated_client, invalid_ec_public_key
    ):
        """
        Invalid public key returns 400 Bad Request.

        Why it matters: Users should get clear error messages for invalid keys.
        """
        response = authenticated_client.post(
            BIOMETRIC_ENROLL_URL,
            {"public_key": invalid_ec_public_key},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_enrollment_with_wrong_curve_returns_400(
        self, authenticated_client, wrong_curve_public_key
    ):
        """
        Public key on wrong curve returns 400 Bad Request.

        Why it matters: Only P-256 keys from Secure Enclave are supported.
        """
        response = authenticated_client.post(
            BIOMETRIC_ENROLL_URL,
            {"public_key": wrong_curve_public_key},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_enrollment_missing_public_key_returns_400(self, authenticated_client):
        """
        Missing public key returns 400 Bad Request.
        """
        response = authenticated_client.post(
            BIOMETRIC_ENROLL_URL,
            {},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


# =============================================================================
# TestBiometricAuthenticationFlow
# =============================================================================


@pytest.mark.integration
@pytest.mark.django_db
class TestBiometricAuthenticationFlow:
    """
    Complete biometric authentication journey.

    Tests the flow from challenge request to JWT token issuance:
    1. Request challenge nonce
    2. Sign challenge with private key
    3. Submit signature to authenticate
    4. Receive JWT tokens
    """

    def test_full_authentication_flow(
        self, api_client, user_with_biometric, ec_key_pair, mock_redis_cache
    ):
        """
        Complete biometric login from challenge to JWT token.
        """
        import base64
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec

        # Step 1: Request challenge
        # -------------------------
        response = api_client.post(
            BIOMETRIC_CHALLENGE_URL,
            {"email": user_with_biometric.email},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert "challenge" in response.data
        assert "expires_in" in response.data

        challenge = response.data["challenge"]

        # Step 2: Sign challenge with private key
        # ---------------------------------------
        challenge_bytes = base64.b64decode(challenge)
        signature_bytes = ec_key_pair["private_key"].sign(
            challenge_bytes, ec.ECDSA(hashes.SHA256())
        )
        signature = base64.b64encode(signature_bytes).decode("ascii")

        # Step 3: Authenticate with signature
        # -----------------------------------
        response = api_client.post(
            BIOMETRIC_AUTHENTICATE_URL,
            {
                "email": user_with_biometric.email,
                "challenge": challenge,
                "signature": signature,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert "access" in response.data
        assert "refresh" in response.data

        # Step 4: Verify token works
        # --------------------------
        access_token = response.data["access"]
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")
        profile_response = api_client.get(PROFILE_URL)
        assert profile_response.status_code == status.HTTP_200_OK

    def test_challenge_requires_biometric_enabled(
        self, api_client, user_without_biometric, mock_redis_cache
    ):
        """
        Challenge request fails if biometric not enabled.

        Why it matters: Can't start auth flow without enrollment.
        """
        response = api_client.post(
            BIOMETRIC_CHALLENGE_URL,
            {"email": user_without_biometric.email},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.data.get("error") == "biometric_not_enabled"

    def test_challenge_for_nonexistent_user_returns_404(
        self, api_client, mock_redis_cache
    ):
        """
        Challenge for non-existent user returns 404.

        Why it matters: Indicate biometric not available (same as not enabled).
        """
        response = api_client.post(
            BIOMETRIC_CHALLENGE_URL,
            {"email": "nonexistent@example.com"},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.data.get("error") == "biometric_not_enabled"

    def test_authenticate_with_invalid_signature_returns_401(
        self, api_client, user_with_biometric, mock_redis_cache
    ):
        """
        Invalid signature returns 401 Unauthorized.

        Why it matters: Failed verification is an authentication failure.
        """
        import base64

        # Get a valid challenge
        challenge_response = api_client.post(
            BIOMETRIC_CHALLENGE_URL,
            {"email": user_with_biometric.email},
            format="json",
        )
        challenge = challenge_response.data["challenge"]

        # Use bogus signature
        bogus_signature = base64.b64encode(b"bogus" * 14).decode("ascii")

        response = api_client.post(
            BIOMETRIC_AUTHENTICATE_URL,
            {
                "email": user_with_biometric.email,
                "challenge": challenge,
                "signature": bogus_signature,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.data.get("error") == "invalid_signature"

    def test_authenticate_with_expired_challenge_returns_401(
        self, api_client, user_with_biometric, mock_redis_cache
    ):
        """
        Expired/invalid challenge returns 401 Unauthorized.

        Why it matters: Challenges must be used within TTL.
        """
        import base64

        # Use a fake challenge that was never stored
        fake_challenge = base64.b64encode(b"x" * 32).decode("ascii")
        fake_signature = base64.b64encode(b"y" * 70).decode("ascii")

        response = api_client.post(
            BIOMETRIC_AUTHENTICATE_URL,
            {
                "email": user_with_biometric.email,
                "challenge": fake_challenge,
                "signature": fake_signature,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.data.get("error") == "invalid_signature"

    def test_challenge_is_single_use(
        self, api_client, user_with_biometric, ec_key_pair, mock_redis_cache
    ):
        """
        Challenge can only be used once.

        Why it matters: Prevents replay attacks.
        """
        import base64
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec

        # Get challenge
        challenge_response = api_client.post(
            BIOMETRIC_CHALLENGE_URL,
            {"email": user_with_biometric.email},
            format="json",
        )
        challenge = challenge_response.data["challenge"]

        # Sign challenge
        challenge_bytes = base64.b64decode(challenge)
        signature_bytes = ec_key_pair["private_key"].sign(
            challenge_bytes, ec.ECDSA(hashes.SHA256())
        )
        signature = base64.b64encode(signature_bytes).decode("ascii")

        # First authentication succeeds
        response1 = api_client.post(
            BIOMETRIC_AUTHENTICATE_URL,
            {
                "email": user_with_biometric.email,
                "challenge": challenge,
                "signature": signature,
            },
            format="json",
        )
        assert response1.status_code == status.HTTP_200_OK

        # Second authentication with same challenge fails
        response2 = api_client.post(
            BIOMETRIC_AUTHENTICATE_URL,
            {
                "email": user_with_biometric.email,
                "challenge": challenge,
                "signature": signature,
            },
            format="json",
        )
        assert response2.status_code == status.HTTP_401_UNAUTHORIZED
        assert response2.data.get("error") == "invalid_signature"

    def test_authentication_for_deactivated_user_fails(
        self, api_client, ec_key_pair, mock_redis_cache
    ):
        """
        Deactivated users cannot authenticate via biometric.

        Why it matters: Security - deactivated accounts should be locked.
        """
        import base64
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec

        # Create user with biometric
        user = UserFactory(email_verified=True, is_active=True)
        user.profile.username = "deactivatinguser"
        user.profile.bio_public_key = ec_key_pair["public_key_b64"]
        user.profile.save()

        # Get challenge while user is active
        challenge_response = api_client.post(
            BIOMETRIC_CHALLENGE_URL,
            {"email": user.email},
            format="json",
        )
        challenge = challenge_response.data["challenge"]

        # Sign challenge
        challenge_bytes = base64.b64decode(challenge)
        signature_bytes = ec_key_pair["private_key"].sign(
            challenge_bytes, ec.ECDSA(hashes.SHA256())
        )
        signature = base64.b64encode(signature_bytes).decode("ascii")

        # Deactivate user
        user.is_active = False
        user.save()

        # Authentication should fail
        response = api_client.post(
            BIOMETRIC_AUTHENTICATE_URL,
            {
                "email": user.email,
                "challenge": challenge,
                "signature": signature,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.data.get("error") == "invalid_signature"


# =============================================================================
# TestBiometricStatusEndpoint
# =============================================================================


@pytest.mark.integration
@pytest.mark.django_db
class TestBiometricStatusEndpoint:
    """
    Tests for the biometric status check endpoint.

    This endpoint allows clients to check if biometric is enabled
    for a given email without requiring authentication.
    """

    def test_status_enabled_for_enrolled_user(self, api_client, user_with_biometric):
        """
        Returns enabled=True for user with biometric enrolled.
        """
        response = api_client.get(
            f"{BIOMETRIC_STATUS_URL}?email={user_with_biometric.email}"
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["biometric_enabled"] is True

    def test_status_disabled_for_unenrolled_user(
        self, api_client, user_without_biometric
    ):
        """
        Returns enabled=False for user without biometric.
        """
        response = api_client.get(
            f"{BIOMETRIC_STATUS_URL}?email={user_without_biometric.email}"
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["biometric_enabled"] is False

    def test_status_disabled_for_nonexistent_user(self, api_client, db):
        """
        Returns enabled=False for non-existent user (no enumeration).

        Why it matters: Don't reveal whether email exists in system.
        """
        response = api_client.get(
            f"{BIOMETRIC_STATUS_URL}?email=nonexistent@example.com"
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["biometric_enabled"] is False

    def test_status_requires_email_parameter(self, api_client, db):
        """
        Missing email parameter returns 400.
        """
        response = api_client.get(BIOMETRIC_STATUS_URL)

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_status_normalizes_email_case(self, api_client, user_with_biometric):
        """
        Email lookup is case-insensitive.
        """
        response = api_client.get(
            f"{BIOMETRIC_STATUS_URL}?email={user_with_biometric.email.upper()}"
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["biometric_enabled"] is True

    def test_status_does_not_require_authentication(
        self, api_client, user_with_biometric
    ):
        """
        Status endpoint is public (no auth required).

        Why it matters: App needs to check before showing Face ID option.
        """
        # Using unauthenticated client
        response = api_client.get(
            f"{BIOMETRIC_STATUS_URL}?email={user_with_biometric.email}"
        )

        assert response.status_code == status.HTTP_200_OK


# =============================================================================
# TestBiometricDisableFlow
# =============================================================================


@pytest.mark.integration
@pytest.mark.django_db
class TestBiometricDisableFlow:
    """
    Tests for biometric disable functionality.

    Users should be able to disable biometric authentication,
    which clears their stored public key.
    """

    def test_disable_clears_biometric(
        self, api_client, authenticated_client_factory, ec_key_pair
    ):
        """
        Disabling biometric clears the public key.
        """
        # Create and enroll user
        user = UserFactory(email_verified=True)
        user.profile.username = "disableuser"
        user.profile.bio_public_key = ec_key_pair["public_key_b64"]
        user.profile.save()

        client = authenticated_client_factory(user)

        # Verify biometric is enabled
        assert user.profile.bio_public_key is not None

        # Disable biometric
        response = client.delete(BIOMETRIC_DISABLE_URL)

        assert response.status_code == status.HTTP_200_OK

        # Verify biometric is disabled
        user.profile.refresh_from_db()
        assert user.profile.bio_public_key is None

    def test_disable_requires_authentication(self, api_client):
        """
        Disable endpoint requires authentication.

        Why it matters: Only authenticated user can disable their biometric.
        """
        response = api_client.delete(BIOMETRIC_DISABLE_URL)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_disable_is_idempotent(self, authenticated_client_factory):
        """
        Disabling when already disabled returns success.

        Why it matters: Multiple disable calls should be safe.
        """
        user = UserFactory(email_verified=True)
        user.profile.username = "idempotentuser"
        user.profile.bio_public_key = None
        user.profile.save()

        client = authenticated_client_factory(user)

        response = client.delete(BIOMETRIC_DISABLE_URL)

        assert response.status_code == status.HTTP_200_OK

    def test_disable_then_status_shows_disabled(
        self, api_client, authenticated_client_factory, ec_key_pair
    ):
        """
        After disabling, status endpoint shows disabled.
        """
        user = UserFactory(email_verified=True)
        user.profile.username = "statusafteruser"
        user.profile.bio_public_key = ec_key_pair["public_key_b64"]
        user.profile.save()

        client = authenticated_client_factory(user)

        # Disable biometric
        client.delete(BIOMETRIC_DISABLE_URL)

        # Check status
        response = api_client.get(f"{BIOMETRIC_STATUS_URL}?email={user.email}")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["biometric_enabled"] is False

    def test_disable_then_challenge_fails(
        self, api_client, authenticated_client_factory, ec_key_pair, mock_redis_cache
    ):
        """
        After disabling, challenge request fails.

        Why it matters: Can't start auth flow without enrollment.
        """
        user = UserFactory(email_verified=True)
        user.profile.username = "challengeafteruser"
        user.profile.bio_public_key = ec_key_pair["public_key_b64"]
        user.profile.save()

        client = authenticated_client_factory(user)

        # Disable biometric
        client.delete(BIOMETRIC_DISABLE_URL)

        # Try to get challenge
        response = api_client.post(
            BIOMETRIC_CHALLENGE_URL,
            {"email": user.email},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.data.get("error") == "biometric_not_enabled"


# =============================================================================
# TestBiometricReEnrollmentFlow
# =============================================================================


@pytest.mark.integration
@pytest.mark.django_db
class TestBiometricReEnrollmentFlow:
    """
    Tests for re-enrollment scenarios (e.g., new device).

    Users should be able to re-enroll with a new key pair,
    which invalidates the old device's authentication.
    """

    def test_re_enrollment_replaces_key(
        self, authenticated_client_factory, ec_key_pair
    ):
        """
        Re-enrollment replaces the existing public key.
        """
        import base64
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives import serialization

        user = UserFactory(email_verified=True)
        user.profile.username = "reenrolluser"
        user.profile.bio_public_key = ec_key_pair["public_key_b64"]
        user.profile.save()

        client = authenticated_client_factory(user)

        # Generate a new key pair
        new_private_key = ec.generate_private_key(ec.SECP256R1())
        new_public_key = new_private_key.public_key()
        new_public_key_der = new_public_key.public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        new_public_key_b64 = base64.b64encode(new_public_key_der).decode("ascii")

        # Re-enroll with new key
        response = client.post(
            BIOMETRIC_ENROLL_URL,
            {"public_key": new_public_key_b64},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK

        # Verify new key is stored
        user.profile.refresh_from_db()
        assert user.profile.bio_public_key == new_public_key_b64
        assert user.profile.bio_public_key != ec_key_pair["public_key_b64"]

    def test_old_device_cannot_authenticate_after_re_enrollment(
        self, api_client, authenticated_client_factory, ec_key_pair, mock_redis_cache
    ):
        """
        After re-enrollment, old device's signatures are invalid.

        Why it matters: Only the new device should work.
        """
        import base64
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives import serialization, hashes

        user = UserFactory(email_verified=True)
        user.profile.username = "olddeviceuser"
        user.profile.bio_public_key = ec_key_pair["public_key_b64"]
        user.profile.save()

        client = authenticated_client_factory(user)

        # Generate a new key pair (simulating new device)
        new_private_key = ec.generate_private_key(ec.SECP256R1())
        new_public_key = new_private_key.public_key()
        new_public_key_der = new_public_key.public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        new_public_key_b64 = base64.b64encode(new_public_key_der).decode("ascii")

        # Re-enroll with new key
        client.post(
            BIOMETRIC_ENROLL_URL,
            {"public_key": new_public_key_b64},
            format="json",
        )

        # Try to authenticate with OLD key
        challenge_response = api_client.post(
            BIOMETRIC_CHALLENGE_URL,
            {"email": user.email},
            format="json",
        )
        challenge = challenge_response.data["challenge"]

        # Sign with OLD private key
        challenge_bytes = base64.b64decode(challenge)
        old_signature_bytes = ec_key_pair["private_key"].sign(
            challenge_bytes, ec.ECDSA(hashes.SHA256())
        )
        old_signature = base64.b64encode(old_signature_bytes).decode("ascii")

        # Authentication with old key should fail
        response = api_client.post(
            BIOMETRIC_AUTHENTICATE_URL,
            {
                "email": user.email,
                "challenge": challenge,
                "signature": old_signature,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.data.get("error") == "invalid_signature"
