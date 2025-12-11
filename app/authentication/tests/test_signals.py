"""
Tests for authentication signals.

This module tests signal handlers defined in authentication.signals:
- create_user_profile: Auto-creates Profile when User is created
- send_verification_on_registration: Sends verification email for email users
- log_email_verification: Logs when email is verified
- populate_profile_from_social: Populates Profile from OAuth provider data

These tests verify:
- Signal trigger conditions (created flag, update_fields)
- Signal behavior (Profile creation, logging, external service calls)
- Edge cases (OAuth users, missing data, existing profiles)

Related files:
    - signals.py: Signal handlers under test
    - models.py: User, Profile, LinkedAccount models
    - conftest.py: Test fixtures
"""

import logging
from unittest.mock import MagicMock, PropertyMock

import pytest
from django.db import transaction

from authentication.models import (
    User,
    Profile,
    LinkedAccount,
)
from authentication.tests.factories import (
    UserFactory,
    LinkedAccountFactory,
)


# =============================================================================
# TestCreateUserProfileSignal
# =============================================================================


class TestCreateUserProfileSignal:
    """
    Tests for the create_user_profile signal handler.

    This signal auto-creates an empty Profile whenever a new User is created.
    The Profile is created via get_or_create to handle race conditions.

    Test scenarios:
    - Profile created for new users (email and OAuth)
    - Profile not duplicated on User update
    - Profile fields start empty (populated later)
    """

    def test_profile_created_when_user_is_created(self, db):
        """
        Verify a Profile is automatically created for a new User.

        Signal trigger: post_save with created=True
        Expected: Profile.objects.get_or_create(user=instance) is called
        """
        # Act: Create a new user (signals fire automatically)
        user = User.objects.create_user(
            email="newuser@example.com",
            password="SecurePass123!"
        )

        # Assert: Profile exists and is linked to the user
        assert hasattr(user, "profile"), "User should have a profile attribute"
        assert user.profile is not None, "Profile should exist"
        assert user.profile.user == user, "Profile should be linked to the user"

    def test_profile_starts_with_empty_fields(self, db):
        """
        Verify newly created Profile has empty identity fields.

        The signal creates an empty Profile that the user completes later
        during onboarding (setting username, name, etc.).
        """
        # Act: Create user
        user = User.objects.create_user(
            email="newuser@example.com",
            password="SecurePass123!"
        )

        # Assert: Profile fields are empty
        profile = user.profile
        assert profile.username == "", "Username should be empty initially"
        assert profile.first_name == "", "First name should be empty initially"
        assert profile.last_name == "", "Last name should be empty initially"

    def test_profile_not_duplicated_on_user_update(self, user):
        """
        Verify updating a User does not create a duplicate Profile.

        Signal trigger: post_save with created=False
        Expected: Signal handler skips Profile creation
        """
        # Arrange: Get initial profile ID
        initial_profile_id = user.profile.pk

        # Act: Update the user (triggers post_save with created=False)
        user.email_verified = True
        user.save()

        # Assert: Same profile exists, no duplicates
        user.refresh_from_db()
        assert user.profile.pk == initial_profile_id, "Profile ID should not change"
        assert Profile.objects.filter(user=user).count() == 1, (
            "Should have exactly one Profile"
        )

    def test_profile_created_for_oauth_users(self, db):
        """
        Verify Profile is created for OAuth users (verified email).

        OAuth users typically have email_verified=True from the start.
        The Profile should still be created for them.
        """
        # Act: Create user as OAuth would (verified from start)
        user = User.objects.create_user(
            email="oauth@gmail.com",
            password=None,  # OAuth users may not have password
            email_verified=True
        )

        # Assert: Profile exists
        assert hasattr(user, "profile"), "OAuth user should have a profile"
        assert user.profile is not None, "Profile should exist for OAuth user"

    def test_profile_creation_logged(self, db, caplog):
        """
        Verify Profile creation is logged at DEBUG level.

        The signal logs: "Profile created for user: {email}"
        """
        # Arrange: Set log level to capture DEBUG
        with caplog.at_level(logging.DEBUG, logger="authentication.signals"):
            # Act: Create user
            user = User.objects.create_user(
                email="logged@example.com",
                password="SecurePass123!"
            )

        # Assert: Log message contains expected text
        assert any(
            "Profile created for user: logged@example.com" in record.message
            for record in caplog.records
        ), "Should log profile creation with user email"

    def test_get_or_create_handles_race_condition(self, db):
        """
        Verify get_or_create prevents duplicate Profile in race conditions.

        The signal uses get_or_create instead of create to handle cases
        where a Profile might already exist (e.g., from concurrent requests).
        """
        # Arrange: Create user and verify profile exists
        user = User.objects.create_user(
            email="race@example.com",
            password="SecurePass123!"
        )
        assert Profile.objects.filter(user=user).count() == 1

        # Act: Manually call the signal handler again (simulating race condition)
        # This should not create a duplicate due to get_or_create
        from authentication.signals import create_user_profile
        create_user_profile(sender=User, instance=user, created=True)

        # Assert: Still only one Profile
        assert Profile.objects.filter(user=user).count() == 1, (
            "get_or_create should prevent duplicates"
        )


# =============================================================================
# TestSendVerificationOnRegistrationSignal
# =============================================================================


class TestSendVerificationOnRegistrationSignal:
    """
    Tests for the send_verification_on_registration signal handler.

    This signal sends a verification email to users who:
    - Are newly created (created=True)
    - Have unverified email (email_verified=False)
    - Registered via email (have email LinkedAccount)

    OAuth users (Google, Apple) are excluded because their emails are
    pre-verified by the OAuth provider.
    """

    def test_triggered_for_email_registration(self, db, caplog):
        """
        Verify signal triggers for email-registered unverified users.

        Condition: created=True, email_verified=False, has email LinkedAccount
        Expected: Verification email would be sent (logged in TODO)
        """
        with caplog.at_level(logging.DEBUG, logger="authentication.signals"):
            # Act: Create unverified user with email LinkedAccount
            user = User.objects.create_user(
                email="emailuser@example.com",
                password="SecurePass123!",
                email_verified=False
            )
            LinkedAccountFactory(
                user=user,
                provider=LinkedAccount.Provider.EMAIL,
                provider_user_id=user.email
            )

            # Note: The signal fires on user creation, but LinkedAccount
            # is created after. In real code, this would be in a transaction.
            # For testing the signal logic, we call it manually.
            from authentication.signals import send_verification_on_registration
            send_verification_on_registration(
                sender=User,
                instance=user,
                created=True
            )

        # Assert: Log shows verification would be sent
        assert any(
            "Verification email would be sent" in record.message
            and "emailuser@example.com" in record.message
            for record in caplog.records
        ), "Should log that verification email would be sent"

    def test_not_triggered_for_oauth_registration(self, db, caplog):
        """
        Verify signal does NOT trigger for OAuth users.

        OAuth users have pre-verified emails and no email LinkedAccount.
        They have a Google or Apple LinkedAccount instead.
        """
        with caplog.at_level(logging.DEBUG, logger="authentication.signals"):
            # Act: Create verified OAuth user (no email LinkedAccount)
            user = User.objects.create_user(
                email="oauth@gmail.com",
                password=None,
                email_verified=True  # OAuth users are verified
            )
            LinkedAccountFactory(
                user=user,
                provider=LinkedAccount.Provider.GOOGLE,
                provider_user_id="google-uid-123"
            )

            # Call signal manually to test logic
            from authentication.signals import send_verification_on_registration
            send_verification_on_registration(
                sender=User,
                instance=user,
                created=True
            )

        # Assert: No verification email log (already verified)
        assert not any(
            "Verification email would be sent" in record.message
            for record in caplog.records
        ), "Should NOT send verification to OAuth users"

    def test_not_triggered_for_already_verified_users(self, db, caplog):
        """
        Verify signal does NOT trigger for pre-verified users.

        Even with email LinkedAccount, verified users skip verification.
        """
        with caplog.at_level(logging.DEBUG, logger="authentication.signals"):
            # Act: Create already-verified user
            user = User.objects.create_user(
                email="verified@example.com",
                password="SecurePass123!",
                email_verified=True  # Already verified
            )
            LinkedAccountFactory(
                user=user,
                provider=LinkedAccount.Provider.EMAIL,
                provider_user_id=user.email
            )

            from authentication.signals import send_verification_on_registration
            send_verification_on_registration(
                sender=User,
                instance=user,
                created=True
            )

        # Assert: No verification email (already verified)
        assert not any(
            "Verification email would be sent" in record.message
            for record in caplog.records
        ), "Should NOT send verification to already-verified users"

    def test_not_triggered_on_user_updates(self, user, caplog):
        """
        Verify signal does NOT trigger on user updates (created=False).

        Only new user creation should trigger verification emails.
        """
        # Arrange: Make user unverified to ensure condition would match
        user.email_verified = False
        user.save()
        LinkedAccountFactory(
            user=user,
            provider=LinkedAccount.Provider.EMAIL,
            provider_user_id=user.email
        )

        with caplog.at_level(logging.DEBUG, logger="authentication.signals"):
            # Act: Update user (simulates post_save with created=False)
            from authentication.signals import send_verification_on_registration
            send_verification_on_registration(
                sender=User,
                instance=user,
                created=False  # Not a new user
            )

        # Assert: No verification email (not a new user)
        assert not any(
            "Verification email would be sent" in record.message
            for record in caplog.records
        ), "Should NOT send verification on user updates"

    def test_not_triggered_without_email_linked_account(self, db, caplog):
        """
        Verify signal checks for email LinkedAccount before sending.

        A user without email LinkedAccount (e.g., OAuth-only) should not
        receive verification email even if unverified.
        """
        with caplog.at_level(logging.DEBUG, logger="authentication.signals"):
            # Act: Create unverified user WITHOUT email LinkedAccount
            user = User.objects.create_user(
                email="nolinked@example.com",
                password="SecurePass123!",
                email_verified=False
            )
            # No LinkedAccount created

            from authentication.signals import send_verification_on_registration
            send_verification_on_registration(
                sender=User,
                instance=user,
                created=True
            )

        # Assert: No verification email (no email LinkedAccount)
        assert not any(
            "Verification email would be sent" in record.message
            for record in caplog.records
        ), "Should NOT send verification without email LinkedAccount"


# =============================================================================
# TestLogEmailVerificationSignal
# =============================================================================


class TestLogEmailVerificationSignal:
    """
    Tests for the log_email_verification signal handler.

    This signal logs when a user's email is verified for audit/compliance.
    It triggers when:
    - created=False (existing user being updated)
    - update_fields contains "email_verified"
    - email_verified is True (just became verified)
    """

    def test_logs_when_email_verified(self, unverified_user, caplog):
        """
        Verify signal logs when email_verified changes to True.

        Condition: update_fields contains "email_verified", value is True
        Expected: INFO log "Email verified for user: {email}"
        """
        with caplog.at_level(logging.INFO, logger="authentication.signals"):
            # Act: Verify the user's email with update_fields
            unverified_user.email_verified = True
            unverified_user.save(update_fields=["email_verified"])

        # Assert: Verification logged at INFO level
        assert any(
            "Email verified for user:" in record.message
            and unverified_user.email in record.message
            and record.levelno == logging.INFO
            for record in caplog.records
        ), "Should log email verification at INFO level"

    def test_not_triggered_on_user_creation(self, db, caplog):
        """
        Verify signal does NOT trigger when user is created (created=True).

        User creation is handled by other signals. This signal is for
        tracking verification of existing users.
        """
        with caplog.at_level(logging.INFO, logger="authentication.signals"):
            # Act: Create a verified user (created=True)
            User.objects.create_user(
                email="newverified@example.com",
                password="SecurePass123!",
                email_verified=True
            )

        # Assert: No verification log (creation, not update)
        assert not any(
            "Email verified for user:" in record.message
            for record in caplog.records
        ), "Should NOT log verification on user creation"

    def test_not_triggered_without_update_fields(self, unverified_user, caplog):
        """
        Verify signal requires update_fields to include email_verified.

        If save() is called without update_fields, the signal skips.
        This prevents unnecessary logging on unrelated updates.
        """
        with caplog.at_level(logging.INFO, logger="authentication.signals"):
            # Act: Save without specifying update_fields
            unverified_user.email_verified = True
            unverified_user.save()  # No update_fields

        # Assert: No verification log (update_fields not specified)
        # Note: Django passes update_fields=None to signal when not specified
        assert not any(
            "Email verified for user:" in record.message
            for record in caplog.records
        ), "Should NOT log without update_fields"

    def test_not_triggered_for_other_field_updates(self, user, caplog):
        """
        Verify signal only triggers for email_verified field changes.

        Updates to other fields should not trigger the log.
        """
        with caplog.at_level(logging.INFO, logger="authentication.signals"):
            # Act: Update a different field
            user.is_active = False
            user.save(update_fields=["is_active"])

        # Assert: No verification log (different field)
        assert not any(
            "Email verified for user:" in record.message
            for record in caplog.records
        ), "Should NOT log for other field updates"

    def test_not_triggered_when_email_verified_stays_false(self, db, caplog):
        """
        Verify signal only logs when email_verified becomes True.

        If email_verified is explicitly saved as False, no log is produced.
        """
        # Arrange: Create unverified user
        user = User.objects.create_user(
            email="staysfalse@example.com",
            password="SecurePass123!",
            email_verified=False
        )

        with caplog.at_level(logging.INFO, logger="authentication.signals"):
            # Act: Save email_verified=False (no change to True)
            user.save(update_fields=["email_verified"])

        # Assert: No verification log (still False)
        assert not any(
            "Email verified for user:" in record.message
            for record in caplog.records
        ), "Should NOT log when email_verified stays False"

    def test_signal_handler_directly_with_created_false(self, user, caplog):
        """
        Verify direct signal handler invocation with correct parameters.

        Tests the signal handler function directly to verify its logic
        independent of Django's save() mechanism.
        """
        from authentication.signals import log_email_verification

        user.email_verified = True

        with caplog.at_level(logging.INFO, logger="authentication.signals"):
            # Act: Call signal handler directly
            log_email_verification(
                sender=User,
                instance=user,
                created=False,
                update_fields=["email_verified"]
            )

        # Assert: Verification logged
        assert any(
            "Email verified for user:" in record.message
            for record in caplog.records
        ), "Direct handler call should log verification"


# =============================================================================
# TestPopulateProfileFromSocialSignal
# =============================================================================


class TestPopulateProfileFromSocialSignal:
    """
    Tests for the populate_profile_from_social signal handler.

    This signal populates Profile name fields from OAuth provider data.
    It fires on allauth's social_account_added signal when a social
    account is linked to a user.

    Test scenarios:
    - Populates first_name/last_name from extra_data
    - Does not overwrite existing profile names
    - Handles Google and Apple provider formats
    - Gracefully handles missing data
    """

    @pytest.fixture
    def mock_sociallogin(self, user):
        """Create a mock sociallogin object for testing."""
        sociallogin = MagicMock()
        sociallogin.user = user
        sociallogin.account = MagicMock()
        sociallogin.account.provider = "google"
        sociallogin.account.extra_data = {
            "first_name": "John",
            "last_name": "Doe",
        }
        return sociallogin

    @pytest.fixture
    def mock_request(self):
        """Create a mock request object for testing."""
        return MagicMock()

    def test_populates_first_name_from_extra_data(
        self, user, mock_sociallogin, mock_request
    ):
        """
        Verify first_name is populated from extra_data.

        Signal extracts first_name from sociallogin.account.extra_data
        and saves it to the user's Profile.
        """
        from authentication.signals import populate_profile_from_social

        # Arrange: Ensure profile has no first name
        user.profile.first_name = ""
        user.profile.save()

        # Act: Fire the signal handler
        populate_profile_from_social(
            sender=None,
            request=mock_request,
            sociallogin=mock_sociallogin
        )

        # Assert: Profile first_name is populated
        user.profile.refresh_from_db()
        assert user.profile.first_name == "John", (
            "first_name should be populated from extra_data"
        )

    def test_populates_last_name_from_extra_data(
        self, user, mock_sociallogin, mock_request
    ):
        """
        Verify last_name is populated from extra_data.

        Signal extracts last_name from sociallogin.account.extra_data
        and saves it to the user's Profile.
        """
        from authentication.signals import populate_profile_from_social

        # Arrange: Ensure profile has no last name
        user.profile.last_name = ""
        user.profile.save()

        # Act: Fire the signal handler
        populate_profile_from_social(
            sender=None,
            request=mock_request,
            sociallogin=mock_sociallogin
        )

        # Assert: Profile last_name is populated
        user.profile.refresh_from_db()
        assert user.profile.last_name == "Doe", (
            "last_name should be populated from extra_data"
        )

    def test_does_not_overwrite_existing_first_name(
        self, user, mock_sociallogin, mock_request
    ):
        """
        Verify existing first_name is NOT overwritten.

        If the user already has a first_name in their Profile,
        the signal should not replace it with data from OAuth.
        """
        from authentication.signals import populate_profile_from_social

        # Arrange: Set existing first name
        user.profile.first_name = "ExistingFirst"
        user.profile.last_name = ""
        user.profile.save()

        # Act: Fire the signal
        populate_profile_from_social(
            sender=None,
            request=mock_request,
            sociallogin=mock_sociallogin
        )

        # Assert: Existing first_name preserved, last_name populated
        user.profile.refresh_from_db()
        assert user.profile.first_name == "ExistingFirst", (
            "Existing first_name should NOT be overwritten"
        )
        assert user.profile.last_name == "Doe", (
            "Empty last_name should be populated"
        )

    def test_does_not_overwrite_existing_last_name(
        self, user, mock_sociallogin, mock_request
    ):
        """
        Verify existing last_name is NOT overwritten.

        If the user already has a last_name in their Profile,
        the signal should not replace it with data from OAuth.
        """
        from authentication.signals import populate_profile_from_social

        # Arrange: Set existing last name
        user.profile.first_name = ""
        user.profile.last_name = "ExistingLast"
        user.profile.save()

        # Act: Fire the signal
        populate_profile_from_social(
            sender=None,
            request=mock_request,
            sociallogin=mock_sociallogin
        )

        # Assert: first_name populated, existing last_name preserved
        user.profile.refresh_from_db()
        assert user.profile.first_name == "John", (
            "Empty first_name should be populated"
        )
        assert user.profile.last_name == "ExistingLast", (
            "Existing last_name should NOT be overwritten"
        )

    def test_handles_missing_extra_data_gracefully(
        self, user, mock_sociallogin, mock_request
    ):
        """
        Verify signal handles missing extra_data without error.

        If extra_data is empty or missing name fields, the signal
        should not crash and should leave Profile unchanged.
        """
        from authentication.signals import populate_profile_from_social

        # Arrange: Empty extra_data
        mock_sociallogin.account.extra_data = {}
        user.profile.first_name = ""
        user.profile.last_name = ""
        user.profile.save()

        # Act: Fire the signal (should not raise)
        populate_profile_from_social(
            sender=None,
            request=mock_request,
            sociallogin=mock_sociallogin
        )

        # Assert: Profile unchanged, no errors
        user.profile.refresh_from_db()
        assert user.profile.first_name == "", "first_name should remain empty"
        assert user.profile.last_name == "", "last_name should remain empty"

    def test_google_provider_uses_given_family_names(
        self, user, mock_sociallogin, mock_request
    ):
        """
        Verify Google provider format (given_name/family_name) is supported.

        Google OAuth returns names as given_name and family_name.
        If first_name/last_name are missing, these should be used.
        """
        from authentication.signals import populate_profile_from_social

        # Arrange: Google format (given_name, family_name instead of first/last)
        mock_sociallogin.account.provider = "google"
        mock_sociallogin.account.extra_data = {
            "given_name": "GoogleFirst",
            "family_name": "GoogleLast",
            # Note: No first_name/last_name keys
        }
        user.profile.first_name = ""
        user.profile.last_name = ""
        user.profile.save()

        # Act: Fire the signal
        populate_profile_from_social(
            sender=None,
            request=mock_request,
            sociallogin=mock_sociallogin
        )

        # Assert: Names populated from Google format
        user.profile.refresh_from_db()
        assert user.profile.first_name == "GoogleFirst", (
            "Should use given_name for Google provider"
        )
        assert user.profile.last_name == "GoogleLast", (
            "Should use family_name for Google provider"
        )

    def test_prefers_first_last_over_given_family(
        self, user, mock_sociallogin, mock_request
    ):
        """
        Verify first_name/last_name takes precedence over given_name/family_name.

        If both formats are present in extra_data, first_name/last_name wins.
        This ensures adapter-normalized data is preferred.
        """
        from authentication.signals import populate_profile_from_social

        # Arrange: Both formats present
        mock_sociallogin.account.provider = "google"
        mock_sociallogin.account.extra_data = {
            "first_name": "PreferredFirst",
            "last_name": "PreferredLast",
            "given_name": "FallbackFirst",
            "family_name": "FallbackLast",
        }
        user.profile.first_name = ""
        user.profile.last_name = ""
        user.profile.save()

        # Act: Fire the signal
        populate_profile_from_social(
            sender=None,
            request=mock_request,
            sociallogin=mock_sociallogin
        )

        # Assert: first_name/last_name preferred
        user.profile.refresh_from_db()
        assert user.profile.first_name == "PreferredFirst", (
            "Should prefer first_name over given_name"
        )
        assert user.profile.last_name == "PreferredLast", (
            "Should prefer last_name over family_name"
        )

    def test_apple_provider_format(self, user, mock_sociallogin, mock_request):
        """
        Verify Apple provider format is supported.

        Apple may provide name data in different formats. Test that
        the signal handles Apple-specific extra_data structure.
        """
        from authentication.signals import populate_profile_from_social

        # Arrange: Apple format (adapter normalizes to first_name/last_name)
        mock_sociallogin.account.provider = "apple"
        mock_sociallogin.account.extra_data = {
            "first_name": "AppleFirst",
            "last_name": "AppleLast",
        }
        user.profile.first_name = ""
        user.profile.last_name = ""
        user.profile.save()

        # Act: Fire the signal
        populate_profile_from_social(
            sender=None,
            request=mock_request,
            sociallogin=mock_sociallogin
        )

        # Assert: Names populated from Apple format
        user.profile.refresh_from_db()
        assert user.profile.first_name == "AppleFirst", (
            "Should populate first_name from Apple provider"
        )
        assert user.profile.last_name == "AppleLast", (
            "Should populate last_name from Apple provider"
        )

    def test_profile_always_exists_from_user_creation_signal(
        self, user, mock_sociallogin, mock_request
    ):
        """
        Verify Profile always exists when social signal fires.

        In normal operation, the create_user_profile signal always creates
        a Profile when a User is created. The social_account_added signal
        fires AFTER user creation, so the profile always exists.

        This test verifies that assumption holds.
        """
        # Assert: Profile exists (created by create_user_profile signal)
        assert hasattr(user, "profile"), "Profile should exist from user creation"
        assert user.profile is not None, "Profile should be auto-created"

        # The populate_profile_from_social signal relies on this behavior
        from authentication.signals import populate_profile_from_social

        # Act: Fire the social signal (should work because profile exists)
        populate_profile_from_social(
            sender=None,
            request=mock_request,
            sociallogin=mock_sociallogin
        )

        # Assert: Names populated successfully
        user.profile.refresh_from_db()
        assert user.profile.first_name == "John", "first_name should be populated"
        assert user.profile.last_name == "Doe", "last_name should be populated"

    def test_logs_profile_population(
        self, user, mock_sociallogin, mock_request, caplog
    ):
        """
        Verify profile population is logged at DEBUG level.

        The signal logs the provider and populated name data.
        """
        from authentication.signals import populate_profile_from_social

        # Arrange: Empty profile
        user.profile.first_name = ""
        user.profile.last_name = ""
        user.profile.save()

        with caplog.at_level(logging.DEBUG, logger="authentication.signals"):
            # Act: Fire the signal
            populate_profile_from_social(
                sender=None,
                request=mock_request,
                sociallogin=mock_sociallogin
            )

        # Assert: Population logged
        assert any(
            "Profile populated from google" in record.message
            and user.email in record.message
            for record in caplog.records
        ), "Should log profile population with provider"

    def test_no_save_when_nothing_updated(
        self, user, mock_sociallogin, mock_request, caplog
    ):
        """
        Verify Profile.save() is not called when no data is updated.

        If the profile already has names or extra_data has no names,
        the signal should skip the unnecessary save operation.

        We verify this by checking that no "Profile populated" log is emitted,
        since the signal only logs when it actually updates the profile.
        """
        from authentication.signals import populate_profile_from_social

        # Arrange: Profile already has both names
        user.profile.first_name = "ExistingFirst"
        user.profile.last_name = "ExistingLast"
        user.profile.save()

        with caplog.at_level(logging.DEBUG, logger="authentication.signals"):
            # Act: Fire the signal
            populate_profile_from_social(
                sender=None,
                request=mock_request,
                sociallogin=mock_sociallogin
            )

        # Assert: No "Profile populated" log (nothing was updated)
        assert not any(
            "Profile populated from" in record.message
            for record in caplog.records
        ), "Should NOT log profile population when nothing changed"

        # Verify data unchanged
        user.profile.refresh_from_db()
        assert user.profile.first_name == "ExistingFirst", "Name unchanged"
        assert user.profile.last_name == "ExistingLast", "Name unchanged"

    def test_handles_none_extra_data_values(
        self, user, mock_sociallogin, mock_request
    ):
        """
        Verify signal handles None values in extra_data gracefully.

        Some OAuth providers may return None for name fields instead
        of omitting them entirely.
        """
        from authentication.signals import populate_profile_from_social

        # Arrange: extra_data with None values
        mock_sociallogin.account.extra_data = {
            "first_name": None,
            "last_name": None,
        }
        user.profile.first_name = ""
        user.profile.last_name = ""
        user.profile.save()

        # Act: Fire the signal (should not raise)
        populate_profile_from_social(
            sender=None,
            request=mock_request,
            sociallogin=mock_sociallogin
        )

        # Assert: Profile unchanged (None treated as empty)
        user.profile.refresh_from_db()
        assert user.profile.first_name == "", "first_name should remain empty"
        assert user.profile.last_name == "", "last_name should remain empty"

    def test_partial_name_update(
        self, user, mock_sociallogin, mock_request
    ):
        """
        Verify only missing names are populated.

        If only first_name is empty, only first_name should be updated.
        The existing last_name should not be touched.
        """
        from authentication.signals import populate_profile_from_social

        # Arrange: Only last_name exists
        user.profile.first_name = ""
        user.profile.last_name = "ExistingLast"
        user.profile.save()

        # Act: Fire the signal
        populate_profile_from_social(
            sender=None,
            request=mock_request,
            sociallogin=mock_sociallogin
        )

        # Assert: Only first_name populated
        user.profile.refresh_from_db()
        assert user.profile.first_name == "John", (
            "Empty first_name should be populated"
        )
        assert user.profile.last_name == "ExistingLast", (
            "Existing last_name should be preserved"
        )


# =============================================================================
# Integration Tests
# =============================================================================


class TestSignalIntegration:
    """
    Integration tests for signal interactions.

    These tests verify that multiple signals work together correctly
    during common user flows (registration, OAuth login).
    """

    def test_email_registration_flow_triggers_correct_signals(self, db, caplog):
        """
        Verify correct signals fire during email registration.

        Flow:
        1. User.create_user() called
        2. post_save fires with created=True
        3. create_user_profile creates Profile
        4. send_verification_on_registration checks for email LinkedAccount

        Note: LinkedAccount is typically created in the same transaction
        by the registration service, but the signal fires immediately
        after user creation.
        """
        with caplog.at_level(logging.DEBUG, logger="authentication.signals"):
            # Act: Simulate email registration
            user = User.objects.create_user(
                email="register@example.com",
                password="SecurePass123!",
                email_verified=False
            )

        # Assert: Profile created
        assert hasattr(user, "profile"), "Profile should be auto-created"
        assert any(
            "Profile created for user: register@example.com" in record.message
            for record in caplog.records
        ), "Profile creation should be logged"

    def test_oauth_registration_flow(self, db, caplog):
        """
        Verify correct signals fire during OAuth registration.

        OAuth users are created with email_verified=True.
        Profile is still created, but verification email is skipped.
        """
        with caplog.at_level(logging.DEBUG, logger="authentication.signals"):
            # Act: Simulate OAuth registration
            user = User.objects.create_user(
                email="oauth@example.com",
                password=None,
                email_verified=True
            )

        # Assert: Profile created, no verification email
        assert hasattr(user, "profile"), "Profile should be auto-created"
        assert any(
            "Profile created for user: oauth@example.com" in record.message
            for record in caplog.records
        ), "Profile creation should be logged"
        assert not any(
            "Verification email would be sent" in record.message
            for record in caplog.records
        ), "No verification for OAuth users"

    def test_verification_flow_logs_event(self, unverified_user, caplog):
        """
        Verify email verification triggers audit log.

        When a user verifies their email (email_verified: False -> True),
        the event is logged for compliance/audit purposes.
        """
        with caplog.at_level(logging.INFO, logger="authentication.signals"):
            # Act: Verify email
            unverified_user.email_verified = True
            unverified_user.save(update_fields=["email_verified"])

        # Assert: Verification logged
        assert any(
            "Email verified for user:" in record.message
            and record.levelno == logging.INFO
            for record in caplog.records
        ), "Email verification should be logged at INFO level"
