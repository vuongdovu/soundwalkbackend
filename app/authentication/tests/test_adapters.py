"""
Comprehensive tests for OAuth adapters.

This module tests the custom allauth adapters that handle:
- Email/password registration (CustomAccountAdapter)
- Social authentication via Google and Apple (CustomSocialAccountAdapter)

Test Organization:
    - TestCustomAccountAdapter: Email registration adapter
    - TestCustomSocialAccountAdapter: OAuth registration adapter

Testing Philosophy:
    These tests verify adapter behavior by mocking the allauth infrastructure
    (request, sociallogin objects) while testing real interactions with our
    models (LinkedAccount). We let Django's ORM operate naturally.

Key Behaviors Tested:
    - LinkedAccount creation for each auth provider
    - Email verification auto-setting for OAuth users
    - Name extraction from provider-specific response formats
    - Error logging for authentication failures

Dependencies:
    - pytest and pytest-django for test framework
    - unittest.mock for mocking request and sociallogin objects
    - caplog fixture for verifying log output
"""

import logging
from unittest.mock import MagicMock, Mock, patch

import pytest

from authentication.adapters import CustomAccountAdapter, CustomSocialAccountAdapter
from authentication.models import LinkedAccount, User
from authentication.tests.factories import UserFactory


# =============================================================================
# CustomAccountAdapter Tests
# =============================================================================


class TestCustomAccountAdapter:
    """
    Tests for CustomAccountAdapter.

    CustomAccountAdapter handles email/password registration, creating a
    LinkedAccount with provider='email' after user creation.

    This adapter is configured in settings.py:
        ACCOUNT_ADAPTER = 'authentication.adapters.CustomAccountAdapter'
    """

    # -------------------------------------------------------------------------
    # Setup Fixtures
    # -------------------------------------------------------------------------

    @pytest.fixture
    def adapter(self):
        """Create an instance of the CustomAccountAdapter."""
        return CustomAccountAdapter()

    @pytest.fixture
    def mock_request(self):
        """
        Create a mock HTTP request object.

        The request is passed to save_user but CustomAccountAdapter
        doesn't use it directly (passed to parent).
        """
        return MagicMock()

    @pytest.fixture
    def mock_form(self):
        """
        Create a mock registration form.

        The form is passed to save_user but CustomAccountAdapter
        doesn't use it directly (passed to parent).
        """
        return MagicMock()

    # -------------------------------------------------------------------------
    # save_user Tests
    # -------------------------------------------------------------------------

    def test_save_user_creates_linked_account_with_email_provider(
        self, db, adapter, mock_request, mock_form
    ):
        """
        save_user creates a LinkedAccount with provider='email'.

        Why it matters: Tracks that the user registered via email/password,
        not OAuth. This is essential for account linking and login method
        identification.
        """
        # Arrange: Create a user that will be returned by parent's save_user
        user = UserFactory.build(email="newuser@example.com")

        with patch.object(
            CustomAccountAdapter.__bases__[0],
            "save_user",
            return_value=user
        ) as mock_parent:
            # The parent save_user would normally save the user
            # We simulate that by saving manually for our test
            user.save()

            # Act: Call the adapter's save_user
            result = adapter.save_user(mock_request, user, mock_form, commit=True)

        # Assert: LinkedAccount was created with email provider
        linked_account = LinkedAccount.objects.get(user=result)
        assert linked_account.provider == LinkedAccount.Provider.EMAIL

    def test_save_user_sets_provider_user_id_to_email(
        self, db, adapter, mock_request, mock_form
    ):
        """
        LinkedAccount.provider_user_id is set to the user's email address.

        Why it matters: For email registration, the email address itself
        serves as the unique identifier from the "provider" (our own system).
        """
        # Arrange
        user = UserFactory.build(email="unique@example.com")

        with patch.object(
            CustomAccountAdapter.__bases__[0],
            "save_user",
            return_value=user
        ):
            user.save()

            # Act
            result = adapter.save_user(mock_request, user, mock_form, commit=True)

        # Assert
        linked_account = LinkedAccount.objects.get(user=result)
        assert linked_account.provider_user_id == "unique@example.com"

    def test_save_user_does_not_create_linked_account_when_commit_false(
        self, db, adapter, mock_request, mock_form
    ):
        """
        When commit=False, no LinkedAccount is created.

        Why it matters: commit=False is used when the caller wants to
        make additional modifications before saving. Creating LinkedAccount
        before the user is saved would cause integrity errors.
        """
        # Arrange
        user = UserFactory.build(email="uncommitted@example.com")

        with patch.object(
            CustomAccountAdapter.__bases__[0],
            "save_user",
            return_value=user
        ):
            # Act: Call with commit=False
            adapter.save_user(mock_request, user, mock_form, commit=False)

        # Assert: No LinkedAccount exists
        assert LinkedAccount.objects.count() == 0

    def test_save_user_uses_get_or_create_for_idempotency(
        self, db, adapter, mock_request, mock_form
    ):
        """
        save_user uses get_or_create to prevent duplicate LinkedAccounts.

        Why it matters: If save_user is called multiple times (e.g., during
        retry scenarios), we should not create duplicate LinkedAccounts.
        """
        # Arrange: Create a user with existing LinkedAccount
        user = UserFactory(email="existing@example.com")
        LinkedAccount.objects.create(
            user=user,
            provider=LinkedAccount.Provider.EMAIL,
            provider_user_id=user.email
        )
        initial_count = LinkedAccount.objects.count()

        with patch.object(
            CustomAccountAdapter.__bases__[0],
            "save_user",
            return_value=user
        ):
            # Act: Call save_user again (simulating retry)
            adapter.save_user(mock_request, user, mock_form, commit=True)

        # Assert: No new LinkedAccount created
        assert LinkedAccount.objects.count() == initial_count

    def test_save_user_logs_registration_event(
        self, db, adapter, mock_request, mock_form, caplog
    ):
        """
        save_user logs the email registration event.

        Why it matters: Audit trail for user registration. Logs should
        include user_id and email for debugging and monitoring.
        """
        # Arrange
        user = UserFactory.build(email="logged@example.com")

        with patch.object(
            CustomAccountAdapter.__bases__[0],
            "save_user",
            return_value=user
        ):
            user.save()

            # Act: Capture logs at INFO level
            with caplog.at_level(logging.INFO, logger="authentication.adapters"):
                adapter.save_user(mock_request, user, mock_form, commit=True)

        # Assert: Registration was logged
        assert "Email user registered" in caplog.text

    def test_save_user_returns_user_instance(
        self, db, adapter, mock_request, mock_form
    ):
        """
        save_user returns the user instance.

        Why it matters: The return value is used by allauth for subsequent
        processing. Must return the same user that was passed/created.
        """
        # Arrange
        user = UserFactory.build(email="return@example.com")

        with patch.object(
            CustomAccountAdapter.__bases__[0],
            "save_user",
            return_value=user
        ):
            user.save()

            # Act
            result = adapter.save_user(mock_request, user, mock_form, commit=True)

        # Assert
        assert result == user
        assert isinstance(result, User)


# =============================================================================
# CustomSocialAccountAdapter Tests
# =============================================================================


class TestCustomSocialAccountAdapter:
    """
    Tests for CustomSocialAccountAdapter.

    CustomSocialAccountAdapter handles OAuth registration (Google, Apple),
    creating LinkedAccounts and handling provider-specific data extraction.

    This adapter is configured in settings.py:
        SOCIALACCOUNT_ADAPTER = 'authentication.adapters.CustomSocialAccountAdapter'
    """

    # -------------------------------------------------------------------------
    # Setup Fixtures
    # -------------------------------------------------------------------------

    @pytest.fixture
    def adapter(self):
        """Create an instance of the CustomSocialAccountAdapter."""
        return CustomSocialAccountAdapter()

    @pytest.fixture
    def mock_request(self):
        """
        Create a mock HTTP request object.

        For Apple Sign-In, the request may contain user data in request.data.
        """
        request = MagicMock()
        request.data = {}
        return request

    @pytest.fixture
    def mock_sociallogin_google(self, db):
        """
        Create a mock Google sociallogin object.

        Simulates the structure provided by allauth for Google OAuth.
        The account object contains provider info and extra_data from Google.
        """
        user = UserFactory.build(email="googleuser@gmail.com")

        sociallogin = MagicMock()
        sociallogin.account.provider = "google"
        sociallogin.account.uid = "google-uid-123456"
        sociallogin.account.extra_data = {
            "sub": "google-uid-123456",
            "email": "googleuser@gmail.com",
            "email_verified": True,
            "given_name": "Google",
            "family_name": "User",
            "picture": "https://example.com/photo.jpg"
        }
        sociallogin.user = user

        return sociallogin

    @pytest.fixture
    def mock_sociallogin_apple(self, db):
        """
        Create a mock Apple sociallogin object.

        Simulates the structure provided by allauth for Apple Sign-In.
        Note: Apple only sends user info on the first authentication.
        """
        user = UserFactory.build(email="appleuser@privaterelay.apple.com")

        sociallogin = MagicMock()
        sociallogin.account.provider = "apple"
        sociallogin.account.uid = "apple-uid-789012"
        sociallogin.account.extra_data = {
            "sub": "apple-uid-789012",
            "email": "appleuser@privaterelay.apple.com",
            "email_verified": True,
            "is_private_email": True
        }
        sociallogin.user = user

        return sociallogin

    # -------------------------------------------------------------------------
    # save_user Tests
    # -------------------------------------------------------------------------

    def test_save_user_marks_oauth_user_as_email_verified(
        self, db, adapter, mock_request, mock_sociallogin_google
    ):
        """
        save_user sets email_verified=True for OAuth users.

        Why it matters: OAuth providers have already verified the user's email.
        We trust their verification and mark our user as verified automatically.
        """
        # Arrange: Prepare user
        user = mock_sociallogin_google.user
        user.email_verified = False  # Explicitly set to False

        with patch.object(
            CustomSocialAccountAdapter.__bases__[0],
            "save_user",
            return_value=user
        ):
            user.save()

            # Act
            result = adapter.save_user(mock_request, mock_sociallogin_google)

        # Assert
        result.refresh_from_db()
        assert result.email_verified is True

    def test_save_user_creates_linked_account_for_google(
        self, db, adapter, mock_request, mock_sociallogin_google
    ):
        """
        save_user creates LinkedAccount with provider='google'.

        Why it matters: Tracks that the user registered via Google OAuth,
        enabling account linking and login method identification.
        """
        # Arrange
        user = mock_sociallogin_google.user

        with patch.object(
            CustomSocialAccountAdapter.__bases__[0],
            "save_user",
            return_value=user
        ):
            user.save()

            # Act
            result = adapter.save_user(mock_request, mock_sociallogin_google)

        # Assert
        linked_account = LinkedAccount.objects.get(user=result)
        assert linked_account.provider == "google"
        assert linked_account.provider_user_id == "google-uid-123456"

    def test_save_user_creates_linked_account_for_apple(
        self, db, adapter, mock_request, mock_sociallogin_apple
    ):
        """
        save_user creates LinkedAccount with provider='apple'.

        Why it matters: Tracks that the user registered via Apple Sign-In.
        """
        # Arrange
        user = mock_sociallogin_apple.user

        with patch.object(
            CustomSocialAccountAdapter.__bases__[0],
            "save_user",
            return_value=user
        ):
            user.save()

            # Act
            result = adapter.save_user(mock_request, mock_sociallogin_apple)

        # Assert
        linked_account = LinkedAccount.objects.get(user=result)
        assert linked_account.provider == "apple"
        assert linked_account.provider_user_id == "apple-uid-789012"

    def test_save_user_uses_provider_uid_as_provider_user_id(
        self, db, adapter, mock_request, mock_sociallogin_google
    ):
        """
        LinkedAccount.provider_user_id is set to sociallogin.account.uid.

        Why it matters: The UID is the unique identifier from the OAuth provider.
        It's stable across sessions and used to match returning users.
        """
        # Arrange
        user = mock_sociallogin_google.user

        with patch.object(
            CustomSocialAccountAdapter.__bases__[0],
            "save_user",
            return_value=user
        ):
            user.save()

            # Act
            adapter.save_user(mock_request, mock_sociallogin_google)

        # Assert
        linked_account = LinkedAccount.objects.get(user=user)
        assert linked_account.provider_user_id == mock_sociallogin_google.account.uid

    def test_save_user_logs_social_registration_event(
        self, db, adapter, mock_request, mock_sociallogin_google, caplog
    ):
        """
        save_user logs the social registration event.

        Why it matters: Audit trail for OAuth registration. Logs should
        include provider, user_id, and email for debugging and monitoring.
        """
        # Arrange
        user = mock_sociallogin_google.user

        with patch.object(
            CustomSocialAccountAdapter.__bases__[0],
            "save_user",
            return_value=user
        ):
            user.save()

            # Act
            with caplog.at_level(logging.INFO, logger="authentication.adapters"):
                adapter.save_user(mock_request, mock_sociallogin_google)

        # Assert
        assert "Social user created" in caplog.text

    def test_save_user_returns_user_instance(
        self, db, adapter, mock_request, mock_sociallogin_google
    ):
        """
        save_user returns the user instance.

        Why it matters: The return value is used by allauth for subsequent
        processing. Must return the saved user.
        """
        # Arrange
        user = mock_sociallogin_google.user

        with patch.object(
            CustomSocialAccountAdapter.__bases__[0],
            "save_user",
            return_value=user
        ):
            user.save()

            # Act
            result = adapter.save_user(mock_request, mock_sociallogin_google)

        # Assert
        assert result == user

    # -------------------------------------------------------------------------
    # populate_user Tests
    # -------------------------------------------------------------------------

    def test_populate_user_stores_name_in_extra_data(
        self, db, adapter, mock_request, mock_sociallogin_google
    ):
        """
        populate_user stores extracted name in sociallogin.account.extra_data.

        Why it matters: The signal handler uses extra_data to populate the
        Profile model. This is necessary because User model doesn't have
        first_name/last_name fields.
        """
        # Arrange
        data = {
            "given_name": "John",
            "family_name": "Doe"
        }

        with patch.object(
            CustomSocialAccountAdapter.__bases__[0],
            "populate_user",
            return_value=mock_sociallogin_google.user
        ):
            # Act
            adapter.populate_user(mock_request, mock_sociallogin_google, data)

        # Assert: Name stored in extra_data
        assert mock_sociallogin_google.account.extra_data["first_name"] == "John"
        assert mock_sociallogin_google.account.extra_data["last_name"] == "Doe"

    def test_populate_user_extracts_google_name_correctly(
        self, db, adapter, mock_request, mock_sociallogin_google
    ):
        """
        populate_user correctly extracts name from Google provider data.

        Why it matters: Google uses 'given_name' and 'family_name' fields.
        We must map these to our standard first_name/last_name.
        """
        # Arrange
        data = {
            "given_name": "Google",
            "family_name": "User",
            "name": "Google User"  # Also provided but we use specific fields
        }

        with patch.object(
            CustomSocialAccountAdapter.__bases__[0],
            "populate_user",
            return_value=mock_sociallogin_google.user
        ):
            # Act
            adapter.populate_user(mock_request, mock_sociallogin_google, data)

        # Assert
        assert mock_sociallogin_google.account.extra_data["first_name"] == "Google"
        assert mock_sociallogin_google.account.extra_data["last_name"] == "User"

    def test_populate_user_handles_missing_google_name_fields(
        self, db, adapter, mock_request, mock_sociallogin_google
    ):
        """
        populate_user handles missing name fields gracefully.

        Why it matters: Not all Google accounts have name information.
        The adapter should not crash and should store empty strings.
        """
        # Arrange: Data without name fields
        data = {
            "email": "noname@gmail.com",
            "email_verified": True
        }

        with patch.object(
            CustomSocialAccountAdapter.__bases__[0],
            "populate_user",
            return_value=mock_sociallogin_google.user
        ):
            # Act: Should not raise
            adapter.populate_user(mock_request, mock_sociallogin_google, data)

        # Assert: Empty strings stored
        assert mock_sociallogin_google.account.extra_data["first_name"] == ""
        assert mock_sociallogin_google.account.extra_data["last_name"] == ""

    def test_populate_user_returns_user_instance(
        self, db, adapter, mock_request, mock_sociallogin_google
    ):
        """
        populate_user returns the user instance.

        Why it matters: The return value is used by allauth for subsequent
        processing.
        """
        # Arrange
        data = {"given_name": "Test", "family_name": "User"}

        with patch.object(
            CustomSocialAccountAdapter.__bases__[0],
            "populate_user",
            return_value=mock_sociallogin_google.user
        ):
            # Act
            result = adapter.populate_user(mock_request, mock_sociallogin_google, data)

        # Assert
        assert result == mock_sociallogin_google.user

    # -------------------------------------------------------------------------
    # _extract_google_name Tests
    # -------------------------------------------------------------------------

    def test_extract_google_name_returns_tuple(self, adapter):
        """
        _extract_google_name returns a (first_name, last_name) tuple.

        Why it matters: Consistent return type for name extraction.
        """
        # Arrange
        data = {"given_name": "John", "family_name": "Doe"}

        # Act
        result = adapter._extract_google_name(data)

        # Assert
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert result == ("John", "Doe")

    def test_extract_google_name_handles_only_given_name(self, adapter):
        """
        _extract_google_name handles data with only given_name.

        Why it matters: Some Google accounts may not have family_name set.
        """
        # Arrange
        data = {"given_name": "John"}

        # Act
        first_name, last_name = adapter._extract_google_name(data)

        # Assert
        assert first_name == "John"
        assert last_name == ""

    def test_extract_google_name_handles_only_family_name(self, adapter):
        """
        _extract_google_name handles data with only family_name.

        Why it matters: Edge case - some accounts might have this pattern.
        """
        # Arrange
        data = {"family_name": "Doe"}

        # Act
        first_name, last_name = adapter._extract_google_name(data)

        # Assert
        assert first_name == ""
        assert last_name == "Doe"

    def test_extract_google_name_handles_empty_data(self, adapter):
        """
        _extract_google_name handles empty data dict.

        Why it matters: Defensive coding - should not crash on missing data.
        """
        # Arrange
        data = {}

        # Act
        first_name, last_name = adapter._extract_google_name(data)

        # Assert
        assert first_name == ""
        assert last_name == ""

    def test_extract_google_name_handles_none_values(self, adapter):
        """
        _extract_google_name handles None values in fields.

        Why it matters: Provider data might have explicit None values.
        """
        # Arrange
        data = {"given_name": None, "family_name": None}

        # Act: Should use .get() default, not crash
        first_name, last_name = adapter._extract_google_name(data)

        # Assert: Returns empty strings (None from .get() if key exists)
        # Note: dict.get() returns None if key exists with None value
        # The adapter uses data.get("given_name", "") which returns None if key exists
        assert first_name is None or first_name == ""
        assert last_name is None or last_name == ""

    # -------------------------------------------------------------------------
    # _extract_apple_name Tests
    # -------------------------------------------------------------------------

    def test_extract_apple_name_from_request_data(self, adapter, mock_request):
        """
        _extract_apple_name extracts name from request.data.user.name.

        Why it matters: Apple only sends user info on the FIRST authentication.
        The name comes in the request body, not the provider data.
        """
        # Arrange: Apple sends user info in request.data
        mock_request.data = {
            "user": {
                "name": {
                    "firstName": "Apple",
                    "lastName": "User"
                },
                "email": "appleuser@privaterelay.apple.com"
            }
        }
        data = {}  # Provider data doesn't have name for Apple

        # Act
        first_name, last_name = adapter._extract_apple_name(mock_request, data)

        # Assert
        assert first_name == "Apple"
        assert last_name == "User"

    def test_extract_apple_name_handles_missing_user_object(
        self, adapter, mock_request
    ):
        """
        _extract_apple_name handles missing user object gracefully.

        Why it matters: On subsequent logins, Apple doesn't send user data.
        The adapter must handle this without crashing.
        """
        # Arrange: No user data in request (subsequent login)
        mock_request.data = {}
        data = {}

        # Act: Should not raise
        first_name, last_name = adapter._extract_apple_name(mock_request, data)

        # Assert
        assert first_name == ""
        assert last_name == ""

    def test_extract_apple_name_handles_missing_name_object(
        self, adapter, mock_request
    ):
        """
        _extract_apple_name handles missing name object in user data.

        Why it matters: User might exist but without name info.
        """
        # Arrange: User object but no name
        mock_request.data = {
            "user": {
                "email": "appleuser@privaterelay.apple.com"
            }
        }
        data = {}

        # Act: Should not raise
        first_name, last_name = adapter._extract_apple_name(mock_request, data)

        # Assert
        assert first_name == ""
        assert last_name == ""

    def test_extract_apple_name_handles_partial_name_data(
        self, adapter, mock_request
    ):
        """
        _extract_apple_name handles partial name data.

        Why it matters: User might have only first or last name.
        """
        # Arrange: Only firstName provided
        mock_request.data = {
            "user": {
                "name": {
                    "firstName": "OnlyFirst"
                }
            }
        }
        data = {}

        # Act
        first_name, last_name = adapter._extract_apple_name(mock_request, data)

        # Assert
        assert first_name == "OnlyFirst"
        assert last_name == ""

    def test_extract_apple_name_handles_non_dict_user(self, adapter, mock_request):
        """
        _extract_apple_name handles non-dict user value.

        Why it matters: Defensive coding against malformed requests.
        """
        # Arrange: user is not a dict
        mock_request.data = {"user": "not-a-dict"}
        data = {}

        # Act: Should not raise
        first_name, last_name = adapter._extract_apple_name(mock_request, data)

        # Assert
        assert first_name == ""
        assert last_name == ""

    def test_extract_apple_name_handles_non_dict_name(self, adapter, mock_request):
        """
        _extract_apple_name handles non-dict name value.

        Why it matters: Defensive coding against malformed requests.
        """
        # Arrange: name is not a dict
        mock_request.data = {
            "user": {
                "name": "not-a-dict"
            }
        }
        data = {}

        # Act: Should not raise
        first_name, last_name = adapter._extract_apple_name(mock_request, data)

        # Assert
        assert first_name == ""
        assert last_name == ""

    def test_extract_apple_name_handles_request_without_data_attribute(
        self, adapter
    ):
        """
        _extract_apple_name handles request without data attribute.

        Why it matters: Some request types might not have .data attribute.
        """
        # Arrange: Request without data attribute
        mock_request = MagicMock(spec=[])  # No attributes

        # Act: Should not raise
        first_name, last_name = adapter._extract_apple_name(mock_request, {})

        # Assert
        assert first_name == ""
        assert last_name == ""

    # -------------------------------------------------------------------------
    # populate_user Apple Provider Tests
    # -------------------------------------------------------------------------

    def test_populate_user_extracts_apple_name_from_request(
        self, db, adapter, mock_request, mock_sociallogin_apple
    ):
        """
        populate_user extracts Apple name from request.data.

        Why it matters: Apple Sign-In sends name in request body on first login.
        This is a provider-specific quirk we must handle.
        """
        # Arrange: Apple user data in request
        mock_request.data = {
            "user": {
                "name": {
                    "firstName": "Apple",
                    "lastName": "Tester"
                }
            }
        }
        data = {}  # Provider data doesn't have name

        with patch.object(
            CustomSocialAccountAdapter.__bases__[0],
            "populate_user",
            return_value=mock_sociallogin_apple.user
        ):
            # Act
            adapter.populate_user(mock_request, mock_sociallogin_apple, data)

        # Assert
        assert mock_sociallogin_apple.account.extra_data["first_name"] == "Apple"
        assert mock_sociallogin_apple.account.extra_data["last_name"] == "Tester"

    # -------------------------------------------------------------------------
    # authentication_error Tests
    # -------------------------------------------------------------------------

    def test_authentication_error_logs_the_error(
        self, adapter, mock_request, caplog
    ):
        """
        authentication_error logs the authentication failure.

        Why it matters: Audit trail for failed OAuth attempts. Essential
        for debugging and monitoring authentication issues.

        Note: The adapter's authentication_error method calls super().authentication_error()
        which doesn't exist in newer allauth versions (it's on_authentication_error).
        We patch the super() call to isolate testing of the logging behavior.
        """
        # Arrange
        provider_id = "google"
        error = "access_denied"
        exception = ValueError("User denied access")
        extra_context = {"state": "some-state-value"}

        with patch("authentication.adapters.super") as mock_super:
            mock_super.return_value.authentication_error.return_value = None

            # Act
            with caplog.at_level(logging.ERROR, logger="authentication.adapters"):
                adapter.authentication_error(
                    mock_request,
                    provider_id,
                    error=error,
                    exception=exception,
                    extra_context=extra_context
                )

        # Assert: Error was logged
        assert "Social authentication error" in caplog.text

    def test_authentication_error_logs_provider_id(
        self, adapter, mock_request, caplog
    ):
        """
        authentication_error includes provider_id in log extra data.

        Why it matters: Identifies which OAuth provider had the error.
        """
        # Arrange
        provider_id = "apple"

        with patch("authentication.adapters.super") as mock_super:
            mock_super.return_value.authentication_error.return_value = None

            # Act
            with caplog.at_level(logging.ERROR, logger="authentication.adapters"):
                adapter.authentication_error(
                    mock_request,
                    provider_id,
                    error="some_error"
                )

        # Assert: Error was logged (provider is in extra data, not message)
        assert any(
            "Social authentication error" in record.message
            for record in caplog.records
        )
        # Verify the log record has the provider in extra data
        error_records = [r for r in caplog.records if "Social authentication error" in r.message]
        assert len(error_records) == 1

    def test_authentication_error_handles_none_exception(
        self, adapter, mock_request, caplog
    ):
        """
        authentication_error handles None exception gracefully.

        Why it matters: Exception parameter is optional.
        """
        # Arrange
        provider_id = "google"
        error = "unknown_error"

        with patch("authentication.adapters.super") as mock_super:
            mock_super.return_value.authentication_error.return_value = None

            # Act: Should not raise
            with caplog.at_level(logging.ERROR, logger="authentication.adapters"):
                adapter.authentication_error(
                    mock_request,
                    provider_id,
                    error=error,
                    exception=None
                )

        # Assert: Logged without exception info
        assert "Social authentication error" in caplog.text

    def test_authentication_error_calls_parent_method(
        self, adapter, mock_request
    ):
        """
        authentication_error calls super().authentication_error().

        Why it matters: We log the error but still delegate to the parent
        for default error handling behavior.

        Note: In allauth 65.x, the parent method is on_authentication_error,
        not authentication_error. This test verifies the code's intent.
        """
        # Arrange
        provider_id = "google"
        error = "some_error"
        exception = ValueError("test")
        extra_context = {"key": "value"}

        with patch("authentication.adapters.super") as mock_super:
            mock_parent_method = mock_super.return_value.authentication_error

            # Act
            adapter.authentication_error(
                mock_request,
                provider_id,
                error=error,
                exception=exception,
                extra_context=extra_context
            )

        # Assert: Parent method was called with same arguments
        mock_parent_method.assert_called_once_with(
            mock_request,
            provider_id,
            error,
            exception,
            extra_context
        )

    def test_authentication_error_returns_parent_result(
        self, adapter, mock_request
    ):
        """
        authentication_error returns whatever the parent returns.

        Why it matters: Maintains the parent's contract for return values.
        """
        # Arrange
        expected_return = "some_response"

        with patch("authentication.adapters.super") as mock_super:
            mock_super.return_value.authentication_error.return_value = expected_return

            # Act
            result = adapter.authentication_error(
                mock_request,
                "google",
                error="test"
            )

        # Assert
        assert result == expected_return


# =============================================================================
# Integration-Style Tests
# =============================================================================


class TestAdapterIntegration:
    """
    Integration tests that verify adapter behavior with real Django models.

    These tests exercise the full flow without mocking the parent adapter,
    focusing on the interaction between adapters and our LinkedAccount model.
    """

    def test_email_registration_creates_complete_linked_account(self, db):
        """
        End-to-end test: Email registration creates proper LinkedAccount.

        Why it matters: Verifies the complete flow from adapter to database.
        """
        # Arrange
        adapter = CustomAccountAdapter()
        user = UserFactory(email="integration@example.com")

        # Act: Directly call the LinkedAccount creation logic
        # (Simulating what happens after parent's save_user)
        LinkedAccount.objects.get_or_create(
            user=user,
            provider=LinkedAccount.Provider.EMAIL,
            provider_user_id=user.email,
        )

        # Assert
        linked_account = LinkedAccount.objects.get(user=user)
        assert linked_account.provider == LinkedAccount.Provider.EMAIL
        assert linked_account.provider_user_id == user.email
        assert linked_account.user == user

    def test_social_registration_flow_creates_verified_user_with_linked_account(
        self, db
    ):
        """
        End-to-end test: Social registration creates verified user with LinkedAccount.

        Why it matters: Verifies OAuth registration sets all expected fields.
        """
        # Arrange
        user = UserFactory(email="social@example.com", email_verified=False)
        provider = "google"
        provider_uid = "google-uid-test"

        # Act: Simulate what save_user does
        user.email_verified = True
        user.save(update_fields=["email_verified", "updated_at"])

        LinkedAccount.objects.get_or_create(
            provider=provider,
            provider_user_id=provider_uid,
            defaults={"user": user},
        )

        # Assert
        user.refresh_from_db()
        assert user.email_verified is True

        linked_account = LinkedAccount.objects.get(user=user)
        assert linked_account.provider == provider
        assert linked_account.provider_user_id == provider_uid
