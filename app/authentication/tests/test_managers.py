"""
Tests for UserManager.

This module tests the custom UserManager that handles email-based user creation.
The UserManager provides:
- create_user(): Creates regular users with optional password (for OAuth flows)
- create_superuser(): Creates admin users with elevated privileges

Test organization follows the Given-When-Then pattern and tests:
1. Happy paths: Expected successful operations
2. Error cases: Invalid inputs and edge cases
3. Behavior verification: Password hashing, email normalization, default flags

Related files:
    - managers.py: Implementation under test
    - models.py: User model that uses this manager
    - factories.py: Test data factories (not used here - we test the manager directly)
"""

import pytest
from django.contrib.auth.hashers import check_password

from authentication.models import User


class TestUserManagerCreateUser:
    """Tests for UserManager.create_user() method."""

    def test_creates_user_with_email_and_password(self, db):
        """
        Given valid email and password
        When create_user is called
        Then a user is created with those credentials
        """
        # Arrange
        email = "mgr_create_user@example.com"
        password = "SecurePass123!"

        # Act
        user = User.objects.create_user(email=email, password=password)

        # Assert: User exists and has correct email
        assert user.pk is not None
        assert user.email == email
        # Assert: User can authenticate with the password
        assert user.check_password(password) is True

    def test_normalizes_email_domain_to_lowercase(self, db):
        """
        Given an email with uppercase characters in domain
        When create_user is called
        Then the domain portion is normalized to lowercase

        Note: Email local part (before @) case is preserved per RFC 5321,
        but the domain must be lowercase for consistency.
        """
        # Arrange
        email = "Test.User@EXAMPLE.COM"

        # Act
        user = User.objects.create_user(email=email, password="TestPass123!")

        # Assert: Domain is lowercase, local part case is preserved
        assert user.email == "Test.User@example.com"

    def test_raises_valueerror_when_email_is_empty(self, db):
        """
        Given an empty email
        When create_user is called
        Then a ValueError is raised with descriptive message
        """
        # Act & Assert
        with pytest.raises(ValueError) as exc_info:
            User.objects.create_user(email="", password="TestPass123!")

        assert "Email field must be set" in str(exc_info.value)

    def test_raises_valueerror_when_email_is_none(self, db):
        """
        Given None as email
        When create_user is called
        Then a ValueError is raised

        This ensures the manager validates email presence explicitly.
        """
        # Act & Assert
        with pytest.raises(ValueError) as exc_info:
            User.objects.create_user(email=None, password="TestPass123!")

        assert "Email field must be set" in str(exc_info.value)

    def test_creates_user_without_password_for_oauth(self, db):
        """
        Given an email but no password
        When create_user is called
        Then user is created with unusable password

        OAuth users authenticate via external providers and don't need
        a password stored in our system.
        """
        # Arrange
        email = "oauth.user@example.com"

        # Act
        user = User.objects.create_user(email=email, password=None)

        # Assert: User exists but has no usable password
        assert user.pk is not None
        assert user.has_usable_password() is False
        # Assert: Cannot authenticate with any password
        assert user.check_password("any_password") is False
        assert user.check_password("") is False

    def test_ignores_profile_fields_first_name(self, db):
        """
        Given first_name passed to create_user
        When create_user is called
        Then first_name is ignored (belongs on Profile model, not User)

        The User model is slim and focused on auth. Profile data
        lives on the separate Profile model created via signals.
        """
        # Arrange
        email = "mgr_ignore_firstname@example.com"

        # Act: Pass first_name which should be ignored
        user = User.objects.create_user(
            email=email,
            password="TestPass123!",
            first_name="IgnoredFirstName"
        )

        # Assert: User exists (no error thrown)
        assert user.pk is not None
        # Assert: first_name is not on User model
        assert not hasattr(user, "first_name") or getattr(user, "first_name", None) is None

    def test_ignores_profile_fields_last_name(self, db):
        """
        Given last_name passed to create_user
        When create_user is called
        Then last_name is ignored (belongs on Profile model, not User)
        """
        # Arrange
        email = "mgr_ignore_lastname@example.com"

        # Act: Pass last_name which should be ignored
        user = User.objects.create_user(
            email=email,
            password="TestPass123!",
            last_name="IgnoredLastName"
        )

        # Assert: User exists (no error thrown)
        assert user.pk is not None
        # Assert: last_name is not on User model
        assert not hasattr(user, "last_name") or getattr(user, "last_name", None) is None

    def test_sets_default_flags_for_regular_user(self, db):
        """
        Given no explicit flags
        When create_user is called
        Then is_staff and is_superuser default to False

        Regular users should not have admin privileges by default.
        """
        # Arrange
        email = "regular@example.com"

        # Act
        user = User.objects.create_user(email=email, password="TestPass123!")

        # Assert: Security-critical defaults
        assert user.is_staff is False
        assert user.is_superuser is False
        assert user.is_active is True  # Users should be active by default

    def test_password_is_properly_hashed(self, db):
        """
        Given a plaintext password
        When create_user is called
        Then the password is stored as a hash, not plaintext

        This verifies Django's set_password() is being used correctly.
        """
        # Arrange
        email = "mgr_password_hash@example.com"
        plaintext_password = "SecurePass123!"

        # Act
        user = User.objects.create_user(email=email, password=plaintext_password)

        # Assert: Password is not stored in plaintext
        assert user.password != plaintext_password
        # Assert: Password hash contains algorithm identifier (format: algorithm$...)
        # Note: Tests may use MD5 hasher for speed, production uses PBKDF2/Argon2
        assert "$" in user.password, "Password should be in Django hash format"
        # Assert: Django's check_password verifies the hash
        assert check_password(plaintext_password, user.password) is True

    def test_can_override_is_active_flag(self, db):
        """
        Given is_active=False in extra_fields
        When create_user is called
        Then user is created as inactive

        This allows creating pre-verified users or suspended accounts.
        """
        # Arrange
        email = "inactive@example.com"

        # Act
        user = User.objects.create_user(
            email=email,
            password="TestPass123!",
            is_active=False
        )

        # Assert
        assert user.is_active is False

    def test_can_set_email_verified_flag(self, db):
        """
        Given email_verified=True in extra_fields
        When create_user is called
        Then user is created with verified email

        This is useful for OAuth users where email is pre-verified.
        """
        # Arrange
        email = "verified@example.com"

        # Act
        user = User.objects.create_user(
            email=email,
            password="TestPass123!",
            email_verified=True
        )

        # Assert
        assert user.email_verified is True


class TestUserManagerCreateSuperuser:
    """Tests for UserManager.create_superuser() method."""

    def test_creates_superuser_with_correct_flags(self, db):
        """
        Given valid email and password
        When create_superuser is called
        Then user is created with is_staff=True and is_superuser=True
        """
        # Arrange
        email = "admin@example.com"
        password = "AdminPass123!"

        # Act
        superuser = User.objects.create_superuser(email=email, password=password)

        # Assert: Superuser has elevated privileges
        assert superuser.pk is not None
        assert superuser.is_staff is True
        assert superuser.is_superuser is True
        assert superuser.email == email
        assert superuser.check_password(password) is True

    def test_superuser_has_email_verified_true(self, db):
        """
        Given no explicit email_verified flag
        When create_superuser is called
        Then email_verified defaults to True

        Superusers created via command line or programmatically
        should be able to access the system immediately.
        """
        # Arrange
        email = "admin@example.com"
        password = "AdminPass123!"

        # Act
        superuser = User.objects.create_superuser(email=email, password=password)

        # Assert: Email is automatically verified for superusers
        assert superuser.email_verified is True

    def test_raises_valueerror_when_is_staff_is_false(self, db):
        """
        Given is_staff=False explicitly passed
        When create_superuser is called
        Then ValueError is raised

        A superuser must have staff privileges to access admin.
        """
        # Arrange
        email = "admin@example.com"
        password = "AdminPass123!"

        # Act & Assert
        with pytest.raises(ValueError) as exc_info:
            User.objects.create_superuser(
                email=email,
                password=password,
                is_staff=False
            )

        assert "is_staff=True" in str(exc_info.value)

    def test_raises_valueerror_when_is_superuser_is_false(self, db):
        """
        Given is_superuser=False explicitly passed
        When create_superuser is called
        Then ValueError is raised

        The create_superuser method must create actual superusers.
        """
        # Arrange
        email = "admin@example.com"
        password = "AdminPass123!"

        # Act & Assert
        with pytest.raises(ValueError) as exc_info:
            User.objects.create_superuser(
                email=email,
                password=password,
                is_superuser=False
            )

        assert "is_superuser=True" in str(exc_info.value)

    def test_superuser_without_password_sets_unusable_password(self, db):
        """
        Given no password (password=None)
        When create_superuser is called
        Then superuser is created with unusable password

        Note: This is permitted by the implementation but typically
        superusers should have passwords for admin access. The manager
        delegates to create_user which handles None passwords.
        """
        # Arrange
        email = "admin@example.com"

        # Act
        superuser = User.objects.create_superuser(email=email, password=None)

        # Assert: Superuser exists but has no usable password
        assert superuser.pk is not None
        assert superuser.is_superuser is True
        assert superuser.has_usable_password() is False

    def test_normalizes_superuser_email(self, db):
        """
        Given email with uppercase domain
        When create_superuser is called
        Then email domain is normalized to lowercase

        Superuser creation should use the same normalization as regular users.
        """
        # Arrange
        email = "Admin@EXAMPLE.COM"
        password = "AdminPass123!"

        # Act
        superuser = User.objects.create_superuser(email=email, password=password)

        # Assert: Domain is lowercase
        assert superuser.email == "Admin@example.com"

    def test_superuser_without_email_raises_valueerror(self, db):
        """
        Given empty email
        When create_superuser is called
        Then ValueError is raised

        Superusers also require valid email addresses.
        """
        # Act & Assert
        with pytest.raises(ValueError) as exc_info:
            User.objects.create_superuser(email="", password="AdminPass123!")

        assert "Email field must be set" in str(exc_info.value)


class TestUserManagerEdgeCases:
    """Edge case tests for UserManager methods."""

    def test_creates_multiple_users_with_unique_emails(self, db):
        """
        Given multiple unique emails
        When create_user is called for each
        Then all users are created successfully

        Verifies the manager doesn't have state issues between calls.
        """
        # Arrange
        emails = [
            "user1@example.com",
            "user2@example.com",
            "user3@example.com",
        ]

        # Act
        users = [
            User.objects.create_user(email=email, password="TestPass123!")
            for email in emails
        ]

        # Assert: All users created with unique PKs
        assert len(users) == 3
        assert len(set(u.pk for u in users)) == 3  # All PKs unique
        assert [u.email for u in users] == emails

    def test_whitespace_only_email_is_accepted_by_manager(self, db):
        """
        Given email containing only whitespace
        When create_user is called
        Then user is created (manager doesn't validate email format)

        Note: The UserManager checks for truthiness (not empty string),
        but whitespace-only strings are truthy in Python. Email format
        validation happens at the model/serializer layer, not the manager.
        This test documents actual behavior.
        """
        # Arrange
        whitespace_email = "   "

        # Act: Manager accepts whitespace (format validation happens elsewhere)
        user = User.objects.create_user(email=whitespace_email, password="TestPass123!")

        # Assert: User is created - format validation is not manager's responsibility
        assert user.pk is not None
        # The email is stored as-is (whitespace)
        assert user.email == whitespace_email

    def test_email_with_plus_addressing_is_preserved(self, db):
        """
        Given email with plus addressing (user+tag@domain.com)
        When create_user is called
        Then the plus addressing is preserved

        Plus addressing is valid and used for email filtering.
        """
        # Arrange
        email = "user+newsletter@example.com"

        # Act
        user = User.objects.create_user(email=email, password="TestPass123!")

        # Assert: Plus addressing preserved
        assert user.email == email

    def test_email_with_subdomain_is_preserved(self, db):
        """
        Given email with subdomain (user@mail.example.com)
        When create_user is called
        Then subdomain is preserved and lowercase

        Subdomains are common in corporate environments.
        """
        # Arrange
        email = "user@MAIL.EXAMPLE.COM"

        # Act
        user = User.objects.create_user(email=email, password="TestPass123!")

        # Assert: Full domain including subdomain is lowercase
        assert user.email == "user@mail.example.com"

    def test_extra_fields_are_passed_to_model(self, db):
        """
        Given valid extra_fields
        When create_user is called
        Then extra fields are set on the user model

        This verifies **extra_fields passthrough works correctly.
        """
        # Arrange
        email = "mgr_extra_fields@example.com"

        # Act
        user = User.objects.create_user(
            email=email,
            password="TestPass123!",
            email_verified=True,
            is_active=False
        )

        # Assert: Extra fields are set
        assert user.email_verified is True
        assert user.is_active is False
