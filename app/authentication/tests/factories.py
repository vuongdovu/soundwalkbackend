"""
Factory Boy factories for authentication models.

Provides realistic test data generation for:
- User: Custom user model with email-based authentication
- Profile: User profile with username validation
- LinkedAccount: OAuth provider connections
- EmailVerificationToken: Verification and password reset tokens

Usage:
    from authentication.tests.factories import UserFactory, ProfileFactory

    # Create a user with default values
    user = UserFactory()

    # Create a verified user
    user = UserFactory(email_verified=True)

    # Create a user with a complete profile
    user = UserFactory(email_verified=True)
    ProfileFactory(user=user, username="testuser")
"""

import secrets
from datetime import timedelta

import factory
from django.utils import timezone

from authentication.models import (
    User,
    Profile,
    LinkedAccount,
    EmailVerificationToken,
)


class UserFactory(factory.django.DjangoModelFactory):
    """
    Factory for User model.

    Creates users with email-based authentication.
    By default, users are unverified and active.

    Examples:
        # Basic user
        user = UserFactory()

        # Verified user
        user = UserFactory(email_verified=True)

        # Staff user
        user = UserFactory(is_staff=True)

        # Inactive user (deactivated)
        user = UserFactory(is_active=False)
    """

    class Meta:
        model = User
        skip_postgeneration_save = True

    email = factory.Sequence(lambda n: f"user{n}@example.com")
    email_verified = False
    is_active = True
    is_staff = False

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        """Override create to use UserManager.create_user()."""
        password = kwargs.pop("password", "TestPass123!")
        return model_class.objects.create_user(
            email=kwargs.pop("email"), password=password, **kwargs
        )


class ProfileFactory(factory.django.DjangoModelFactory):
    """
    Factory for Profile model.

    Creates user profiles with optional username.
    If user is not provided, creates a new UserFactory instance.

    Examples:
        # Profile for existing user
        profile = ProfileFactory(user=existing_user, username="testuser")

        # Profile without username (incomplete)
        profile = ProfileFactory(user=existing_user, username="")

        # Profile with full name
        profile = ProfileFactory(
            user=existing_user,
            username="testuser",
            first_name="John",
            last_name="Doe"
        )
    """

    class Meta:
        model = Profile
        django_get_or_create = ("user",)

    user = factory.SubFactory(UserFactory)
    username = factory.Sequence(lambda n: f"username{n}")
    first_name = factory.Faker("first_name")
    last_name = factory.Faker("last_name")
    timezone = "UTC"
    preferences = factory.LazyFunction(lambda: {"theme": "light", "language": "en"})


class LinkedAccountFactory(factory.django.DjangoModelFactory):
    """
    Factory for LinkedAccount model.

    Creates OAuth provider connections.
    Default provider is email.

    Examples:
        # Email linked account
        linked = LinkedAccountFactory(user=user, provider="email")

        # Google linked account
        linked = LinkedAccountFactory(
            user=user,
            provider="google",
            provider_user_id="google-uid-123"
        )

        # Apple linked account
        linked = LinkedAccountFactory(
            user=user,
            provider="apple",
            provider_user_id="apple-uid-456"
        )
    """

    class Meta:
        model = LinkedAccount

    user = factory.SubFactory(UserFactory)
    provider = LinkedAccount.Provider.EMAIL
    provider_user_id = factory.LazyAttribute(lambda obj: obj.user.email)


class EmailVerificationTokenFactory(factory.django.DjangoModelFactory):
    """
    Factory for EmailVerificationToken model.

    Creates verification or password reset tokens.
    By default, creates valid email verification tokens that expire in 24 hours.

    Examples:
        # Valid verification token
        token = EmailVerificationTokenFactory(user=user)

        # Expired token
        token = EmailVerificationTokenFactory(
            user=user,
            expires_at=timezone.now() - timedelta(hours=1)
        )

        # Used token
        token = EmailVerificationTokenFactory(
            user=user,
            used_at=timezone.now()
        )

        # Password reset token
        token = EmailVerificationTokenFactory(
            user=user,
            token_type=EmailVerificationToken.TokenType.PASSWORD_RESET,
            expires_at=timezone.now() + timedelta(hours=1)
        )
    """

    class Meta:
        model = EmailVerificationToken

    user = factory.SubFactory(UserFactory)
    token = factory.LazyFunction(lambda: secrets.token_hex(32))
    token_type = EmailVerificationToken.TokenType.EMAIL_VERIFICATION
    expires_at = factory.LazyFunction(lambda: timezone.now() + timedelta(hours=24))
    used_at = None
