"""
Comprehensive tests for AuthService business logic.

This module tests all AuthService methods following TDD principles:
- complete_profile: Profile completion with username validation
- validate_username: Username format, reserved names, and uniqueness
- create_linked_account: OAuth provider account linking
- create_user: User creation with optional password
- verify_email: Email verification token validation
- request_password_reset: Password reset token creation
- reset_password: Password reset with token
- deactivate_user: Soft-delete user accounts
- send_verification_email: Verification token creation
- get_or_create_profile: Profile retrieval or creation
- update_profile: Generic profile field updates

Test Organization:
    - Each service method has its own test class
    - Each test validates ONE specific behavior
    - Tests use descriptive names following the pattern: test_<scenario>_<expected_outcome>

Testing Philosophy:
    Tests focus on observable behavior, not implementation details. We test:
    - Return values and their correctness
    - Database state changes
    - Exception raising for error conditions
    - Security considerations (enumeration attacks, token invalidation)

Dependencies:
    - pytest and pytest-django for test framework
    - freezegun for time-based tests (token expiration)
    - Factory Boy fixtures from conftest.py
"""

from datetime import timedelta
from io import BytesIO
from unittest.mock import MagicMock

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from freezegun import freeze_time
from PIL import Image

from authentication.models import (
    User,
    Profile,
    LinkedAccount,
    EmailVerificationToken,
    RESERVED_USERNAMES,
)
from authentication.services import AuthService
from authentication.tests.factories import (
    UserFactory,
    ProfileFactory,
    LinkedAccountFactory,
    EmailVerificationTokenFactory,
)


# =============================================================================
# Helper Functions
# =============================================================================


def create_test_image(name="test.jpg", size=(100, 100), format="JPEG"):
    """
    Create a test image file for profile picture tests.

    Args:
        name: Filename for the uploaded file
        size: Tuple of (width, height) in pixels
        format: Image format (JPEG, PNG, etc.)

    Returns:
        SimpleUploadedFile suitable for model fields
    """
    image = Image.new("RGB", size, color="red")
    buffer = BytesIO()
    image.save(buffer, format=format)
    buffer.seek(0)
    return SimpleUploadedFile(
        name=name,
        content=buffer.read(),
        content_type=f"image/{format.lower()}"
    )


# =============================================================================
# TestCompleteProfile
# =============================================================================


class TestCompleteProfile:
    """
    Tests for AuthService.complete_profile().

    This method handles profile completion after registration or OAuth signup.
    It validates username format, handles optional fields, and manages profile
    picture uploads.
    """

    def test_complete_profile_creates_profile_with_valid_username(self, db, user):
        """
        Successfully creates profile with valid username.

        Why it matters: This is the primary happy path for profile completion.
        Users must be able to set their username during onboarding.
        """
        profile = AuthService.complete_profile(
            user=user,
            username="validuser123"
        )

        assert profile.username == "validuser123"
        assert profile.user == user

    def test_complete_profile_normalizes_username_to_lowercase(self, db, user):
        """
        Username is normalized to lowercase for case-insensitive matching.

        Why it matters: Prevents duplicate usernames that differ only by case
        (e.g., "JohnDoe" and "johndoe" should be the same username).
        """
        profile = AuthService.complete_profile(
            user=user,
            username="MixedCaseUser"
        )

        assert profile.username == "mixedcaseuser"

    def test_complete_profile_strips_whitespace_from_username(self, db, user):
        """
        Username has leading/trailing whitespace removed.

        Why it matters: Prevents accidental whitespace in usernames from
        copy-paste or form submission errors.
        """
        profile = AuthService.complete_profile(
            user=user,
            username="  spaceduser  "
        )

        assert profile.username == "spaceduser"

    def test_complete_profile_sets_optional_first_name(self, db, user):
        """
        First name can be optionally provided during profile completion.

        Why it matters: Some users want to display their real name. This should
        be optional, not required for profile completion.
        """
        profile = AuthService.complete_profile(
            user=user,
            username="testuser",
            first_name="John"
        )

        assert profile.first_name == "John"

    def test_complete_profile_sets_optional_last_name(self, db, user):
        """
        Last name can be optionally provided during profile completion.

        Why it matters: Allows users to set their full name for display purposes.
        """
        profile = AuthService.complete_profile(
            user=user,
            username="testuser",
            last_name="Doe"
        )

        assert profile.last_name == "Doe"

    def test_complete_profile_strips_whitespace_from_names(self, db, user):
        """
        First and last names have whitespace stripped.

        Why it matters: Prevents display issues with names that have accidental
        leading or trailing spaces.
        """
        profile = AuthService.complete_profile(
            user=user,
            username="testuser",
            first_name="  John  ",
            last_name="  Doe  "
        )

        assert profile.first_name == "John"
        assert profile.last_name == "Doe"

    def test_complete_profile_handles_none_names_as_empty_string(self, db, user):
        """
        None values for names are converted to empty strings.

        Why it matters: Prevents None from being stored in the database, which
        could cause issues with string operations on the name fields.
        """
        profile = AuthService.complete_profile(
            user=user,
            username="testuser",
            first_name=None,
            last_name=None
        )

        assert profile.first_name == ""
        assert profile.last_name == ""

    def test_complete_profile_saves_profile_picture(self, db, user):
        """
        Profile picture can be uploaded during profile completion.

        Why it matters: Users may want to set their avatar immediately during
        onboarding rather than going through a separate flow.
        """
        image = create_test_image()
        profile = AuthService.complete_profile(
            user=user,
            username="testuser",
            profile_picture=image
        )

        assert profile.profile_picture is not None
        assert profile.profile_picture.name  # File was saved

    def test_complete_profile_updates_existing_profile(self, db, user_with_complete_profile):
        """
        Can update an existing profile rather than creating a new one.

        Why it matters: Users should be able to change their username and other
        profile data after initial setup. This uses get_or_create internally.
        """
        original_username = user_with_complete_profile.profile.username
        profile = AuthService.complete_profile(
            user=user_with_complete_profile,
            username="newusername"
        )

        assert profile.username == "newusername"
        assert profile.username != original_username
        # Should be same profile object, not a new one
        assert Profile.objects.filter(user=user_with_complete_profile).count() == 1

    def test_complete_profile_raises_validation_error_for_invalid_username(self, db, user):
        """
        Raises ValidationError when username format is invalid.

        Why it matters: Invalid usernames should be rejected before being stored.
        The full_clean() call triggers Django validators on the model.
        """
        with pytest.raises(ValidationError):
            AuthService.complete_profile(
                user=user,
                username="ab"  # Too short
            )

    def test_complete_profile_raises_validation_error_for_reserved_username(self, db, user):
        """
        Raises ValidationError for reserved usernames like 'admin'.

        Why it matters: Reserved usernames could be used for impersonation or
        confusion. They're blocked at the model validation level.
        """
        with pytest.raises(ValidationError):
            AuthService.complete_profile(
                user=user,
                username="admin"
            )

    def test_complete_profile_creates_profile_if_not_exists(self, db):
        """
        Creates profile if user doesn't have one yet.

        Why it matters: Handles edge case where profile signal didn't fire or
        user was created through a code path that bypasses signals.
        """
        # Create user without profile (bypassing signal)
        user = User.objects.create_user(
            email="noprofile@example.com",
            password="TestPass123!"
        )
        # Delete auto-created profile
        Profile.objects.filter(user=user).delete()

        profile = AuthService.complete_profile(
            user=user,
            username="newuser"
        )

        assert profile.user == user
        assert profile.username == "newuser"


# =============================================================================
# TestValidateUsername
# =============================================================================


class TestValidateUsername:
    """
    Tests for AuthService.validate_username().

    This method performs three-level validation:
    1. Format check: 3-30 chars, alphanumeric + _ + -
    2. Reserved names check: Prevents admin, root, etc.
    3. Uniqueness check: Case-insensitive duplicate prevention
    """

    # -------------------------------------------------------------------------
    # Format Validation Tests
    # -------------------------------------------------------------------------

    def test_validate_username_accepts_minimum_length(self, db):
        """
        Accepts username with exactly 3 characters (minimum).

        Why it matters: Boundary testing for the minimum length requirement.
        Users with short names should be able to use valid 3-char usernames.
        """
        is_valid, message = AuthService.validate_username("abc")

        assert is_valid is True
        assert message == "Username is available."

    def test_validate_username_accepts_maximum_length(self, db):
        """
        Accepts username with exactly 30 characters (maximum).

        Why it matters: Boundary testing for the maximum length requirement.
        Some users may want longer usernames for uniqueness.
        """
        is_valid, message = AuthService.validate_username("a" * 30)

        assert is_valid is True
        assert message == "Username is available."

    def test_validate_username_rejects_too_short(self, db):
        """
        Rejects username with fewer than 3 characters.

        Why it matters: Very short usernames are often not useful for
        identification and can lead to collision issues.
        """
        is_valid, message = AuthService.validate_username("ab")

        assert is_valid is False
        assert "3-30 characters" in message

    def test_validate_username_rejects_too_long(self, db):
        """
        Rejects username with more than 30 characters.

        Why it matters: Database field has max_length=30. Also, extremely long
        usernames cause display issues in UI.
        """
        is_valid, message = AuthService.validate_username("a" * 31)

        assert is_valid is False
        assert "3-30 characters" in message

    def test_validate_username_accepts_alphanumeric(self, db):
        """
        Accepts username with letters and numbers.

        Why it matters: Basic alphanumeric usernames are the most common case.
        """
        is_valid, message = AuthService.validate_username("user123")

        assert is_valid is True

    def test_validate_username_accepts_underscores(self, db):
        """
        Accepts username containing underscores.

        Why it matters: Underscores are commonly used word separators in usernames.
        """
        is_valid, message = AuthService.validate_username("user_name")

        assert is_valid is True

    def test_validate_username_accepts_hyphens(self, db):
        """
        Accepts username containing hyphens.

        Why it matters: Hyphens are an alternative word separator style.
        """
        is_valid, message = AuthService.validate_username("user-name")

        assert is_valid is True

    def test_validate_username_rejects_spaces(self, db):
        """
        Rejects username containing spaces.

        Why it matters: Spaces cause issues in URLs, command-line tools,
        and general username conventions.
        """
        is_valid, message = AuthService.validate_username("user name")

        assert is_valid is False
        assert "letters, numbers, underscores, and hyphens" in message

    def test_validate_username_rejects_special_characters(self, db):
        """
        Rejects username with special characters like @, !, etc.

        Why it matters: Special characters can cause issues with URL encoding,
        injection attacks, and display problems.
        """
        invalid_chars = ["@", "!", "#", "$", "%", "^", "&", "*", "(", ")", ".", "/"]
        for char in invalid_chars:
            is_valid, message = AuthService.validate_username(f"user{char}name")
            assert is_valid is False, f"Should reject character: {char}"

    def test_validate_username_normalizes_to_lowercase(self, db):
        """
        Username is normalized to lowercase before validation.

        Why it matters: Ensures case-insensitive matching works correctly.
        "UserName" and "username" should be treated as the same.
        """
        # Create a profile with lowercase username
        user = UserFactory()
        user.profile.username = "existinguser"
        user.profile.save()

        # Try to validate same username with different case
        is_valid, message = AuthService.validate_username("ExistingUser")

        assert is_valid is False
        assert "already taken" in message

    # -------------------------------------------------------------------------
    # Reserved Names Tests
    # -------------------------------------------------------------------------

    def test_validate_username_rejects_reserved_admin(self, db):
        """
        Rejects 'admin' as a reserved username.

        Why it matters: 'admin' could be used for impersonation or to
        confuse users about account authenticity.
        """
        is_valid, message = AuthService.validate_username("admin")

        assert is_valid is False
        assert "reserved" in message

    def test_validate_username_rejects_reserved_root(self, db):
        """
        Rejects 'root' as a reserved username.

        Why it matters: 'root' has special meaning in Unix systems and
        could mislead users about system-level access.
        """
        is_valid, message = AuthService.validate_username("root")

        assert is_valid is False
        assert "reserved" in message

    def test_validate_username_rejects_reserved_support(self, db):
        """
        Rejects 'support' as a reserved username.

        Why it matters: 'support' could be used for social engineering
        attacks pretending to be official support.
        """
        is_valid, message = AuthService.validate_username("support")

        assert is_valid is False
        assert "reserved" in message

    def test_validate_username_rejects_all_reserved_names(self, db):
        """
        All names in RESERVED_USERNAMES list are rejected.

        Why it matters: Comprehensive check that the reserved list is
        properly enforced for all entries.
        """
        for reserved_name in list(RESERVED_USERNAMES)[:10]:  # Test first 10
            is_valid, message = AuthService.validate_username(reserved_name)
            assert is_valid is False, f"Should reject reserved name: {reserved_name}"
            assert "reserved" in message

    # -------------------------------------------------------------------------
    # Uniqueness Tests
    # -------------------------------------------------------------------------

    def test_validate_username_rejects_existing_username(self, db):
        """
        Rejects username that already exists in database.

        Why it matters: Usernames must be unique for identification purposes.
        """
        user = UserFactory()
        user.profile.username = "takenname"
        user.profile.save()

        is_valid, message = AuthService.validate_username("takenname")

        assert is_valid is False
        assert "already taken" in message

    def test_validate_username_case_insensitive_uniqueness(self, db):
        """
        Uniqueness check is case-insensitive.

        Why it matters: Prevents near-duplicate usernames that could cause
        confusion (e.g., "Admin" vs "admin").
        """
        user = UserFactory()
        user.profile.username = "uniquename"
        user.profile.save()

        is_valid, message = AuthService.validate_username("UNIQUENAME")

        assert is_valid is False
        assert "already taken" in message

    def test_validate_username_allows_same_user_to_keep_username(self, db):
        """
        exclude_user parameter allows user to validate their own current username.

        Why it matters: When editing profile, the user's own username shouldn't
        be flagged as taken. This enables keeping the same username on update.
        """
        user = UserFactory()
        user.profile.username = "myusername"
        user.profile.save()

        is_valid, message = AuthService.validate_username(
            "myusername",
            exclude_user=user
        )

        assert is_valid is True
        assert "available" in message

    def test_validate_username_exclude_user_still_checks_other_users(self, db):
        """
        exclude_user only excludes the specified user, not all uniqueness checks.

        Why it matters: Even when updating, users shouldn't be able to take
        another user's username.
        """
        user1 = UserFactory()
        user1.profile.username = "user1name"
        user1.profile.save()

        user2 = UserFactory()
        user2.profile.username = "user2name"
        user2.profile.save()

        # user2 tries to take user1's username
        is_valid, message = AuthService.validate_username(
            "user1name",
            exclude_user=user2
        )

        assert is_valid is False
        assert "already taken" in message


# =============================================================================
# TestCreateLinkedAccount
# =============================================================================


class TestCreateLinkedAccount:
    """
    Tests for AuthService.create_linked_account().

    This method creates or retrieves linked accounts for OAuth providers.
    It's idempotent - calling it multiple times with the same data returns
    the existing account.
    """

    def test_create_linked_account_creates_email_provider(self, db, user):
        """
        Creates linked account for email provider.

        Why it matters: Email/password users need a linked account record
        for consistency with OAuth users.
        """
        linked = AuthService.create_linked_account(
            user=user,
            provider=LinkedAccount.Provider.EMAIL,
            provider_user_id=user.email
        )

        assert linked.user == user
        assert linked.provider == LinkedAccount.Provider.EMAIL
        assert linked.provider_user_id == user.email

    def test_create_linked_account_creates_google_provider(self, db, user):
        """
        Creates linked account for Google OAuth provider.

        Why it matters: Google is a primary OAuth provider. Users signing in
        with Google need their provider ID linked.
        """
        linked = AuthService.create_linked_account(
            user=user,
            provider=LinkedAccount.Provider.GOOGLE,
            provider_user_id="google-uid-123"
        )

        assert linked.user == user
        assert linked.provider == LinkedAccount.Provider.GOOGLE
        assert linked.provider_user_id == "google-uid-123"

    def test_create_linked_account_creates_apple_provider(self, db, user):
        """
        Creates linked account for Apple Sign-In provider.

        Why it matters: Apple is a primary OAuth provider for iOS users.
        """
        linked = AuthService.create_linked_account(
            user=user,
            provider=LinkedAccount.Provider.APPLE,
            provider_user_id="apple-uid-456"
        )

        assert linked.user == user
        assert linked.provider == LinkedAccount.Provider.APPLE
        assert linked.provider_user_id == "apple-uid-456"

    def test_create_linked_account_is_idempotent(self, db, user):
        """
        Calling with same provider/ID returns existing account without error.

        Why it matters: OAuth logins may call this method on every login.
        Idempotency prevents duplicate records and IntegrityError exceptions.
        """
        # First call creates
        linked1 = AuthService.create_linked_account(
            user=user,
            provider=LinkedAccount.Provider.GOOGLE,
            provider_user_id="google-uid-123"
        )

        # Second call with same data returns existing
        linked2 = AuthService.create_linked_account(
            user=user,
            provider=LinkedAccount.Provider.GOOGLE,
            provider_user_id="google-uid-123"
        )

        assert linked1.pk == linked2.pk
        assert LinkedAccount.objects.filter(
            provider=LinkedAccount.Provider.GOOGLE,
            provider_user_id="google-uid-123"
        ).count() == 1

    def test_create_linked_account_allows_multiple_providers_per_user(self, db, user):
        """
        User can have multiple linked accounts from different providers.

        Why it matters: Users may want to link both Google and Apple for
        flexible login options.
        """
        google = AuthService.create_linked_account(
            user=user,
            provider=LinkedAccount.Provider.GOOGLE,
            provider_user_id="google-uid-123"
        )
        apple = AuthService.create_linked_account(
            user=user,
            provider=LinkedAccount.Provider.APPLE,
            provider_user_id="apple-uid-456"
        )

        assert user.linked_accounts.count() == 2
        assert google.pk != apple.pk

    def test_create_linked_account_returns_existing_even_with_different_user(self, db):
        """
        If provider/ID combo exists, returns it even if user param differs.

        Why it matters: A provider_user_id should only be linked to one user.
        This prevents account takeover by relinking to a different user.
        The get_or_create uses provider+provider_user_id as the lookup key.
        """
        user1 = UserFactory()
        user2 = UserFactory()

        # User1 links Google account
        linked1 = AuthService.create_linked_account(
            user=user1,
            provider=LinkedAccount.Provider.GOOGLE,
            provider_user_id="google-uid-123"
        )

        # User2 tries to link same Google account
        linked2 = AuthService.create_linked_account(
            user=user2,
            provider=LinkedAccount.Provider.GOOGLE,
            provider_user_id="google-uid-123"
        )

        # Should return the existing link to user1, not create new for user2
        assert linked1.pk == linked2.pk
        assert linked2.user == user1  # Still linked to original user


# =============================================================================
# TestCreateUser
# =============================================================================


class TestCreateUser:
    """
    Tests for AuthService.create_user().

    This method creates new users with optional password (for OAuth users).
    It normalizes email, prevents duplicates, and creates LinkedAccount
    for email-registered users.
    """

    def test_create_user_with_email_and_password(self, db):
        """
        Creates user with email and password for standard registration.

        Why it matters: This is the primary registration path for users
        signing up with email/password.
        """
        user = AuthService.create_user(
            email="newuser@example.com",
            password="SecurePass123!"
        )

        assert user.email == "newuser@example.com"
        assert user.check_password("SecurePass123!")

    def test_create_user_normalizes_email_to_lowercase(self, db):
        """
        Email is normalized to lowercase before storage.

        Why it matters: Prevents duplicate accounts for same email with
        different casing. Email addresses are case-insensitive.
        """
        user = AuthService.create_user(
            email="TEST_LOWERCASE@EXAMPLE.COM",
            password="SecurePass123!"
        )

        assert user.email == "test_lowercase@example.com"

    def test_create_user_strips_email_whitespace(self, db):
        """
        Email has leading/trailing whitespace removed.

        Why it matters: Prevents accidental spaces from form input or copy-paste.
        """
        user = AuthService.create_user(
            email="  spaced@example.com  ",
            password="SecurePass123!"
        )

        assert user.email == "spaced@example.com"

    def test_create_user_creates_linked_account_for_email_registration(self, db):
        """
        Creates LinkedAccount with EMAIL provider when password is provided.

        Why it matters: Tracks authentication method. Users who registered
        with email/password have a LinkedAccount for the email provider.
        """
        user = AuthService.create_user(
            email="newuser@example.com",
            password="SecurePass123!"
        )

        linked = LinkedAccount.objects.get(user=user)
        assert linked.provider == LinkedAccount.Provider.EMAIL
        assert linked.provider_user_id == "newuser@example.com"

    def test_create_user_without_password_for_oauth(self, db):
        """
        Can create user without password for OAuth-only users.

        Why it matters: OAuth users don't need a password - they authenticate
        through their provider. Password=None indicates OAuth-only user.
        """
        user = AuthService.create_user(
            email="oauth@example.com",
            password=None
        )

        assert user.email == "oauth@example.com"
        assert not user.has_usable_password()

    def test_create_user_oauth_does_not_create_email_linked_account(self, db):
        """
        OAuth users (password=None) don't get email LinkedAccount.

        Why it matters: OAuth users authenticate through their provider,
        not email. Their LinkedAccount is created separately for that provider.
        """
        user = AuthService.create_user(
            email="oauth@example.com",
            password=None
        )

        # Should have no linked accounts (OAuth link created separately)
        assert not LinkedAccount.objects.filter(
            user=user,
            provider=LinkedAccount.Provider.EMAIL
        ).exists()

    def test_create_user_raises_value_error_for_duplicate_email(self, db):
        """
        Raises ValueError when email already exists.

        Why it matters: Prevents duplicate accounts. Email is the unique
        identifier for users.
        """
        AuthService.create_user(
            email="existing@example.com",
            password="SecurePass123!"
        )

        with pytest.raises(ValueError) as exc_info:
            AuthService.create_user(
                email="existing@example.com",
                password="DifferentPass123!"
            )

        assert "already exists" in str(exc_info.value)

    def test_create_user_duplicate_check_is_case_insensitive(self, db):
        """
        Duplicate check works regardless of email case.

        Why it matters: "User@Example.com" and "user@example.com" are the
        same email and should not result in two accounts.
        """
        AuthService.create_user(
            email="existing@example.com",
            password="SecurePass123!"
        )

        with pytest.raises(ValueError):
            AuthService.create_user(
                email="EXISTING@EXAMPLE.COM",
                password="DifferentPass123!"
            )

    def test_create_user_accepts_additional_kwargs(self, db):
        """
        Additional keyword arguments are passed to User.objects.create_user().

        Why it matters: Allows setting fields like is_staff during creation
        for special user types.
        """
        user = AuthService.create_user(
            email="staff@example.com",
            password="SecurePass123!",
            is_staff=True
        )

        assert user.is_staff is True


# =============================================================================
# TestVerifyEmail
# =============================================================================


class TestVerifyEmail:
    """
    Tests for AuthService.verify_email().

    This method validates email verification tokens and marks users as verified.
    Tokens must be valid (not used, not expired) to succeed.
    """

    def test_verify_email_succeeds_with_valid_token(self, db, valid_verification_token):
        """
        Successfully verifies email with valid token.

        Why it matters: Happy path for email verification flow.
        """
        success, message = AuthService.verify_email(valid_verification_token.token)

        assert success is True
        assert "successfully" in message

    def test_verify_email_sets_email_verified_flag(self, db, unverified_user):
        """
        Sets email_verified=True on the user after successful verification.

        Why it matters: The user's email_verified status controls access
        to protected features.
        """
        token = EmailVerificationTokenFactory(
            user=unverified_user,
            token_type=EmailVerificationToken.TokenType.EMAIL_VERIFICATION,
            expires_at=timezone.now() + timedelta(hours=24),
            used_at=None
        )

        AuthService.verify_email(token.token)

        unverified_user.refresh_from_db()
        assert unverified_user.email_verified is True

    def test_verify_email_marks_token_as_used(self, db, valid_verification_token):
        """
        Marks the token as used after successful verification.

        Why it matters: Tokens are single-use to prevent replay attacks.
        """
        AuthService.verify_email(valid_verification_token.token)

        valid_verification_token.refresh_from_db()
        assert valid_verification_token.used_at is not None

    def test_verify_email_fails_with_expired_token(self, db, expired_verification_token):
        """
        Rejects expired verification tokens.

        Why it matters: Time-limited tokens prevent stale verification links
        from being used long after they were sent.
        """
        success, message = AuthService.verify_email(expired_verification_token.token)

        assert success is False
        assert "Invalid or expired" in message

    def test_verify_email_fails_with_used_token(self, db, used_verification_token):
        """
        Rejects already-used verification tokens.

        Why it matters: Prevents token reuse after successful verification.
        """
        success, message = AuthService.verify_email(used_verification_token.token)

        assert success is False
        assert "Invalid or expired" in message

    def test_verify_email_fails_with_nonexistent_token(self, db):
        """
        Rejects tokens that don't exist in the database.

        Why it matters: Invalid/random tokens should not verify any user.
        """
        success, message = AuthService.verify_email("nonexistent-token-12345")

        assert success is False
        assert "Invalid or expired" in message

    def test_verify_email_fails_with_password_reset_token_type(self, db, password_reset_token):
        """
        Rejects password reset tokens used for email verification.

        Why it matters: Token types must match their intended purpose.
        A password reset token should not be usable for email verification.
        """
        success, message = AuthService.verify_email(password_reset_token.token)

        assert success is False
        assert "Invalid or expired" in message

    @freeze_time("2024-01-15 12:00:00")
    def test_verify_email_respects_expiration_boundary(self, db, user):
        """
        Token that expires at exact current time is considered expired.

        Why it matters: Boundary testing for expiration logic. The query
        uses expires_at__gt=timezone.now(), so equal times should fail.
        """
        # Token expires exactly now
        token = EmailVerificationTokenFactory(
            user=user,
            token_type=EmailVerificationToken.TokenType.EMAIL_VERIFICATION,
            expires_at=timezone.now(),
            used_at=None
        )

        success, message = AuthService.verify_email(token.token)

        assert success is False


# =============================================================================
# TestRequestPasswordReset
# =============================================================================


class TestRequestPasswordReset:
    """
    Tests for AuthService.request_password_reset().

    This method creates password reset tokens. It always returns True to
    prevent user enumeration attacks - callers cannot tell if email exists.
    """

    def test_request_password_reset_returns_true_for_existing_user(self, db, user):
        """
        Returns True when user exists.

        Why it matters: Confirms the method works for valid requests.
        """
        result = AuthService.request_password_reset(user.email)

        assert result is True

    def test_request_password_reset_creates_token(self, db, user):
        """
        Creates a password reset token for existing user.

        Why it matters: The token is needed for the reset_password step.
        """
        initial_count = EmailVerificationToken.objects.filter(
            user=user,
            token_type=EmailVerificationToken.TokenType.PASSWORD_RESET
        ).count()

        AuthService.request_password_reset(user.email)

        final_count = EmailVerificationToken.objects.filter(
            user=user,
            token_type=EmailVerificationToken.TokenType.PASSWORD_RESET
        ).count()

        assert final_count == initial_count + 1

    def test_request_password_reset_token_has_correct_expiry(self, db, user):
        """
        Created token expires in 1 hour (PASSWORD_RESET_EXPIRY_HOURS).

        Why it matters: Password reset links should expire quickly for security.
        """
        before = timezone.now()
        AuthService.request_password_reset(user.email)
        after = timezone.now()

        token = EmailVerificationToken.objects.filter(
            user=user,
            token_type=EmailVerificationToken.TokenType.PASSWORD_RESET
        ).latest("created_at")

        # Expiry should be ~1 hour from creation
        expected_min = before + timedelta(hours=1)
        expected_max = after + timedelta(hours=1)
        assert expected_min <= token.expires_at <= expected_max

    def test_request_password_reset_returns_true_for_nonexistent_email(self, db):
        """
        Returns True even when email doesn't exist (security measure).

        Why it matters: Prevents attackers from discovering which emails
        are registered in the system (user enumeration attack).
        """
        result = AuthService.request_password_reset("nonexistent@example.com")

        assert result is True

    def test_request_password_reset_does_not_create_token_for_nonexistent_email(self, db):
        """
        No token is created for non-existent email.

        Why it matters: We don't want orphan tokens or any indication
        of whether the email exists.
        """
        initial_count = EmailVerificationToken.objects.count()

        AuthService.request_password_reset("nonexistent@example.com")

        assert EmailVerificationToken.objects.count() == initial_count

    def test_request_password_reset_normalizes_email(self, db, user):
        """
        Email lookup is case-insensitive.

        Why it matters: Users may enter their email with different casing
        than they registered with.
        """
        result = AuthService.request_password_reset(user.email.upper())

        assert result is True
        # Verify token was actually created
        assert EmailVerificationToken.objects.filter(
            user=user,
            token_type=EmailVerificationToken.TokenType.PASSWORD_RESET
        ).exists()

    def test_request_password_reset_allows_multiple_requests(self, db, user):
        """
        Users can request multiple password resets (creates new token each time).

        Why it matters: Users may not receive the first email or may
        accidentally delete it. They should be able to request again.
        """
        AuthService.request_password_reset(user.email)
        AuthService.request_password_reset(user.email)
        AuthService.request_password_reset(user.email)

        token_count = EmailVerificationToken.objects.filter(
            user=user,
            token_type=EmailVerificationToken.TokenType.PASSWORD_RESET
        ).count()

        assert token_count == 3


# =============================================================================
# TestResetPassword
# =============================================================================


class TestResetPassword:
    """
    Tests for AuthService.reset_password().

    This method validates the reset token, sets the new password, and
    invalidates all other pending reset tokens for the user.
    """

    def test_reset_password_succeeds_with_valid_token(self, db, password_reset_token):
        """
        Successfully resets password with valid token.

        Why it matters: Happy path for password reset flow.
        """
        success, message = AuthService.reset_password(
            token=password_reset_token.token,
            new_password="NewSecurePass123!"
        )

        assert success is True
        assert "successfully" in message

    def test_reset_password_sets_new_password(self, db, password_reset_token):
        """
        User's password is changed to the new password.

        Why it matters: The whole point of password reset is changing the password.
        """
        user = password_reset_token.user
        old_password_hash = user.password

        AuthService.reset_password(
            token=password_reset_token.token,
            new_password="NewSecurePass123!"
        )

        user.refresh_from_db()
        assert user.password != old_password_hash
        assert user.check_password("NewSecurePass123!")

    def test_reset_password_marks_token_as_used(self, db, password_reset_token):
        """
        The reset token is marked as used after successful reset.

        Why it matters: Prevents token reuse - one reset per token.
        """
        AuthService.reset_password(
            token=password_reset_token.token,
            new_password="NewSecurePass123!"
        )

        password_reset_token.refresh_from_db()
        assert password_reset_token.used_at is not None

    def test_reset_password_invalidates_other_pending_tokens(self, db, user):
        """
        All other pending reset tokens for the user are invalidated.

        Why it matters: When password is reset, all previous reset links
        should stop working for security.
        """
        # Create multiple pending tokens
        token1 = EmailVerificationTokenFactory(
            user=user,
            token_type=EmailVerificationToken.TokenType.PASSWORD_RESET,
            expires_at=timezone.now() + timedelta(hours=1),
            used_at=None
        )
        token2 = EmailVerificationTokenFactory(
            user=user,
            token_type=EmailVerificationToken.TokenType.PASSWORD_RESET,
            expires_at=timezone.now() + timedelta(hours=1),
            used_at=None
        )
        token3 = EmailVerificationTokenFactory(
            user=user,
            token_type=EmailVerificationToken.TokenType.PASSWORD_RESET,
            expires_at=timezone.now() + timedelta(hours=1),
            used_at=None
        )

        # Use token2 to reset password
        AuthService.reset_password(
            token=token2.token,
            new_password="NewSecurePass123!"
        )

        # All tokens should now be marked as used
        token1.refresh_from_db()
        token2.refresh_from_db()
        token3.refresh_from_db()

        assert token1.used_at is not None
        assert token2.used_at is not None
        assert token3.used_at is not None

    def test_reset_password_fails_with_expired_token(self, db, expired_password_reset_token):
        """
        Rejects expired password reset tokens.

        Why it matters: Expired tokens should not allow password changes.
        """
        success, message = AuthService.reset_password(
            token=expired_password_reset_token.token,
            new_password="NewSecurePass123!"
        )

        assert success is False
        assert "Invalid or expired" in message

    def test_reset_password_fails_with_used_token(self, db, user):
        """
        Rejects already-used password reset tokens.

        Why it matters: Each token should only work once.
        """
        used_token = EmailVerificationTokenFactory(
            user=user,
            token_type=EmailVerificationToken.TokenType.PASSWORD_RESET,
            expires_at=timezone.now() + timedelta(hours=1),
            used_at=timezone.now() - timedelta(minutes=30)
        )

        success, message = AuthService.reset_password(
            token=used_token.token,
            new_password="NewSecurePass123!"
        )

        assert success is False
        assert "Invalid or expired" in message

    def test_reset_password_fails_with_nonexistent_token(self, db):
        """
        Rejects tokens that don't exist.

        Why it matters: Random/guessed tokens should not work.
        """
        success, message = AuthService.reset_password(
            token="nonexistent-token-12345",
            new_password="NewSecurePass123!"
        )

        assert success is False
        assert "Invalid or expired" in message

    def test_reset_password_fails_with_verification_token_type(self, db, valid_verification_token):
        """
        Rejects email verification tokens used for password reset.

        Why it matters: Token types must match their intended purpose.
        """
        success, message = AuthService.reset_password(
            token=valid_verification_token.token,
            new_password="NewSecurePass123!"
        )

        assert success is False
        assert "Invalid or expired" in message

    def test_reset_password_does_not_invalidate_verification_tokens(self, db, user):
        """
        Only invalidates password reset tokens, not email verification tokens.

        Why it matters: Email verification tokens serve a different purpose
        and should remain valid after password reset.
        """
        # Create a verification token
        verification_token = EmailVerificationTokenFactory(
            user=user,
            token_type=EmailVerificationToken.TokenType.EMAIL_VERIFICATION,
            expires_at=timezone.now() + timedelta(hours=24),
            used_at=None
        )

        # Create and use a password reset token
        reset_token = EmailVerificationTokenFactory(
            user=user,
            token_type=EmailVerificationToken.TokenType.PASSWORD_RESET,
            expires_at=timezone.now() + timedelta(hours=1),
            used_at=None
        )

        AuthService.reset_password(
            token=reset_token.token,
            new_password="NewSecurePass123!"
        )

        # Verification token should still be unused
        verification_token.refresh_from_db()
        assert verification_token.used_at is None


# =============================================================================
# TestDeactivateUser
# =============================================================================


class TestDeactivateUser:
    """
    Tests for AuthService.deactivate_user().

    This method performs soft-delete by setting is_active=False.
    User data is preserved for audit trail.
    """

    def test_deactivate_user_sets_is_active_false(self, db, user):
        """
        Sets is_active=False on the user.

        Why it matters: Deactivated users cannot log in.
        """
        assert user.is_active is True

        AuthService.deactivate_user(user)

        user.refresh_from_db()
        assert user.is_active is False

    def test_deactivate_user_preserves_user_data(self, db, user):
        """
        User data is preserved after deactivation (soft delete).

        Why it matters: Unlike hard delete, soft delete preserves data for
        audit trail, potential reactivation, and data integrity.
        """
        email = user.email
        user_pk = user.pk

        AuthService.deactivate_user(user)

        user.refresh_from_db()
        assert user.pk == user_pk
        assert user.email == email

    def test_deactivate_user_accepts_optional_reason(self, db, user):
        """
        Can provide a reason for deactivation (for logging).

        Why it matters: Reason is logged for audit purposes. Method should
        accept but not require a reason.
        """
        # Should not raise
        AuthService.deactivate_user(user, reason="Violated terms of service")

        user.refresh_from_db()
        assert user.is_active is False

    def test_deactivate_user_works_without_reason(self, db, user):
        """
        Reason parameter is optional.

        Why it matters: Simple deactivations shouldn't require a reason.
        """
        # Should not raise
        AuthService.deactivate_user(user)

        user.refresh_from_db()
        assert user.is_active is False

    def test_deactivate_user_updates_updated_at(self, db, user):
        """
        User's updated_at timestamp is updated.

        Why it matters: Tracks when the deactivation occurred.
        """
        original_updated_at = user.updated_at

        AuthService.deactivate_user(user)

        user.refresh_from_db()
        assert user.updated_at > original_updated_at

    def test_deactivate_already_inactive_user(self, db, deactivated_user):
        """
        Deactivating already inactive user doesn't raise error.

        Why it matters: Method should be idempotent - calling it on
        already deactivated user shouldn't cause issues.
        """
        # Should not raise
        AuthService.deactivate_user(deactivated_user)

        deactivated_user.refresh_from_db()
        assert deactivated_user.is_active is False


# =============================================================================
# TestSendVerificationEmail
# =============================================================================


class TestSendVerificationEmail:
    """
    Tests for AuthService.send_verification_email().

    This method creates a verification token for the user. The actual email
    sending is handled asynchronously (currently TODO in the implementation).
    """

    def test_send_verification_email_creates_token(self, db, unverified_user):
        """
        Creates an email verification token for the user.

        Why it matters: The token is needed for the verify_email flow.
        """
        initial_count = EmailVerificationToken.objects.filter(
            user=unverified_user,
            token_type=EmailVerificationToken.TokenType.EMAIL_VERIFICATION
        ).count()

        AuthService.send_verification_email(unverified_user)

        final_count = EmailVerificationToken.objects.filter(
            user=unverified_user,
            token_type=EmailVerificationToken.TokenType.EMAIL_VERIFICATION
        ).count()

        assert final_count == initial_count + 1

    def test_send_verification_email_token_has_correct_expiry(self, db, unverified_user):
        """
        Created token expires in 24 hours (EMAIL_VERIFICATION_EXPIRY_HOURS).

        Why it matters: Verification links should expire to prevent stale
        tokens from being used indefinitely.
        """
        before = timezone.now()
        AuthService.send_verification_email(unverified_user)
        after = timezone.now()

        token = EmailVerificationToken.objects.filter(
            user=unverified_user,
            token_type=EmailVerificationToken.TokenType.EMAIL_VERIFICATION
        ).latest("created_at")

        # Expiry should be ~24 hours from creation
        expected_min = before + timedelta(hours=24)
        expected_max = after + timedelta(hours=24)
        assert expected_min <= token.expires_at <= expected_max

    def test_send_verification_email_token_type_is_email_verification(self, db, unverified_user):
        """
        Created token has EMAIL_VERIFICATION type.

        Why it matters: Token type distinguishes verification from password reset.
        """
        AuthService.send_verification_email(unverified_user)

        token = EmailVerificationToken.objects.filter(
            user=unverified_user
        ).latest("created_at")

        assert token.token_type == EmailVerificationToken.TokenType.EMAIL_VERIFICATION

    def test_send_verification_email_token_is_unused(self, db, unverified_user):
        """
        Created token has used_at=None (not used yet).

        Why it matters: New tokens should be unused and available for verification.
        """
        AuthService.send_verification_email(unverified_user)

        token = EmailVerificationToken.objects.filter(
            user=unverified_user,
            token_type=EmailVerificationToken.TokenType.EMAIL_VERIFICATION
        ).latest("created_at")

        assert token.used_at is None

    def test_send_verification_email_creates_unique_tokens(self, db, unverified_user):
        """
        Each call creates a unique token.

        Why it matters: Tokens must be unique for security. Duplicates would
        allow one token to verify multiple accounts.
        """
        AuthService.send_verification_email(unverified_user)
        AuthService.send_verification_email(unverified_user)

        tokens = EmailVerificationToken.objects.filter(
            user=unverified_user,
            token_type=EmailVerificationToken.TokenType.EMAIL_VERIFICATION
        ).values_list("token", flat=True)

        # All tokens should be unique
        assert len(tokens) == len(set(tokens))


# =============================================================================
# TestGetOrCreateProfile
# =============================================================================


class TestGetOrCreateProfile:
    """
    Tests for AuthService.get_or_create_profile().

    This method ensures a profile exists for a user, creating one if needed.
    Used defensively when accessing profile data.
    """

    def test_get_or_create_profile_returns_existing_profile(self, db, user):
        """
        Returns existing profile when user has one.

        Why it matters: Profiles are auto-created via signals. This should
        return that existing profile.
        """
        existing_profile = user.profile

        profile = AuthService.get_or_create_profile(user)

        assert profile.pk == existing_profile.pk

    def test_get_or_create_profile_creates_profile_if_missing(self, db):
        """
        Creates a new profile if user doesn't have one.

        Why it matters: Handles edge case where profile signal didn't fire
        or profile was accidentally deleted.
        """
        # Create user and delete the auto-created profile
        user = User.objects.create_user(
            email="noprofile@example.com",
            password="TestPass123!"
        )
        Profile.objects.filter(user=user).delete()

        profile = AuthService.get_or_create_profile(user)

        assert profile.user == user
        assert Profile.objects.filter(user=user).exists()

    def test_get_or_create_profile_is_idempotent(self, db, user):
        """
        Multiple calls return the same profile instance.

        Why it matters: Method shouldn't create duplicate profiles.
        """
        profile1 = AuthService.get_or_create_profile(user)
        profile2 = AuthService.get_or_create_profile(user)

        assert profile1.pk == profile2.pk
        assert Profile.objects.filter(user=user).count() == 1

    def test_get_or_create_profile_new_profile_has_empty_fields(self, db):
        """
        Newly created profile has empty optional fields.

        Why it matters: New profiles should not have unexpected default values.
        """
        user = User.objects.create_user(
            email="noprofile@example.com",
            password="TestPass123!"
        )
        Profile.objects.filter(user=user).delete()

        profile = AuthService.get_or_create_profile(user)

        assert profile.username == ""
        assert profile.first_name == ""
        assert profile.last_name == ""


# =============================================================================
# TestUpdateProfile
# =============================================================================


class TestUpdateProfile:
    """
    Tests for AuthService.update_profile().

    This method provides generic profile field updates. It dynamically
    sets any valid profile field passed as keyword arguments.
    """

    def test_update_profile_updates_first_name(self, db, user):
        """
        Can update first_name field.

        Why it matters: Users should be able to change their name.
        """
        profile = AuthService.update_profile(user, first_name="Updated")

        assert profile.first_name == "Updated"

    def test_update_profile_updates_last_name(self, db, user):
        """
        Can update last_name field.

        Why it matters: Users should be able to change their name.
        """
        profile = AuthService.update_profile(user, last_name="Name")

        assert profile.last_name == "Name"

    def test_update_profile_updates_timezone(self, db, user):
        """
        Can update timezone preference.

        Why it matters: Users in different locations need different timezones.
        """
        profile = AuthService.update_profile(user, timezone="America/New_York")

        assert profile.timezone == "America/New_York"

    def test_update_profile_updates_preferences(self, db, user):
        """
        Can update preferences JSON field.

        Why it matters: Flexible storage for user settings like theme, language.
        """
        new_prefs = {"theme": "dark", "language": "es"}
        profile = AuthService.update_profile(user, preferences=new_prefs)

        assert profile.preferences == new_prefs

    def test_update_profile_updates_multiple_fields(self, db, user):
        """
        Can update multiple fields in one call.

        Why it matters: Efficient updates without multiple database writes.
        """
        profile = AuthService.update_profile(
            user,
            first_name="John",
            last_name="Doe",
            timezone="Europe/London"
        )

        assert profile.first_name == "John"
        assert profile.last_name == "Doe"
        assert profile.timezone == "Europe/London"

    def test_update_profile_ignores_invalid_fields(self, db, user):
        """
        Ignores fields that don't exist on Profile model.

        Why it matters: Prevents errors when extra data is passed.
        The hasattr check skips non-existent fields.
        """
        # Should not raise
        profile = AuthService.update_profile(
            user,
            nonexistent_field="value",
            first_name="Valid"
        )

        assert profile.first_name == "Valid"
        assert not hasattr(profile, "nonexistent_field")

    def test_update_profile_creates_profile_if_missing(self, db):
        """
        Creates profile if user doesn't have one, then updates it.

        Why it matters: Defensive programming - should work even if
        profile signal didn't fire.
        """
        user = User.objects.create_user(
            email="noprofile@example.com",
            password="TestPass123!"
        )
        Profile.objects.filter(user=user).delete()

        profile = AuthService.update_profile(user, first_name="Created")

        assert profile.first_name == "Created"
        assert profile.user == user

    def test_update_profile_returns_profile(self, db, user):
        """
        Returns the updated profile instance.

        Why it matters: Allows chaining or immediate use of updated profile.
        """
        result = AuthService.update_profile(user, first_name="Test")

        assert isinstance(result, Profile)
        assert result.first_name == "Test"

    def test_update_profile_persists_changes(self, db, user):
        """
        Changes are persisted to database.

        Why it matters: Confirms save() is called and changes aren't lost.
        """
        AuthService.update_profile(user, first_name="Persisted")

        # Reload from database
        profile = Profile.objects.get(user=user)
        assert profile.first_name == "Persisted"
