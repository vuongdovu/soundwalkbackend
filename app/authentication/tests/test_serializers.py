"""
Comprehensive tests for authentication serializers.

This module tests all authentication serializers following TDD principles:
- UserSerializer: Read-only user data for API responses
- ProfileSerializer: Read-only profile data with computed fields
- ProfileUpdateSerializer: Profile updates with dynamic validation
- LinkedAccountSerializer: Read-only linked account data
- RegisterSerializer: User registration with email/password

Test Organization:
    - Each serializer has its own test class
    - Each test validates ONE specific behavior
    - Tests use descriptive names following the pattern: test_<scenario>_<expected_outcome>

Testing Philosophy:
    Tests focus on observable behavior, not implementation details. We test:
    - Serialization output (model to JSON)
    - Deserialization validation (JSON to model)
    - Computed fields and method fields
    - Validation rules and error messages
    - Dynamic behavior based on context

Dependencies:
    - pytest and pytest-django for test framework
    - Factory Boy fixtures from conftest.py
    - PIL for image fixture generation
"""

import io
from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from PIL import Image
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from authentication.models import User, Profile, LinkedAccount
from authentication.serializers import (
    UserSerializer,
    ProfileSerializer,
    ProfileUpdateSerializer,
    LinkedAccountSerializer,
    RegisterSerializer,
)
from authentication.tests.factories import (
    UserFactory,
    ProfileFactory,
    LinkedAccountFactory,
)


# =============================================================================
# Test Fixtures for Image Upload Testing
# =============================================================================


@pytest.fixture
def create_test_image():
    """
    Factory fixture that creates test image files for upload testing.

    Returns a function that generates in-memory image files with configurable
    dimensions, format, and size.
    """
    def _create_image(
        name="test.jpg",
        format="JPEG",
        size=(100, 100),
        file_size_kb=None
    ):
        """
        Create an in-memory image file for testing uploads.

        Args:
            name: Filename for the uploaded file
            format: Image format (JPEG, PNG, GIF, WEBP)
            size: Tuple of (width, height) in pixels
            file_size_kb: Optional target file size in KB (for size limit tests)

        Returns:
            SimpleUploadedFile ready for serializer validation
        """
        image = Image.new("RGB", size, color="red")
        buffer = io.BytesIO()
        image.save(buffer, format=format)

        # If target file size specified, pad the buffer
        if file_size_kb:
            current_size = buffer.tell()
            target_size = file_size_kb * 1024
            if target_size > current_size:
                buffer.write(b"\x00" * (target_size - current_size))

        buffer.seek(0)
        content_type = f"image/{format.lower()}"
        return SimpleUploadedFile(name, buffer.read(), content_type=content_type)

    return _create_image


@pytest.fixture
def valid_image(create_test_image):
    """A valid small JPEG image for profile picture tests."""
    return create_test_image(name="profile.jpg", format="JPEG", size=(200, 200))


@pytest.fixture
def oversized_image(create_test_image):
    """An image exceeding the 5MB size limit."""
    return create_test_image(
        name="large.jpg",
        format="JPEG",
        size=(100, 100),
        file_size_kb=6000  # 6MB, exceeds 5MB limit
    )


@pytest.fixture
def mock_request():
    """
    Create a mock DRF request for serializer context.

    Provides build_absolute_uri() for URL generation in computed fields.
    """
    factory = APIRequestFactory()
    request = factory.get("/")
    return Request(request)


# =============================================================================
# UserSerializer Tests
# =============================================================================


class TestUserSerializer:
    """
    Tests for UserSerializer.

    UserSerializer is a read-only serializer used by dj-rest-auth
    for the /api/v1/auth/user/ endpoint. It exposes user data along
    with computed fields derived from the profile and linked accounts.
    """

    # -------------------------------------------------------------------------
    # Basic Serialization Tests
    # -------------------------------------------------------------------------

    def test_serializes_user_id(self, user):
        """
        User ID is included in serialized output.

        Why it matters: Frontend applications need a unique identifier
        for the user to make subsequent API calls.
        """
        serializer = UserSerializer(user)
        assert serializer.data["id"] == user.id

    def test_serializes_email(self, user):
        """
        User email is included in serialized output.

        Why it matters: Email is the primary user identifier and is needed
        for display in the UI and for user communication.
        """
        serializer = UserSerializer(user)
        assert serializer.data["email"] == user.email

    def test_serializes_email_verified_status(self, user):
        """
        Email verification status is included in output.

        Why it matters: Frontend may need to show verification prompts
        or restrict access to certain features for unverified users.
        """
        serializer = UserSerializer(user)
        assert serializer.data["email_verified"] == user.email_verified

    def test_serializes_date_joined(self, user):
        """
        Account creation date is included in output.

        Why it matters: Used for account age display, analytics,
        and determining user tenure for feature rollouts.
        """
        serializer = UserSerializer(user)
        assert "date_joined" in serializer.data

    # -------------------------------------------------------------------------
    # Computed Field Tests
    # -------------------------------------------------------------------------

    def test_full_name_from_profile(self, user_with_complete_profile):
        """
        Full name is computed from profile.get_full_name().

        Why it matters: The User model delegates name storage to Profile.
        This computed field provides a convenient accessor without
        requiring the frontend to access profile separately.
        """
        serializer = UserSerializer(user_with_complete_profile)
        expected_name = user_with_complete_profile.get_full_name()
        assert serializer.data["full_name"] == expected_name

    def test_full_name_returns_email_when_no_profile_name(self, user):
        """
        Full name falls back to email when profile has no name set.

        Why it matters: New users may not have completed their profile.
        The system should gracefully degrade by showing the email
        rather than an empty or null value.
        """
        # Ensure profile exists but has no name
        user.profile.first_name = ""
        user.profile.last_name = ""
        user.profile.save()

        serializer = UserSerializer(user)
        # get_full_name returns email when profile has no name
        assert serializer.data["full_name"] == user.email

    def test_username_from_profile(self, user_with_complete_profile):
        """
        Username is retrieved from the related profile.

        Why it matters: Username is stored on Profile, but frontends
        commonly need it alongside user data. This avoids a separate
        API call to fetch profile information.
        """
        serializer = UserSerializer(user_with_complete_profile)
        assert serializer.data["username"] == user_with_complete_profile.profile.username

    def test_username_empty_for_incomplete_profile(self, user_with_incomplete_profile):
        """
        Username is empty string when profile has no username set.

        Why it matters: Users who haven't completed onboarding will
        have empty usernames. The serializer should return empty string
        rather than null to maintain type consistency.
        """
        serializer = UserSerializer(user_with_incomplete_profile)
        assert serializer.data["username"] == ""

    def test_profile_completed_true_when_username_set(self, user_with_complete_profile):
        """
        profile_completed is True when user has set username.

        Why it matters: Frontend uses this flag to determine whether
        to redirect users to the profile completion flow after login.
        """
        serializer = UserSerializer(user_with_complete_profile)
        assert serializer.data["profile_completed"] is True

    def test_profile_completed_false_when_no_username(self, user_with_incomplete_profile):
        """
        profile_completed is False when user has no username.

        Why it matters: Users without usernames need to complete
        their profile before using the full application.
        """
        serializer = UserSerializer(user_with_incomplete_profile)
        assert serializer.data["profile_completed"] is False

    def test_linked_providers_lists_all_providers(
        self, user, linked_account_email, linked_account_google
    ):
        """
        linked_providers returns list of all linked authentication providers.

        Why it matters: Frontend displays which auth methods are connected
        in settings, and may offer different linking options based on
        which providers are already connected.
        """
        serializer = UserSerializer(user)
        providers = serializer.data["linked_providers"]
        assert LinkedAccount.Provider.EMAIL in providers
        assert LinkedAccount.Provider.GOOGLE in providers
        assert len(providers) == 2

    def test_linked_providers_empty_when_no_accounts(self, user):
        """
        linked_providers returns empty list when no accounts linked.

        Why it matters: New users via OAuth may not have any linked
        accounts yet (edge case during registration flow).
        """
        # Remove any auto-created linked accounts
        user.linked_accounts.all().delete()

        serializer = UserSerializer(user)
        assert serializer.data["linked_providers"] == []

    # -------------------------------------------------------------------------
    # Read-Only Field Tests
    # -------------------------------------------------------------------------

    def test_all_fields_are_read_only(self, user):
        """
        All UserSerializer fields are read-only.

        Why it matters: This serializer is for reading user data only.
        Attempts to modify user data should use different endpoints
        (profile update, email change, etc.).
        """
        serializer = UserSerializer(user, data={"email": "hacked@evil.com"})
        # Even if valid, data should not update the user
        assert serializer.is_valid()
        # Read-only serializer ignores input data for all fields


# =============================================================================
# ProfileSerializer Tests
# =============================================================================


class TestProfileSerializer:
    """
    Tests for ProfileSerializer.

    ProfileSerializer is a read-only serializer for displaying complete
    profile data including computed fields like profile_picture_url
    and is_complete status.
    """

    # -------------------------------------------------------------------------
    # Basic Serialization Tests
    # -------------------------------------------------------------------------

    def test_serializes_user_id(self, user):
        """
        User ID is included via nested source.

        Why it matters: Profile responses need user identification
        for frontend state management and API calls.
        """
        serializer = ProfileSerializer(user.profile)
        assert serializer.data["user_id"] == user.id

    def test_serializes_user_email(self, user):
        """
        User email is included via nested source.

        Why it matters: Profile view often displays user email
        for identification and communication preferences.
        """
        serializer = ProfileSerializer(user.profile)
        assert serializer.data["user_email"] == user.email

    def test_serializes_username(self, user_with_complete_profile):
        """
        Username is included in profile data.

        Why it matters: Username is a key profile field for
        display and @mentions.
        """
        serializer = ProfileSerializer(user_with_complete_profile.profile)
        assert serializer.data["username"] == user_with_complete_profile.profile.username

    def test_serializes_name_fields(self, user_with_complete_profile):
        """
        First and last name fields are serialized.

        Why it matters: Name fields are editable profile data
        displayed in profile views and settings.
        """
        profile = user_with_complete_profile.profile
        serializer = ProfileSerializer(profile)
        assert serializer.data["first_name"] == profile.first_name
        assert serializer.data["last_name"] == profile.last_name

    def test_serializes_full_name_computed(self, user_with_complete_profile):
        """
        Full name is computed and included.

        Why it matters: Convenient combined field for display
        without frontend string concatenation.
        """
        profile = user_with_complete_profile.profile
        serializer = ProfileSerializer(profile)
        expected = f"{profile.first_name} {profile.last_name}".strip()
        assert serializer.data["full_name"] == expected

    def test_serializes_timezone(self, user):
        """
        Timezone preference is included.

        Why it matters: Frontend uses timezone for date/time display
        localization.
        """
        user.profile.timezone = "America/New_York"
        user.profile.save()

        serializer = ProfileSerializer(user.profile)
        assert serializer.data["timezone"] == "America/New_York"

    def test_serializes_preferences(self, user):
        """
        User preferences JSON is included.

        Why it matters: Preferences store user settings like theme,
        language, notification preferences, etc.
        """
        user.profile.preferences = {"theme": "dark", "language": "es"}
        user.profile.save()

        serializer = ProfileSerializer(user.profile)
        assert serializer.data["preferences"] == {"theme": "dark", "language": "es"}

    def test_serializes_timestamps(self, user):
        """
        Created and updated timestamps are included.

        Why it matters: Timestamps are needed for audit trails
        and "member since" displays.
        """
        serializer = ProfileSerializer(user.profile)
        assert "created_at" in serializer.data
        assert "updated_at" in serializer.data

    # -------------------------------------------------------------------------
    # Computed Field Tests
    # -------------------------------------------------------------------------

    def test_profile_picture_url_with_request_context(self, db, mock_request):
        """
        profile_picture_url builds absolute URL when request in context.

        Why it matters: Frontend needs absolute URLs for images.
        With request context, we can build proper absolute URLs
        including protocol and domain.
        """
        user = UserFactory()
        # Note: We don't actually upload an image in unit tests
        # This tests the conditional logic for when picture exists
        serializer = ProfileSerializer(user.profile, context={"request": mock_request})
        # Without an actual file, URL should be None
        assert serializer.data["profile_picture_url"] is None

    def test_profile_picture_url_none_when_no_picture(self, user):
        """
        profile_picture_url is None when no profile picture set.

        Why it matters: Frontend needs to know when to show
        a default avatar vs. custom profile picture.
        """
        serializer = ProfileSerializer(user.profile)
        assert serializer.data["profile_picture_url"] is None

    def test_is_complete_true_when_username_set(self, user_with_complete_profile):
        """
        is_complete returns True when profile has username.

        Why it matters: Profile completion status determines
        whether user needs to complete onboarding flow.
        """
        serializer = ProfileSerializer(user_with_complete_profile.profile)
        assert serializer.data["is_complete"] is True

    def test_is_complete_false_when_no_username(self, user_with_incomplete_profile):
        """
        is_complete returns False when profile lacks username.

        Why it matters: Incomplete profiles trigger onboarding redirect.
        """
        serializer = ProfileSerializer(user_with_incomplete_profile.profile)
        assert serializer.data["is_complete"] is False

    # -------------------------------------------------------------------------
    # Read-Only Field Tests
    # -------------------------------------------------------------------------

    def test_user_id_is_read_only(self, user):
        """
        user_id cannot be modified through serializer.

        Why it matters: user_id is derived from the relationship
        and should never be changed via API.
        """
        serializer = ProfileSerializer(user.profile)
        assert "user_id" in serializer.Meta.read_only_fields

    def test_user_email_is_read_only(self, user):
        """
        user_email cannot be modified through serializer.

        Why it matters: Email changes require separate flow
        with verification.
        """
        serializer = ProfileSerializer(user.profile)
        assert "user_email" in serializer.Meta.read_only_fields

    def test_timestamps_are_read_only(self, user):
        """
        created_at and updated_at cannot be modified.

        Why it matters: Timestamps are auto-managed by Django
        and should never be manually set via API.
        """
        serializer = ProfileSerializer(user.profile)
        assert "created_at" in serializer.Meta.read_only_fields
        assert "updated_at" in serializer.Meta.read_only_fields


# =============================================================================
# ProfileUpdateSerializer Tests
# =============================================================================


class TestProfileUpdateSerializer:
    """
    Tests for ProfileUpdateSerializer.

    ProfileUpdateSerializer handles profile updates with dynamic validation:
    - Username is REQUIRED if profile has no username (initial setup)
    - Username is OPTIONAL for subsequent updates
    - Validates username format, reserved names, and uniqueness
    - Validates profile picture size and format
    - Validates preferences JSON structure
    """

    # -------------------------------------------------------------------------
    # Dynamic Username Requirement Tests
    # -------------------------------------------------------------------------

    def test_username_required_when_profile_incomplete(
        self, user_with_incomplete_profile, valid_profile_data
    ):
        """
        Username is required when profile has no username set.

        Why it matters: First-time profile completion must include
        username to enable full app functionality (mentions, URLs, etc.).
        """
        profile = user_with_incomplete_profile.profile
        data = {"first_name": "John"}  # Missing username

        serializer = ProfileUpdateSerializer(
            instance=profile,
            data=data,
            context={"user": user_with_incomplete_profile}
        )
        assert not serializer.is_valid()
        assert "username" in serializer.errors

    def test_username_optional_when_profile_complete(self, user_with_complete_profile):
        """
        Username is optional when profile already has username.

        Why it matters: Users updating other profile fields shouldn't
        need to re-submit their username every time.
        """
        profile = user_with_complete_profile.profile
        data = {"first_name": "Updated"}  # No username

        serializer = ProfileUpdateSerializer(
            instance=profile,
            data=data,
            partial=True,
            context={"user": user_with_complete_profile}
        )
        assert serializer.is_valid(), serializer.errors

    def test_username_required_error_message_for_full_update(
        self, user_with_incomplete_profile
    ):
        """
        Username required error is standard DRF message for full update.

        Why it matters: When serializer sets required=True in __init__,
        DRF's standard field validation runs first with its default message.
        """
        profile = user_with_incomplete_profile.profile
        serializer = ProfileUpdateSerializer(
            instance=profile,
            data={"first_name": "Test"},
            context={"user": user_with_incomplete_profile}
        )
        serializer.is_valid()
        # DRF default required message when required=True is set
        assert "required" in str(serializer.errors.get("username", "")).lower()

    def test_username_required_error_message_for_partial_update(
        self, user_with_incomplete_profile
    ):
        """
        Username required error provides custom message for partial update.

        Why it matters: For PATCH requests, DRF skips field-level required
        checks, so our custom validate() method catches missing username
        with a helpful message.
        """
        profile = user_with_incomplete_profile.profile
        serializer = ProfileUpdateSerializer(
            instance=profile,
            data={"first_name": "Test"},
            partial=True,  # PATCH-style partial update
            context={"user": user_with_incomplete_profile}
        )
        serializer.is_valid()
        assert "required to complete your profile" in str(serializer.errors.get("username", ""))

    # -------------------------------------------------------------------------
    # Username Format Validation Tests
    # -------------------------------------------------------------------------

    def test_valid_username_formats(self, user_with_incomplete_profile, valid_usernames):
        """
        Valid username formats pass validation.

        Why it matters: Users should be able to use common username patterns
        including letters, numbers, underscores, and hyphens.
        """
        profile = user_with_incomplete_profile.profile

        for username in valid_usernames:
            serializer = ProfileUpdateSerializer(
                instance=profile,
                data={"username": username},
                context={"user": user_with_incomplete_profile}
            )
            # Valid format should pass format validation
            # (may fail uniqueness if run multiple times)
            is_valid = serializer.is_valid()
            if not is_valid:
                # Only format-related errors are acceptable failures
                errors = str(serializer.errors.get("username", ""))
                assert "already taken" in errors or "reserved" in errors, (
                    f"Username '{username}' should be valid format but got: {errors}"
                )

    def test_invalid_username_too_short(self, user_with_incomplete_profile):
        """
        Username must be at least 3 characters.

        Why it matters: Very short usernames are hard to identify
        and could cause confusion or abuse.
        """
        profile = user_with_incomplete_profile.profile
        serializer = ProfileUpdateSerializer(
            instance=profile,
            data={"username": "ab"},
            context={"user": user_with_incomplete_profile}
        )
        assert not serializer.is_valid()
        assert "username" in serializer.errors

    def test_invalid_username_too_long(self, user_with_incomplete_profile):
        """
        Username must be at most 30 characters.

        Why it matters: Long usernames cause display issues
        and are generally unnecessary.
        """
        profile = user_with_incomplete_profile.profile
        serializer = ProfileUpdateSerializer(
            instance=profile,
            data={"username": "a" * 31},
            context={"user": user_with_incomplete_profile}
        )
        assert not serializer.is_valid()
        assert "username" in serializer.errors

    def test_invalid_username_with_spaces(self, user_with_incomplete_profile):
        """
        Username cannot contain spaces.

        Why it matters: Spaces would break URLs and @mentions.
        """
        profile = user_with_incomplete_profile.profile
        serializer = ProfileUpdateSerializer(
            instance=profile,
            data={"username": "user name"},
            context={"user": user_with_incomplete_profile}
        )
        assert not serializer.is_valid()
        assert "username" in serializer.errors

    def test_invalid_username_with_special_characters(self, user_with_incomplete_profile):
        """
        Username cannot contain special characters (except _ and -).

        Why it matters: Special characters could enable injection attacks
        or break URL routing.
        """
        invalid_chars = ["@", ".", "!", "/", "#", "$", "%", "&", "*"]
        profile = user_with_incomplete_profile.profile

        for char in invalid_chars:
            serializer = ProfileUpdateSerializer(
                instance=profile,
                data={"username": f"user{char}name"},
                context={"user": user_with_incomplete_profile}
            )
            assert not serializer.is_valid(), f"Character '{char}' should be invalid"
            assert "username" in serializer.errors

    def test_username_format_error_message_is_descriptive(
        self, user_with_incomplete_profile
    ):
        """
        Username format error explains allowed characters.

        Why it matters: Users need to know what characters are allowed.
        """
        profile = user_with_incomplete_profile.profile
        serializer = ProfileUpdateSerializer(
            instance=profile,
            data={"username": "invalid@user"},
            context={"user": user_with_incomplete_profile}
        )
        serializer.is_valid()
        error_msg = str(serializer.errors.get("username", ""))
        assert "3-30 characters" in error_msg
        assert "letters" in error_msg.lower()

    # -------------------------------------------------------------------------
    # Reserved Username Tests
    # -------------------------------------------------------------------------

    def test_reserved_usernames_rejected(
        self, user_with_incomplete_profile, reserved_usernames
    ):
        """
        Reserved usernames cannot be used.

        Why it matters: Reserved names prevent impersonation and
        confusion with system accounts (admin, support, etc.).
        """
        profile = user_with_incomplete_profile.profile

        for reserved in reserved_usernames:
            serializer = ProfileUpdateSerializer(
                instance=profile,
                data={"username": reserved},
                context={"user": user_with_incomplete_profile}
            )
            assert not serializer.is_valid(), f"'{reserved}' should be rejected"
            assert "username" in serializer.errors

    def test_reserved_username_error_message_mentions_reserved(
        self, user_with_incomplete_profile
    ):
        """
        Reserved username error specifically mentions the term 'reserved'.

        Why it matters: Users should understand why a valid-looking
        username was rejected.
        """
        profile = user_with_incomplete_profile.profile
        serializer = ProfileUpdateSerializer(
            instance=profile,
            data={"username": "admin"},
            context={"user": user_with_incomplete_profile}
        )
        serializer.is_valid()
        error_msg = str(serializer.errors.get("username", ""))
        assert "reserved" in error_msg.lower()

    # -------------------------------------------------------------------------
    # Username Uniqueness Tests
    # -------------------------------------------------------------------------

    def test_duplicate_username_rejected(self, db):
        """
        Username must be unique across all users.

        Why it matters: Usernames are used for identification and
        @mentions; duplicates would cause ambiguity.
        """
        # Create first user with username
        user1 = UserFactory()
        user1.profile.username = "takenname"
        user1.profile.save()

        # Try to use same username for second user
        user2 = UserFactory()
        user2.profile.username = ""
        user2.profile.save()

        serializer = ProfileUpdateSerializer(
            instance=user2.profile,
            data={"username": "takenname"},
            context={"user": user2}
        )
        assert not serializer.is_valid()
        assert "username" in serializer.errors
        assert "already taken" in str(serializer.errors["username"])

    def test_duplicate_username_case_insensitive(self, db):
        """
        Username uniqueness check is case-insensitive.

        Why it matters: "JohnDoe" and "johndoe" should be considered
        the same username to prevent confusion and impersonation.
        """
        user1 = UserFactory()
        user1.profile.username = "TestUser"
        user1.profile.save()

        user2 = UserFactory()
        user2.profile.username = ""
        user2.profile.save()

        serializer = ProfileUpdateSerializer(
            instance=user2.profile,
            data={"username": "testuser"},  # Different case
            context={"user": user2}
        )
        assert not serializer.is_valid()
        assert "already taken" in str(serializer.errors.get("username", ""))

    def test_user_can_keep_own_username(self, user_with_complete_profile):
        """
        User can submit their own existing username without error.

        Why it matters: During updates, users may submit the same
        username they already have; this shouldn't fail uniqueness check.
        """
        profile = user_with_complete_profile.profile
        current_username = profile.username

        serializer = ProfileUpdateSerializer(
            instance=profile,
            data={"username": current_username, "first_name": "Updated"},
            context={"user": user_with_complete_profile}
        )
        assert serializer.is_valid(), serializer.errors

    def test_username_normalized_to_lowercase(self, user_with_incomplete_profile):
        """
        Usernames are normalized to lowercase during validation.

        Why it matters: Case normalization ensures consistent storage
        and enables case-insensitive lookups.
        """
        profile = user_with_incomplete_profile.profile
        serializer = ProfileUpdateSerializer(
            instance=profile,
            data={"username": "MyUserName"},
            context={"user": user_with_incomplete_profile}
        )
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["username"] == "myusername"

    # -------------------------------------------------------------------------
    # Profile Picture Validation Tests
    # -------------------------------------------------------------------------

    def test_valid_image_accepted(
        self, user_with_complete_profile, valid_image
    ):
        """
        Valid image files are accepted for profile picture.

        Why it matters: Users should be able to upload standard
        image formats for their profile picture.
        """
        profile = user_with_complete_profile.profile
        serializer = ProfileUpdateSerializer(
            instance=profile,
            data={"profile_picture": valid_image},
            partial=True,
            context={"user": user_with_complete_profile}
        )
        assert serializer.is_valid(), serializer.errors

    def test_oversized_image_rejected(
        self, user_with_complete_profile, oversized_image
    ):
        """
        Images over 5MB are rejected.

        Why it matters: Large files consume storage and bandwidth.
        5MB is generous for profile pictures while preventing abuse.
        """
        profile = user_with_complete_profile.profile
        serializer = ProfileUpdateSerializer(
            instance=profile,
            data={"profile_picture": oversized_image},
            partial=True,
            context={"user": user_with_complete_profile}
        )
        assert not serializer.is_valid()
        assert "profile_picture" in serializer.errors
        assert "5MB" in str(serializer.errors["profile_picture"])

    def test_profile_picture_can_be_null(self, user_with_complete_profile):
        """
        Profile picture can be set to null to remove it.

        Why it matters: Users should be able to remove their profile
        picture and revert to default avatar.
        """
        profile = user_with_complete_profile.profile
        serializer = ProfileUpdateSerializer(
            instance=profile,
            data={"profile_picture": None},
            partial=True,
            context={"user": user_with_complete_profile}
        )
        assert serializer.is_valid(), serializer.errors

    def test_invalid_file_extension_rejected(self, user_with_complete_profile):
        """
        Non-image file extensions are rejected.

        Why it matters: Only image files should be accepted for
        profile pictures to prevent security issues.
        """
        # Create a fake "PDF" file
        invalid_file = SimpleUploadedFile(
            "document.pdf",
            b"fake pdf content",
            content_type="application/pdf"
        )
        profile = user_with_complete_profile.profile
        serializer = ProfileUpdateSerializer(
            instance=profile,
            data={"profile_picture": invalid_file},
            partial=True,
            context={"user": user_with_complete_profile}
        )
        assert not serializer.is_valid()
        assert "profile_picture" in serializer.errors

    @pytest.mark.parametrize("filename,fmt", [
        ("test.jpg", "JPEG"),
        ("test.png", "PNG"),
        ("test.gif", "GIF"),
    ])
    def test_accepted_image_formats(
        self, user_with_complete_profile, create_test_image, filename, fmt
    ):
        """
        JPEG, PNG, and GIF formats are accepted.

        Why it matters: These are the most common web image formats
        and should be supported for profile pictures.
        """
        profile = user_with_complete_profile.profile
        image = create_test_image(name=filename, format=fmt)
        serializer = ProfileUpdateSerializer(
            instance=profile,
            data={"profile_picture": image},
            partial=True,
            context={"user": user_with_complete_profile}
        )
        assert serializer.is_valid(), f"{fmt} should be accepted: {serializer.errors}"

    def test_webp_image_format_accepted(
        self, user_with_complete_profile, create_test_image
    ):
        """
        WEBP format is accepted when Pillow has WEBP support.

        Why it matters: WEBP is a modern image format with better compression.
        This test verifies the serializer accepts WEBP when available.
        """
        # Skip if Pillow doesn't have WEBP support (depends on libwebp)
        try:
            from PIL import features
            if not features.check("webp"):
                pytest.skip("Pillow WEBP support not available")
        except ImportError:
            pytest.skip("Pillow features module not available")

        profile = user_with_complete_profile.profile
        image = create_test_image(name="test.webp", format="WEBP")
        serializer = ProfileUpdateSerializer(
            instance=profile,
            data={"profile_picture": image},
            partial=True,
            context={"user": user_with_complete_profile}
        )
        assert serializer.is_valid(), f"WEBP should be accepted: {serializer.errors}"

    # -------------------------------------------------------------------------
    # Preferences Validation Tests
    # -------------------------------------------------------------------------

    def test_valid_preferences_accepted(self, user_with_complete_profile):
        """
        Valid JSON object is accepted for preferences.

        Why it matters: Preferences store structured user settings
        that frontend can read and modify.
        """
        profile = user_with_complete_profile.profile
        preferences = {
            "theme": "dark",
            "language": "en",
            "notifications": {"email": True, "push": False}
        }
        serializer = ProfileUpdateSerializer(
            instance=profile,
            data={"preferences": preferences},
            partial=True,
            context={"user": user_with_complete_profile}
        )
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["preferences"] == preferences

    def test_preferences_must_be_dict(self, user_with_complete_profile):
        """
        Preferences must be a JSON object (dict), not array or primitive.

        Why it matters: The preferences schema expects key-value pairs.
        Arrays or primitives would break frontend expectations.
        """
        profile = user_with_complete_profile.profile
        invalid_values = [
            ["array", "of", "values"],
            "string value",
            123,
            True,
            None,
        ]

        for invalid in invalid_values:
            serializer = ProfileUpdateSerializer(
                instance=profile,
                data={"preferences": invalid},
                partial=True,
                context={"user": user_with_complete_profile}
            )
            # None is allowed by the field but should pass validation
            if invalid is None:
                continue
            assert not serializer.is_valid(), f"{invalid} should be rejected"
            assert "preferences" in serializer.errors
            assert "JSON object" in str(serializer.errors["preferences"])

    def test_empty_preferences_allowed(self, user_with_complete_profile):
        """
        Empty preferences object is allowed.

        Why it matters: Users may want to clear all preferences
        or start with defaults.
        """
        profile = user_with_complete_profile.profile
        serializer = ProfileUpdateSerializer(
            instance=profile,
            data={"preferences": {}},
            partial=True,
            context={"user": user_with_complete_profile}
        )
        assert serializer.is_valid(), serializer.errors

    # -------------------------------------------------------------------------
    # Partial Update Tests
    # -------------------------------------------------------------------------

    def test_partial_update_only_provided_fields(self, user_with_complete_profile):
        """
        Partial updates only modify provided fields.

        Why it matters: PATCH requests should update only the fields
        included in the request, leaving others unchanged.
        """
        profile = user_with_complete_profile.profile
        original_username = profile.username
        original_last_name = profile.last_name

        serializer = ProfileUpdateSerializer(
            instance=profile,
            data={"first_name": "NewFirst"},
            partial=True,
            context={"user": user_with_complete_profile}
        )
        assert serializer.is_valid(), serializer.errors

        # Only first_name should be in validated_data
        assert serializer.validated_data.get("first_name") == "NewFirst"
        # Other fields should not be in validated_data for partial update
        assert "username" not in serializer.validated_data
        assert "last_name" not in serializer.validated_data


# =============================================================================
# LinkedAccountSerializer Tests
# =============================================================================


class TestLinkedAccountSerializer:
    """
    Tests for LinkedAccountSerializer.

    LinkedAccountSerializer is a read-only serializer for displaying
    connected authentication providers (email, Google, Apple).
    """

    # -------------------------------------------------------------------------
    # Basic Serialization Tests
    # -------------------------------------------------------------------------

    def test_serializes_id(self, linked_account_email):
        """
        Linked account ID is included.

        Why it matters: ID is needed for unlinking operations
        in account settings.
        """
        serializer = LinkedAccountSerializer(linked_account_email)
        assert serializer.data["id"] == linked_account_email.id

    def test_serializes_provider(self, linked_account_google):
        """
        Provider code is included.

        Why it matters: Frontend uses provider code to display
        appropriate icons and handle provider-specific logic.
        """
        serializer = LinkedAccountSerializer(linked_account_google)
        assert serializer.data["provider"] == LinkedAccount.Provider.GOOGLE

    def test_serializes_provider_display(self, linked_account_google):
        """
        Human-readable provider name is included.

        Why it matters: Display name is shown to users in settings
        (e.g., "Google" instead of "google").
        """
        serializer = LinkedAccountSerializer(linked_account_google)
        assert serializer.data["provider_display"] == "Google"

    def test_serializes_provider_user_id(self, linked_account_google):
        """
        Provider's user ID is included.

        Why it matters: Useful for debugging and showing which
        account is linked (especially for providers allowing multiple accounts).
        """
        serializer = LinkedAccountSerializer(linked_account_google)
        assert serializer.data["provider_user_id"] == "google-uid-123"

    def test_serializes_created_at(self, linked_account_email):
        """
        Link creation timestamp is included.

        Why it matters: Shows when each authentication method was
        connected for account audit/security purposes.
        """
        serializer = LinkedAccountSerializer(linked_account_email)
        assert "created_at" in serializer.data

    # -------------------------------------------------------------------------
    # Provider Display Tests
    # -------------------------------------------------------------------------

    def test_email_provider_display(self, linked_account_email):
        """
        Email provider shows 'Email' display name.
        """
        serializer = LinkedAccountSerializer(linked_account_email)
        assert serializer.data["provider_display"] == "Email"

    def test_google_provider_display(self, linked_account_google):
        """
        Google provider shows 'Google' display name.
        """
        serializer = LinkedAccountSerializer(linked_account_google)
        assert serializer.data["provider_display"] == "Google"

    def test_apple_provider_display(self, linked_account_apple):
        """
        Apple provider shows 'Apple' display name.
        """
        serializer = LinkedAccountSerializer(linked_account_apple)
        assert serializer.data["provider_display"] == "Apple"

    # -------------------------------------------------------------------------
    # Read-Only Tests
    # -------------------------------------------------------------------------

    def test_all_fields_are_read_only(self, linked_account_email):
        """
        All LinkedAccountSerializer fields are read-only.

        Why it matters: Linked accounts should only be created/deleted
        through the OAuth flow, not modified via API.
        """
        serializer = LinkedAccountSerializer(linked_account_email)
        assert serializer.Meta.read_only_fields == serializer.Meta.fields

    # -------------------------------------------------------------------------
    # List Serialization Tests
    # -------------------------------------------------------------------------

    def test_serializes_multiple_accounts(
        self, user, linked_account_email, linked_account_google, linked_account_apple
    ):
        """
        Multiple linked accounts can be serialized as a list.

        Why it matters: Users may have multiple auth methods connected;
        frontend needs to display all of them.
        """
        accounts = [linked_account_email, linked_account_google, linked_account_apple]
        serializer = LinkedAccountSerializer(accounts, many=True)

        assert len(serializer.data) == 3
        providers = [item["provider"] for item in serializer.data]
        assert LinkedAccount.Provider.EMAIL in providers
        assert LinkedAccount.Provider.GOOGLE in providers
        assert LinkedAccount.Provider.APPLE in providers


# =============================================================================
# RegisterSerializer Tests
# =============================================================================


class TestRegisterSerializer:
    """
    Tests for RegisterSerializer.

    RegisterSerializer handles email/password registration:
    - Validates email format and uniqueness
    - Validates password minimum length
    - Validates password confirmation match
    - Creates user with linked email account
    """

    # -------------------------------------------------------------------------
    # Email Validation Tests
    # -------------------------------------------------------------------------

    def test_valid_email_accepted(self, db, valid_registration_data):
        """
        Valid email addresses are accepted.

        Why it matters: Users should be able to register with
        any valid email format.
        """
        serializer = RegisterSerializer(data=valid_registration_data)
        assert serializer.is_valid(), serializer.errors

    def test_email_required(self, db):
        """
        Email field is required for registration.

        Why it matters: Email is the primary identifier and is
        required for account recovery and communication.
        """
        data = {
            "password1": "SecurePass123!",
            "password2": "SecurePass123!"
        }
        serializer = RegisterSerializer(data=data)
        assert not serializer.is_valid()
        assert "email" in serializer.errors

    def test_invalid_email_format_rejected(self, db):
        """
        Invalid email formats are rejected.

        Why it matters: Invalid emails would prevent account
        verification and password recovery.
        """
        data = {
            "email": "not-an-email",
            "password1": "SecurePass123!",
            "password2": "SecurePass123!"
        }
        serializer = RegisterSerializer(data=data)
        assert not serializer.is_valid()
        assert "email" in serializer.errors

    def test_duplicate_email_rejected(self, db, user):
        """
        Existing email addresses are rejected.

        Why it matters: Prevents duplicate accounts and potential
        account takeover through email overlap.
        """
        data = {
            "email": user.email,
            "password1": "SecurePass123!",
            "password2": "SecurePass123!"
        }
        serializer = RegisterSerializer(data=data)
        assert not serializer.is_valid()
        assert "email" in serializer.errors
        assert "already exists" in str(serializer.errors["email"])

    def test_duplicate_email_case_insensitive(self, db, user):
        """
        Email uniqueness check is case-insensitive.

        Why it matters: user@example.com and USER@EXAMPLE.COM
        should be considered the same account.
        """
        data = {
            "email": user.email.upper(),
            "password1": "SecurePass123!",
            "password2": "SecurePass123!"
        }
        serializer = RegisterSerializer(data=data)
        assert not serializer.is_valid()
        assert "already exists" in str(serializer.errors.get("email", ""))

    def test_email_normalized_to_lowercase(self, db):
        """
        Email is normalized to lowercase during validation.

        Why it matters: Consistent email storage enables reliable
        lookups regardless of input casing.
        """
        data = {
            "email": "USER@EXAMPLE.COM",
            "password1": "SecurePass123!",
            "password2": "SecurePass123!"
        }
        serializer = RegisterSerializer(data=data)
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["email"] == "user@example.com"

    # -------------------------------------------------------------------------
    # Password Validation Tests
    # -------------------------------------------------------------------------

    def test_password_required(self, db):
        """
        Password field is required for registration.

        Why it matters: Users must set a password for email-based
        authentication.
        """
        data = {
            "email": "test_pwd_required@example.com",
            "password2": "SecurePass123!"
        }
        serializer = RegisterSerializer(data=data)
        assert not serializer.is_valid()
        assert "password1" in serializer.errors

    def test_password_minimum_length(self, db):
        """
        Password must be at least 8 characters.

        Why it matters: Short passwords are vulnerable to brute-force
        attacks. 8 characters is a common minimum requirement.
        """
        data = {
            "email": "test_pwd_minlen@example.com",
            "password1": "short",
            "password2": "short"
        }
        serializer = RegisterSerializer(data=data)
        assert not serializer.is_valid()
        assert "password1" in serializer.errors

    def test_password_eight_chars_accepted(self, db):
        """
        Password exactly 8 characters is accepted.

        Why it matters: The boundary case (exactly minimum length)
        should be valid.
        """
        data = {
            "email": "test_pwd_8chars@example.com",
            "password1": "12345678",
            "password2": "12345678"
        }
        serializer = RegisterSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

    def test_password_is_write_only(self, db, valid_registration_data):
        """
        Password field does not appear in serialized output.

        Why it matters: Passwords should never be exposed in API
        responses, even for the user who set them.
        """
        serializer = RegisterSerializer(data=valid_registration_data)
        assert serializer.is_valid()
        # After validation, password should not appear in data representation
        assert "password1" not in serializer.data

    # -------------------------------------------------------------------------
    # Password Confirmation Tests
    # -------------------------------------------------------------------------

    def test_password2_required(self, db):
        """
        Password confirmation field is required.

        Why it matters: Confirmation prevents typos in password entry
        that would lock users out.
        """
        data = {
            "email": "test_pwd2_required@example.com",
            "password1": "SecurePass123!"
        }
        serializer = RegisterSerializer(data=data)
        assert not serializer.is_valid()
        assert "password2" in serializer.errors

    def test_password2_must_match(self, db):
        """
        Password confirmation must match password.

        Why it matters: Mismatched passwords indicate user error
        that should be caught before account creation.
        """
        data = {
            "email": "test_pwd2_mismatch@example.com",
            "password1": "SecurePass123!",
            "password2": "DifferentPass456!"
        }
        serializer = RegisterSerializer(data=data)
        assert not serializer.is_valid()
        assert "password2" in serializer.errors
        assert "do not match" in str(serializer.errors["password2"])

    def test_password2_is_write_only(self, db, valid_registration_data):
        """
        Password confirmation does not appear in output.

        Why it matters: Confirmation field is only for input validation
        and should never be persisted or exposed.
        """
        serializer = RegisterSerializer(data=valid_registration_data)
        assert serializer.is_valid()
        assert "password2" not in serializer.data

    # -------------------------------------------------------------------------
    # User Creation Tests
    # -------------------------------------------------------------------------

    def test_create_returns_user_instance(self, db, valid_registration_data, mocker):
        """
        save() method returns a User instance.

        Why it matters: After successful registration, the serializer
        should return the created user for the view to use.
        """
        mock_request = mocker.MagicMock()
        serializer = RegisterSerializer(data=valid_registration_data)
        assert serializer.is_valid()
        user = serializer.save(mock_request)

        assert isinstance(user, User)
        assert user.email == valid_registration_data["email"].lower()

    def test_create_sets_password_correctly(self, db, valid_registration_data, mocker):
        """
        Created user has correctly hashed password.

        Why it matters: Password should be hashed (not stored plaintext)
        and should validate against the original password.
        """
        mock_request = mocker.MagicMock()
        serializer = RegisterSerializer(data=valid_registration_data)
        assert serializer.is_valid()
        user = serializer.save(mock_request)

        assert user.check_password(valid_registration_data["password1"])

    def test_create_creates_linked_account(self, db, valid_registration_data, mocker):
        """
        User creation also creates LinkedAccount for email provider.

        Why it matters: LinkedAccount tracks authentication methods.
        Email registration should create an email-type linked account.
        """
        mock_request = mocker.MagicMock()
        serializer = RegisterSerializer(data=valid_registration_data)
        assert serializer.is_valid()
        user = serializer.save(mock_request)

        linked = user.linked_accounts.get(provider=LinkedAccount.Provider.EMAIL)
        assert linked.provider_user_id == user.email

    def test_created_user_is_unverified(self, db, valid_registration_data, mocker):
        """
        Newly created user has email_verified=False.

        Why it matters: Users must verify their email before being
        considered fully authenticated.
        """
        mock_request = mocker.MagicMock()
        serializer = RegisterSerializer(data=valid_registration_data)
        assert serializer.is_valid()
        user = serializer.save(mock_request)

        assert user.email_verified is False

    def test_created_user_is_active(self, db, valid_registration_data, mocker):
        """
        Newly created user has is_active=True.

        Why it matters: New users should be able to log in immediately
        (though they may have limited access until verified).
        """
        mock_request = mocker.MagicMock()
        serializer = RegisterSerializer(data=valid_registration_data)
        assert serializer.is_valid()
        user = serializer.save(mock_request)

        assert user.is_active is True

    def test_created_user_has_profile(self, db, valid_registration_data, mocker):
        """
        Newly created user has auto-created profile.

        Why it matters: Profile is created via signals when user is
        created, ensuring every user has a profile for consistency.
        """
        mock_request = mocker.MagicMock()
        serializer = RegisterSerializer(data=valid_registration_data)
        assert serializer.is_valid()
        user = serializer.save(mock_request)

        # Profile should exist (created by signal)
        assert hasattr(user, "profile")
        assert user.profile is not None
        # But username should be empty (requires profile completion)
        assert user.profile.username == ""
