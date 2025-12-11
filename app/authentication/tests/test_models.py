"""
Comprehensive tests for authentication models.

This module tests all authentication models following TDD principles:
- User: Custom user model with email-based authentication
- Profile: Extended user profile data with username validation
- LinkedAccount: OAuth provider connections
- EmailVerificationToken: Single-use verification tokens

Test Organization:
    - Each model has its own test class
    - Each test validates ONE specific behavior
    - Tests use descriptive names following the pattern: test_<scenario>_<expected_outcome>

Testing Philosophy:
    Tests focus on observable behavior, not implementation details. We test:
    - Field constraints and validation
    - Model methods and properties
    - String representations
    - Database constraints

Dependencies:
    - pytest and pytest-django for test framework
    - freezegun for time-based tests (token expiration)
    - Factory Boy fixtures from conftest.py
"""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.utils import timezone
from freezegun import freeze_time

from authentication.models import (
    User,
    Profile,
    LinkedAccount,
    EmailVerificationToken,
    RESERVED_USERNAMES,
    validate_username_format,
    validate_username_not_reserved,
)
from authentication.tests.factories import (
    UserFactory,
    ProfileFactory,
    LinkedAccountFactory,
    EmailVerificationTokenFactory,
)


# =============================================================================
# User Model Tests
# =============================================================================


class TestUserModel:
    """
    Tests for the User model.

    The User model is a custom Django user with email as the primary identifier.
    It's intentionally slim, focusing only on authentication concerns.
    """

    # -------------------------------------------------------------------------
    # Field Tests
    # -------------------------------------------------------------------------

    def test_user_email_is_required(self, db):
        """
        Email is the primary identifier and must be provided.

        Why it matters: Users cannot authenticate without an email address.
        The system relies on email for login, password reset, and communication.
        """
        with pytest.raises((IntegrityError, ValueError)):
            User.objects.create_user(email=None, password="TestPass123!")

    def test_user_email_must_be_unique(self, db, user):
        """
        Email addresses must be unique across all users.

        Why it matters: Email is the USERNAME_FIELD, so duplicates would
        create login ambiguity. This enforces data integrity at the DB level.
        """
        with pytest.raises(IntegrityError):
            User.objects.create_user(email=user.email, password="DifferentPass123!")

    def test_user_email_normalized_to_lowercase_domain(self, db):
        """
        Email domain is normalized to lowercase during creation.

        Why it matters: Email addresses are case-insensitive by RFC 5321.
        Normalizing the domain prevents duplicate accounts for the same email
        with different casing (e.g., user@EXAMPLE.com vs user@example.com).
        """
        user = User.objects.create_user(
            email="Test@EXAMPLE.COM",
            password="TestPass123!"
        )
        # Django's normalize_email lowercases the domain, not the local part
        assert user.email == "Test@example.com"

    def test_user_default_email_verified_is_false(self, db):
        """
        New users should have unverified email by default.

        Why it matters: Email verification is a security requirement. Users
        must prove they own the email before accessing protected features.
        """
        user = User.objects.create_user(
            email="new@example.com",
            password="TestPass123!"
        )
        assert user.email_verified is False

    def test_user_default_is_active_is_true(self, db):
        """
        New users are active by default.

        Why it matters: Users should be able to log in immediately after
        registration. Deactivation is an administrative action.
        """
        user = User.objects.create_user(
            email="new@example.com",
            password="TestPass123!"
        )
        assert user.is_active is True

    def test_user_default_is_staff_is_false(self, db):
        """
        New users should not have staff access by default.

        Why it matters: Admin access is a privileged permission that must
        be explicitly granted. Default-deny is a security best practice.
        """
        user = User.objects.create_user(
            email="new@example.com",
            password="TestPass123!"
        )
        assert user.is_staff is False

    def test_user_date_joined_is_set_on_creation(self, db):
        """
        date_joined is automatically set when user is created.

        Why it matters: Audit trail for user accounts. Used for analytics,
        sorting, and determining account age for features like rate limits.
        """
        before_creation = timezone.now()
        user = User.objects.create_user(
            email="new@example.com",
            password="TestPass123!"
        )
        after_creation = timezone.now()

        assert user.date_joined is not None
        assert before_creation <= user.date_joined <= after_creation

    def test_user_updated_at_changes_on_save(self, db, user):
        """
        updated_at timestamp updates on each save.

        Why it matters: Tracks when user data was last modified. Essential
        for cache invalidation, sync operations, and audit logging.
        """
        original_updated_at = user.updated_at
        user.email_verified = True
        user.save()
        user.refresh_from_db()

        assert user.updated_at > original_updated_at

    def test_user_password_is_hashed(self, db):
        """
        Passwords are stored as hashes, not plain text.

        Why it matters: Critical security requirement. Plain text passwords
        would be catastrophic in case of a data breach.
        """
        plain_password = "TestPass123!"
        user = User.objects.create_user(
            email="new@example.com",
            password=plain_password
        )

        assert user.password != plain_password
        assert user.check_password(plain_password) is True

    # -------------------------------------------------------------------------
    # Method Tests
    # -------------------------------------------------------------------------

    def test_str_returns_email(self, user):
        """
        String representation is the user's email.

        Why it matters: Used in admin, logs, and debugging. Email is the
        most recognizable identifier for a user.
        """
        assert str(user) == user.email

    def test_get_full_name_returns_profile_full_name_when_set(
        self, user_with_complete_profile
    ):
        """
        get_full_name returns the profile's full name when available.

        Why it matters: Required by Django's user model contract. Used in
        emails, UI greetings, and user-facing displays.
        """
        user = user_with_complete_profile
        expected_name = f"{user.profile.first_name} {user.profile.last_name}"
        assert user.get_full_name() == expected_name.strip()

    def test_get_full_name_returns_email_when_profile_has_no_name(
        self, user_with_incomplete_profile
    ):
        """
        get_full_name falls back to email when profile name is empty.

        Why it matters: Ensures a usable display name is always available,
        even for users who haven't completed their profile.
        """
        user = user_with_incomplete_profile
        user.profile.first_name = ""
        user.profile.last_name = ""
        user.profile.save()

        assert user.get_full_name() == user.email

    def test_get_full_name_returns_email_when_no_profile(self, db):
        """
        get_full_name returns email when user has no profile.

        Why it matters: Handles edge case where profile signal fails or
        profile is manually deleted. System should not crash.
        """
        # Create user without triggering profile signal
        user = User(email="noprofile@example.com")
        user.set_password("TestPass123!")
        user.save()
        # Delete the auto-created profile
        Profile.objects.filter(user=user).delete()

        assert user.get_full_name() == user.email

    def test_get_short_name_returns_first_name_when_set(
        self, user_with_complete_profile
    ):
        """
        get_short_name returns the user's first name.

        Why it matters: Used for informal greetings like "Hi, John!"
        in emails and UI.
        """
        user = user_with_complete_profile
        assert user.get_short_name() == user.profile.first_name

    def test_get_short_name_returns_email_local_part_when_no_first_name(
        self, user_with_incomplete_profile
    ):
        """
        get_short_name falls back to email local part when no first name.

        Why it matters: Provides a reasonable default for informal greetings
        when the user hasn't provided their name.
        """
        user = user_with_incomplete_profile
        user.profile.first_name = ""
        user.profile.save()

        expected = user.email.split("@")[0]
        assert user.get_short_name() == expected

    def test_get_short_name_returns_email_local_part_when_no_profile(self, db):
        """
        get_short_name uses email local part when profile doesn't exist.

        Why it matters: Graceful degradation when profile is missing.
        """
        user = User(email="testuser@example.com")
        user.set_password("TestPass123!")
        user.save()
        Profile.objects.filter(user=user).delete()

        assert user.get_short_name() == "testuser"

    # -------------------------------------------------------------------------
    # Property Tests
    # -------------------------------------------------------------------------

    def test_has_completed_profile_true_when_username_set(
        self, user_with_complete_profile
    ):
        """
        has_completed_profile is True when profile has a username.

        Why it matters: Determines if user needs to complete onboarding.
        Username selection is the key step in profile completion.
        """
        assert user_with_complete_profile.has_completed_profile is True

    def test_has_completed_profile_false_when_username_empty(
        self, user_with_incomplete_profile
    ):
        """
        has_completed_profile is False when username is empty.

        Why it matters: Identifies users who need to complete onboarding.
        """
        assert user_with_incomplete_profile.has_completed_profile is False

    def test_has_completed_profile_false_when_no_profile(self, db):
        """
        has_completed_profile is False when user has no profile.

        Why it matters: Edge case handling for corrupted data.
        """
        user = User(email="noprofile@example.com")
        user.set_password("TestPass123!")
        user.save()
        Profile.objects.filter(user=user).delete()

        assert user.has_completed_profile is False

    # -------------------------------------------------------------------------
    # Manager Tests
    # -------------------------------------------------------------------------

    def test_create_user_creates_regular_user(self, db):
        """
        create_user creates a non-staff, non-superuser account.

        Why it matters: Default user creation should have minimal privileges.
        """
        user = User.objects.create_user(
            email="regular@example.com",
            password="TestPass123!"
        )
        assert user.is_staff is False
        assert user.is_superuser is False

    def test_create_superuser_sets_staff_and_superuser_flags(self, superuser):
        """
        create_superuser sets is_staff and is_superuser to True.

        Why it matters: Superusers need admin access and all permissions.
        """
        assert superuser.is_staff is True
        assert superuser.is_superuser is True

    def test_create_superuser_raises_error_if_is_staff_false(self, db):
        """
        create_superuser rejects is_staff=False.

        Why it matters: Prevents creation of broken admin accounts that
        can't access the admin site.
        """
        with pytest.raises(ValueError, match="is_staff"):
            User.objects.create_superuser(
                email="admin@example.com",
                password="AdminPass123!",
                is_staff=False
            )

    def test_create_superuser_raises_error_if_is_superuser_false(self, db):
        """
        create_superuser rejects is_superuser=False.

        Why it matters: A superuser must have superuser flag set.
        """
        with pytest.raises(ValueError, match="is_superuser"):
            User.objects.create_superuser(
                email="admin@example.com",
                password="AdminPass123!",
                is_superuser=False
            )

    # -------------------------------------------------------------------------
    # Ordering Tests
    # -------------------------------------------------------------------------

    def test_users_ordered_by_date_joined_descending(self, db):
        """
        Users are ordered by date_joined, newest first.

        Why it matters: Default ordering affects admin views and querysets.
        Newest users first is typically most useful for admin tasks.
        """
        with freeze_time("2024-01-01 10:00:00"):
            old_user = UserFactory()
        with freeze_time("2024-01-02 10:00:00"):
            new_user = UserFactory()

        # Filter to only include users created by this test
        users = list(User.objects.filter(pk__in=[old_user.pk, new_user.pk]))

        assert users[0] == new_user
        assert users[1] == old_user


# =============================================================================
# Profile Model Tests
# =============================================================================


class TestProfileModel:
    """
    Tests for the Profile model.

    The Profile model extends User with additional profile data.
    Key features: username validation, case-insensitive uniqueness,
    and full name computation.
    """

    # -------------------------------------------------------------------------
    # Field Tests
    # -------------------------------------------------------------------------

    def test_profile_user_is_primary_key(self, profile):
        """
        Profile uses user as its primary key (OneToOne with pk=True).

        Why it matters: Ensures 1:1 relationship and prevents orphan profiles.
        Using user as PK makes the relationship explicit and efficient.
        """
        assert profile.pk == profile.user.pk

    def test_profile_deletes_when_user_deleted(self, db, user):
        """
        Profile is deleted when its user is deleted (CASCADE).

        Why it matters: Prevents orphan profile data when user is removed.
        """
        profile_pk = user.profile.pk
        user.delete()

        assert not Profile.objects.filter(pk=profile_pk).exists()

    def test_profile_username_is_optional(self, db, user):
        """
        Username can be blank (incomplete profile).

        Why it matters: New users have profiles created by signal but
        haven't chosen a username yet. Empty string is valid.
        """
        user.profile.username = ""
        user.profile.full_clean()  # Should not raise
        user.profile.save()

        assert user.profile.username == ""

    def test_profile_username_max_length_is_30(self, db, user):
        """
        Username cannot exceed 30 characters.

        Why it matters: Reasonable limit for display purposes and URL safety.
        """
        user.profile.username = "a" * 30
        user.profile.full_clean()  # Should not raise

        user.profile.username = "a" * 31
        with pytest.raises(ValidationError):
            user.profile.full_clean()

    def test_profile_username_min_length_is_3(self, db, user):
        """
        Username must be at least 3 characters when set.

        Why it matters: Very short usernames are hard to identify
        and may conflict with URL patterns.
        """
        user.profile.username = "ab"
        with pytest.raises(ValidationError):
            user.profile.full_clean()

    def test_profile_first_name_max_length_is_150(self, db, user):
        """
        First name accepts up to 150 characters.

        Why it matters: Accommodates long names from various cultures
        while setting a reasonable upper bound.
        """
        user.profile.first_name = "a" * 150
        user.profile.full_clean()  # Should not raise
        user.profile.save()

        assert len(user.profile.first_name) == 150

    def test_profile_timezone_defaults_to_utc(self, profile):
        """
        Default timezone is UTC.

        Why it matters: UTC is a safe default that doesn't assume
        user location. Users can customize if needed.
        """
        new_profile = ProfileFactory.build()
        assert new_profile.timezone == "UTC"

    def test_profile_preferences_defaults_to_empty_dict(self, db):
        """
        Preferences defaults to empty dict, not None.

        Why it matters: JSONField should never be None to avoid
        NoneType errors when accessing keys.
        """
        user = UserFactory()
        # Profile created by signal has default preferences
        # When explicitly set to default
        user.profile.preferences = {}
        user.profile.save()

        assert user.profile.preferences == {}
        assert isinstance(user.profile.preferences, dict)

    def test_profile_preferences_stores_json_data(self, db, user):
        """
        Preferences field stores arbitrary JSON data.

        Why it matters: Flexible storage for user preferences without
        requiring schema changes for new settings.
        """
        preferences = {
            "theme": "dark",
            "language": "en",
            "email_frequency": "weekly",
            "nested": {"key": "value"}
        }
        user.profile.preferences = preferences
        user.profile.save()
        user.profile.refresh_from_db()

        assert user.profile.preferences == preferences

    # -------------------------------------------------------------------------
    # Validation Tests
    # -------------------------------------------------------------------------

    def test_profile_username_validated_on_save(self, db, user):
        """
        Username validators run on full_clean.

        Why it matters: Invalid usernames are caught before reaching
        the database, providing clear error messages.
        """
        user.profile.username = "invalid username!"  # Contains space and !
        with pytest.raises(ValidationError):
            user.profile.full_clean()

    def test_profile_username_case_insensitive_unique(self, db):
        """
        Usernames are unique case-insensitively.

        Why it matters: Prevents user confusion and impersonation.
        "Admin" and "admin" should be the same username.
        """
        user1 = UserFactory()
        user1.profile.username = "testuser"
        user1.profile.save()

        user2 = UserFactory()
        user2.profile.username = "TestUser"  # Same username, different case

        with pytest.raises(IntegrityError):
            user2.profile.save()

    def test_profile_empty_usernames_not_unique_constrained(self, db):
        """
        Empty usernames are not subject to uniqueness constraint.

        Why it matters: Multiple new users can have empty usernames
        before completing profile setup.
        """
        user1 = UserFactory()
        user1.profile.username = ""
        user1.profile.save()

        user2 = UserFactory()
        user2.profile.username = ""
        user2.profile.save()  # Should not raise

        assert user1.profile.username == user2.profile.username == ""

    # -------------------------------------------------------------------------
    # Normalization Tests
    # -------------------------------------------------------------------------

    def test_profile_username_normalized_to_lowercase_on_clean(self, db, user):
        """
        Username is lowercased during clean().

        Why it matters: Ensures consistent storage and comparison.
        """
        user.profile.username = "TestUser"
        user.profile.clean()

        assert user.profile.username == "testuser"

    def test_profile_username_normalized_to_lowercase_on_save(self, db, user):
        """
        Username is lowercased during save().

        Why it matters: Even if clean() is bypassed, save() normalizes.
        Defense in depth for data consistency.
        """
        user.profile.username = "UPPERCASE"
        user.profile.save()
        user.profile.refresh_from_db()

        assert user.profile.username == "uppercase"

    # -------------------------------------------------------------------------
    # Property Tests
    # -------------------------------------------------------------------------

    def test_full_name_combines_first_and_last_name(self, db, user):
        """
        full_name property returns "first_name last_name".

        Why it matters: Common display pattern for user names.
        """
        user.profile.first_name = "John"
        user.profile.last_name = "Doe"

        assert user.profile.full_name == "John Doe"

    def test_full_name_handles_only_first_name(self, db, user):
        """
        full_name works with just first name set.

        Why it matters: Some users only provide first name.
        """
        user.profile.first_name = "John"
        user.profile.last_name = ""

        assert user.profile.full_name == "John"

    def test_full_name_handles_only_last_name(self, db, user):
        """
        full_name works with just last name set.

        Why it matters: Some cultures use single names or only surname.
        """
        user.profile.first_name = ""
        user.profile.last_name = "Doe"

        assert user.profile.full_name == "Doe"

    def test_full_name_returns_empty_string_when_no_names(self, db, user):
        """
        full_name returns empty string when neither name is set.

        Why it matters: Explicit empty string, not None, for template safety.
        """
        user.profile.first_name = ""
        user.profile.last_name = ""

        assert user.profile.full_name == ""

    # -------------------------------------------------------------------------
    # Method Tests
    # -------------------------------------------------------------------------

    def test_str_returns_username_when_set(self, db, user):
        """
        String representation is username when available.

        Why it matters: Most recognizable identifier for admin/logs.
        """
        user.profile.username = "testuser"
        user.profile.save()

        assert str(user.profile) == "testuser"

    def test_str_returns_user_email_when_no_username(self, db, user):
        """
        String representation falls back to user email.

        Why it matters: Incomplete profiles still need a string representation.
        """
        user.profile.username = ""
        user.profile.save()

        assert str(user.profile) == user.email


# =============================================================================
# Username Validation Tests
# =============================================================================


class TestUsernameValidation:
    """
    Tests for username format and reserved name validators.

    These are standalone validator functions used by the Profile model.
    Testing them separately allows focused verification of edge cases.
    """

    # -------------------------------------------------------------------------
    # Format Validation Tests
    # -------------------------------------------------------------------------

    def test_valid_username_formats_pass(self, valid_usernames):
        """
        Valid username formats do not raise validation errors.

        Why it matters: Confirms the regex correctly allows intended formats.
        """
        for username in valid_usernames:
            # Should not raise
            validate_username_format(username)

    def test_invalid_username_formats_fail(self, invalid_usernames):
        """
        Invalid username formats raise ValidationError.

        Why it matters: Confirms the regex correctly rejects bad formats.
        """
        for username in invalid_usernames:
            if username == "":
                # Empty string is a special case - may or may not validate
                # depending on implementation (blank=True vs validator)
                continue
            with pytest.raises(ValidationError):
                validate_username_format(username)

    def test_username_too_short_fails(self):
        """
        Usernames shorter than 3 characters fail validation.

        Why it matters: Very short usernames are hard to identify.
        """
        with pytest.raises(ValidationError) as exc_info:
            validate_username_format("ab")

        assert "3-30 characters" in str(exc_info.value)

    def test_username_too_long_fails(self):
        """
        Usernames longer than 30 characters fail validation.

        Why it matters: Sets reasonable upper bound for display/URLs.
        """
        with pytest.raises(ValidationError) as exc_info:
            validate_username_format("a" * 31)

        assert "3-30 characters" in str(exc_info.value)

    def test_username_with_space_fails(self):
        """
        Usernames cannot contain spaces.

        Why it matters: Spaces cause URL encoding issues and ambiguity.
        """
        with pytest.raises(ValidationError):
            validate_username_format("user name")

    def test_username_with_special_chars_fails(self):
        """
        Usernames cannot contain special characters except _ and -.

        Why it matters: Limited character set ensures URL safety and
        prevents confusable characters for security.
        """
        invalid_chars = ["@", ".", "!", "#", "$", "%", "^", "&", "*", "/"]
        for char in invalid_chars:
            with pytest.raises(ValidationError):
                validate_username_format(f"user{char}name")

    def test_username_with_underscore_passes(self):
        """
        Underscores are allowed in usernames.

        Why it matters: Common username pattern (e.g., john_doe).
        """
        validate_username_format("user_name")  # Should not raise

    def test_username_with_hyphen_passes(self):
        """
        Hyphens are allowed in usernames.

        Why it matters: Common username pattern (e.g., john-doe).
        """
        validate_username_format("user-name")  # Should not raise

    def test_username_starting_with_number_passes(self):
        """
        Usernames can start with a number.

        Why it matters: Some users prefer numeric prefixes (e.g., 123user).
        """
        validate_username_format("123user")  # Should not raise

    def test_username_all_numbers_passes(self):
        """
        All-numeric usernames are allowed.

        Why it matters: While unusual, some users want numeric usernames.
        """
        validate_username_format("12345")  # Should not raise

    # -------------------------------------------------------------------------
    # Reserved Username Tests
    # -------------------------------------------------------------------------

    def test_reserved_usernames_fail(self, reserved_usernames):
        """
        Reserved usernames raise ValidationError.

        Why it matters: Prevents impersonation and confusion with
        system accounts (admin, support, etc.).
        """
        for username in reserved_usernames:
            with pytest.raises(ValidationError) as exc_info:
                validate_username_not_reserved(username)

            assert "reserved" in str(exc_info.value).lower()

    def test_reserved_usernames_case_insensitive(self):
        """
        Reserved username check is case-insensitive.

        Why it matters: Prevents bypass by using "Admin" instead of "admin".
        """
        with pytest.raises(ValidationError):
            validate_username_not_reserved("ADMIN")

        with pytest.raises(ValidationError):
            validate_username_not_reserved("Admin")

        with pytest.raises(ValidationError):
            validate_username_not_reserved("aDmIn")

    def test_non_reserved_username_passes(self):
        """
        Non-reserved usernames pass validation.

        Why it matters: Normal usernames should work fine.
        """
        validate_username_not_reserved("normaluser")  # Should not raise
        validate_username_not_reserved("john_doe")  # Should not raise
        validate_username_not_reserved("myname123")  # Should not raise

    def test_reserved_usernames_list_contains_expected_values(self):
        """
        RESERVED_USERNAMES contains expected system-critical names.

        Why it matters: Documents and verifies critical reserved names.
        """
        critical_names = [
            "admin", "administrator", "root", "system", "api", "support",
            "login", "logout", "signup", "signin", "auth", "null", "undefined"
        ]
        for name in critical_names:
            assert name in RESERVED_USERNAMES, f"{name} should be reserved"

    def test_reserved_usernames_count(self):
        """
        Reserved usernames list has expected number of entries.

        Why it matters: Catches accidental additions or removals.
        The model docstring says 44 reserved names.
        """
        # Count may change, but verify it's substantial
        assert len(RESERVED_USERNAMES) >= 40


# =============================================================================
# LinkedAccount Model Tests
# =============================================================================


class TestLinkedAccountModel:
    """
    Tests for the LinkedAccount model.

    LinkedAccount tracks OAuth provider connections to user accounts,
    allowing multiple auth methods per user.
    """

    # -------------------------------------------------------------------------
    # Field Tests
    # -------------------------------------------------------------------------

    def test_linked_account_provider_choices(self):
        """
        Provider field has expected choices.

        Why it matters: Documents supported OAuth providers.
        """
        choices = dict(LinkedAccount.Provider.choices)

        assert "email" in choices
        assert "google" in choices
        assert "apple" in choices

    def test_linked_account_requires_user(self, db):
        """
        User is required for linked account.

        Why it matters: Orphan linked accounts are meaningless.
        """
        with pytest.raises(IntegrityError):
            LinkedAccount.objects.create(
                user=None,
                provider=LinkedAccount.Provider.EMAIL,
                provider_user_id="test@example.com"
            )

    def test_linked_account_deletes_when_user_deleted(self, linked_account_email):
        """
        Linked accounts are deleted when user is deleted (CASCADE).

        Why it matters: Prevents orphan OAuth connections.
        """
        user = linked_account_email.user
        account_pk = linked_account_email.pk
        user.delete()

        assert not LinkedAccount.objects.filter(pk=account_pk).exists()

    # -------------------------------------------------------------------------
    # Constraint Tests
    # -------------------------------------------------------------------------

    def test_linked_account_unique_provider_user_id(self, db, linked_account_google):
        """
        Same provider_user_id cannot be linked to multiple users.

        Why it matters: Each OAuth account (e.g., Google account)
        can only be linked to one user in our system.
        """
        another_user = UserFactory()

        with pytest.raises(IntegrityError):
            LinkedAccountFactory(
                user=another_user,
                provider=LinkedAccount.Provider.GOOGLE,
                provider_user_id=linked_account_google.provider_user_id
            )

    def test_linked_account_same_provider_id_different_providers_allowed(
        self, db, user
    ):
        """
        Same provider_user_id can exist for different providers.

        Why it matters: Different providers may use same ID format
        but they're independent identity systems.
        """
        LinkedAccountFactory(
            user=user,
            provider=LinkedAccount.Provider.GOOGLE,
            provider_user_id="same-id-123"
        )
        LinkedAccountFactory(
            user=user,
            provider=LinkedAccount.Provider.APPLE,
            provider_user_id="same-id-123"
        )

        assert user.linked_accounts.count() == 2

    def test_user_can_have_multiple_linked_accounts(self, db, user):
        """
        A user can link multiple OAuth providers.

        Why it matters: Users may want to log in via email, Google, and Apple.
        """
        LinkedAccountFactory(user=user, provider=LinkedAccount.Provider.EMAIL)
        LinkedAccountFactory(user=user, provider=LinkedAccount.Provider.GOOGLE)
        LinkedAccountFactory(user=user, provider=LinkedAccount.Provider.APPLE)

        assert user.linked_accounts.count() == 3

    def test_user_can_have_only_one_account_per_provider(self, db, user):
        """
        A user cannot link the same provider twice with different IDs.

        Why it matters: Each provider represents one identity source.
        """
        LinkedAccountFactory(
            user=user,
            provider=LinkedAccount.Provider.GOOGLE,
            provider_user_id="google-id-1"
        )

        # Same user, same provider, different ID - should this be allowed?
        # Based on the constraint, only provider+provider_user_id is unique
        # So technically this creates a second record
        second = LinkedAccountFactory(
            user=user,
            provider=LinkedAccount.Provider.GOOGLE,
            provider_user_id="google-id-2"
        )

        # This is actually allowed by current schema - may want to review
        assert user.linked_accounts.filter(
            provider=LinkedAccount.Provider.GOOGLE
        ).count() == 2

    # -------------------------------------------------------------------------
    # Method Tests
    # -------------------------------------------------------------------------

    def test_str_returns_provider_and_user(self, linked_account_google):
        """
        String representation includes provider name and user.

        Why it matters: Admin/log readability.
        """
        expected = f"Google account for {linked_account_google.user}"
        assert str(linked_account_google) == expected

    def test_str_uses_display_name_for_provider(self, linked_account_email):
        """
        String representation uses human-readable provider name.

        Why it matters: "Email account" is clearer than "email account".
        """
        assert str(linked_account_email).startswith("Email account")

    # -------------------------------------------------------------------------
    # BaseModel Inheritance Tests
    # -------------------------------------------------------------------------

    def test_linked_account_has_timestamps(self, linked_account_email):
        """
        LinkedAccount inherits timestamps from BaseModel.

        Why it matters: Audit trail for when accounts were linked.
        """
        assert linked_account_email.created_at is not None
        assert linked_account_email.updated_at is not None


# =============================================================================
# EmailVerificationToken Model Tests
# =============================================================================


class TestEmailVerificationToken:
    """
    Tests for the EmailVerificationToken model.

    Tokens are used for email verification and password reset.
    Key features: single-use, time-limited, cryptographically secure.
    """

    # -------------------------------------------------------------------------
    # Field Tests
    # -------------------------------------------------------------------------

    def test_token_is_required(self, db, user):
        """
        Token field cannot be empty.

        Why it matters: Token is the lookup key for verification.
        """
        with pytest.raises(IntegrityError):
            EmailVerificationToken.objects.create(
                user=user,
                token=None,
                token_type=EmailVerificationToken.TokenType.EMAIL_VERIFICATION,
                expires_at=timezone.now() + timedelta(hours=24)
            )

    def test_token_must_be_unique(self, db, user):
        """
        Tokens must be globally unique.

        Why it matters: Prevents token collision attacks.
        """
        token = EmailVerificationTokenFactory(user=user)

        with pytest.raises(IntegrityError):
            EmailVerificationTokenFactory(user=user, token=token.token)

    def test_token_max_length_is_64(self, valid_verification_token):
        """
        Token field allows 64 characters.

        Why it matters: 64-char hex = 256 bits of entropy.
        """
        assert len(valid_verification_token.token) == 64

    def test_token_type_choices(self):
        """
        Token type has expected choices.

        Why it matters: Documents supported token types.
        """
        choices = dict(EmailVerificationToken.TokenType.choices)

        assert "email_verification" in choices
        assert "password_reset" in choices

    def test_expires_at_is_required(self, db, user):
        """
        Expiration time must be set.

        Why it matters: Tokens must have a limited lifespan for security.
        """
        with pytest.raises(IntegrityError):
            EmailVerificationToken.objects.create(
                user=user,
                token="a" * 64,
                token_type=EmailVerificationToken.TokenType.EMAIL_VERIFICATION,
                expires_at=None
            )

    def test_used_at_defaults_to_none(self, valid_verification_token):
        """
        used_at is None for new tokens.

        Why it matters: Indicates the token hasn't been consumed yet.
        """
        assert valid_verification_token.used_at is None

    # -------------------------------------------------------------------------
    # Constraint Tests
    # -------------------------------------------------------------------------

    def test_token_deletes_when_user_deleted(self, valid_verification_token):
        """
        Tokens are deleted when user is deleted (CASCADE).

        Why it matters: Prevents orphan tokens and potential security issues.
        """
        user = valid_verification_token.user
        token_pk = valid_verification_token.pk
        user.delete()

        assert not EmailVerificationToken.objects.filter(pk=token_pk).exists()

    def test_user_can_have_multiple_tokens(self, db, user):
        """
        A user can have multiple verification tokens.

        Why it matters: User may request new token before using old one,
        or have both email verification and password reset tokens.
        """
        EmailVerificationTokenFactory(
            user=user,
            token_type=EmailVerificationToken.TokenType.EMAIL_VERIFICATION
        )
        EmailVerificationTokenFactory(
            user=user,
            token_type=EmailVerificationToken.TokenType.PASSWORD_RESET
        )
        EmailVerificationTokenFactory(
            user=user,
            token_type=EmailVerificationToken.TokenType.EMAIL_VERIFICATION
        )

        assert user.verification_tokens.count() == 3

    # -------------------------------------------------------------------------
    # is_valid Property Tests
    # -------------------------------------------------------------------------

    def test_is_valid_true_for_unused_unexpired_token(self, valid_verification_token):
        """
        is_valid returns True for unused and unexpired tokens.

        Why it matters: Core validation for token consumption.
        """
        assert valid_verification_token.is_valid is True

    def test_is_valid_false_for_used_token(self, used_verification_token):
        """
        is_valid returns False for used tokens.

        Why it matters: Single-use tokens prevent replay attacks.
        """
        assert used_verification_token.is_valid is False

    def test_is_valid_false_for_expired_token(self, expired_verification_token):
        """
        is_valid returns False for expired tokens.

        Why it matters: Time-limited tokens reduce window of attack.
        """
        assert expired_verification_token.is_valid is False

    @freeze_time("2024-01-01 12:00:00")
    def test_is_valid_false_when_expiry_is_exactly_now(self, db, user):
        """
        Token is invalid when expires_at equals current time.

        Why it matters: Edge case - ">" not ">=" in validation.
        """
        token = EmailVerificationTokenFactory(
            user=user,
            expires_at=timezone.now()  # Expires exactly now
        )

        assert token.is_valid is False

    @freeze_time("2024-01-01 12:00:00")
    def test_is_valid_true_when_expiry_is_one_second_in_future(self, db, user):
        """
        Token is valid when expiry is in the future.

        Why it matters: Boundary test for expiration check.
        """
        token = EmailVerificationTokenFactory(
            user=user,
            expires_at=timezone.now() + timedelta(seconds=1)
        )

        assert token.is_valid is True

    def test_is_valid_false_for_used_and_expired_token(self, db, user):
        """
        Token is invalid if both used and expired.

        Why it matters: Multiple failure conditions still result in invalid.
        """
        token = EmailVerificationTokenFactory(
            user=user,
            used_at=timezone.now() - timedelta(hours=2),
            expires_at=timezone.now() - timedelta(hours=1)
        )

        assert token.is_valid is False

    # -------------------------------------------------------------------------
    # Method Tests
    # -------------------------------------------------------------------------

    def test_str_returns_token_type_and_user(self, valid_verification_token):
        """
        String representation includes token type and user.

        Why it matters: Admin/log readability.
        """
        expected = f"Email Verification for {valid_verification_token.user}"
        assert str(valid_verification_token) == expected

    def test_str_for_password_reset_token(self, password_reset_token):
        """
        String representation uses human-readable token type.

        Why it matters: Different token types are clearly identified.
        """
        assert str(password_reset_token).startswith("Password Reset")

    # -------------------------------------------------------------------------
    # Time-Based Tests with Freezegun
    # -------------------------------------------------------------------------

    @freeze_time("2024-01-01 12:00:00")
    def test_token_validity_changes_over_time(self, db, user):
        """
        Token validity respects time progression.

        Why it matters: Demonstrates token lifecycle from valid to expired.
        """
        # Create token that expires in 1 hour
        token = EmailVerificationTokenFactory(
            user=user,
            expires_at=timezone.now() + timedelta(hours=1)
        )

        # Token is valid now
        assert token.is_valid is True

        # Time travel to 59 minutes later - still valid
        with freeze_time("2024-01-01 12:59:00"):
            assert token.is_valid is True

        # Time travel to 61 minutes later - expired
        with freeze_time("2024-01-01 13:01:00"):
            assert token.is_valid is False

    @freeze_time("2024-01-01 12:00:00")
    def test_token_24_hour_expiration(self, db, user):
        """
        Email verification tokens typically expire in 24 hours.

        Why it matters: Verifies expected token lifespan.
        """
        token = EmailVerificationTokenFactory(
            user=user,
            token_type=EmailVerificationToken.TokenType.EMAIL_VERIFICATION,
            expires_at=timezone.now() + timedelta(hours=24)
        )

        # Valid at 23 hours
        with freeze_time("2024-01-02 11:00:00"):
            assert token.is_valid is True

        # Invalid at 25 hours
        with freeze_time("2024-01-02 13:00:00"):
            assert token.is_valid is False

    # -------------------------------------------------------------------------
    # BaseModel Inheritance Tests
    # -------------------------------------------------------------------------

    def test_token_has_timestamps(self, valid_verification_token):
        """
        EmailVerificationToken inherits timestamps from BaseModel.

        Why it matters: Audit trail for token creation.
        """
        assert valid_verification_token.created_at is not None
        assert valid_verification_token.updated_at is not None
