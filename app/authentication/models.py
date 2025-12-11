"""
Authentication models.

This module defines the core authentication models:
- User: Custom user model with email-based authentication (slim, auth-focused)
- Profile: Extended user profile data (OneToOne with User)
- LinkedAccount: Tracks authentication providers linked to a user
- EmailVerificationToken: Tokens for email verification and password reset

Related files:
    - managers.py: Custom user manager for email-based creation
    - services.py: AuthService business logic
    - signals.py: Auto-create profile on user creation

Security:
    - User passwords hashed with Django's PBKDF2
    - Verification tokens are cryptographically random
    - Token expiration enforced at database level
"""

import re

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower

from core.models import BaseModel
from authentication.managers import UserManager


# Reserved usernames that cannot be used
RESERVED_USERNAMES = frozenset([
    "admin", "administrator", "root", "system", "api", "www",
    "mail", "email", "support", "help", "info", "contact",
    "about", "terms", "privacy", "security", "account", "login",
    "logout", "register", "signup", "signin", "signout", "auth",
    "authentication", "user", "users", "profile", "profiles",
    "settings", "config", "configuration", "dashboard", "home",
    "index", "null", "undefined", "anonymous", "guest", "test",
    "demo", "example", "official", "verified", "staff", "mod",
    "moderator", "bot", "robot", "service", "notification",
])


def validate_username_not_reserved(value):
    """Validate that username is not in the reserved list."""
    if value.lower() in RESERVED_USERNAMES:
        raise ValidationError(
            f"The username '{value}' is reserved and cannot be used."
        )


def validate_username_format(value):
    """Validate username format: 3-30 chars, alphanumeric + _ + -."""
    if not re.match(r"^[a-zA-Z0-9_-]{3,30}$", value):
        raise ValidationError(
            "Username must be 3-30 characters and contain only "
            "letters, numbers, underscores, and hyphens."
        )


class User(AbstractBaseUser, PermissionsMixin):
    """
    Custom User model using email as the primary identifier.

    This is a slim user model focused on authentication only.
    Profile data (name, avatar, etc.) is stored in the Profile model.
    OAuth provider tracking is handled by LinkedAccount.

    Fields:
        email: Primary identifier, unique, used for login
        email_verified: Whether the user's email has been verified
        is_active: Whether the user account is active
        is_staff: Whether the user can access Django admin
        date_joined: When the user account was created
        updated_at: When the user record was last modified

    Usage:
        # Create a regular user
        user = User.objects.create_user(
            email='user@example.com',
            password='securepassword'
        )

        # Create a superuser
        admin = User.objects.create_superuser(
            email='admin@example.com',
            password='adminpassword'
        )
    """

    # Primary identifier (replaces username)
    email = models.EmailField(
        unique=True,
        db_index=True,
        max_length=254,
        help_text="User's email address (primary identifier)",
    )

    # Email verification status
    email_verified = models.BooleanField(
        default=False,
        help_text="Whether the user's email has been verified",
    )

    # Account status flags
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this user account is active. Deselect instead of deleting.",
    )
    is_staff = models.BooleanField(
        default=False,
        help_text="Whether the user can access the admin site.",
    )

    # Timestamps
    date_joined = models.DateTimeField(
        auto_now_add=True,
        help_text="When the user account was created",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="When the user record was last modified",
    )

    # Configure email as the username field
    USERNAME_FIELD = "email"

    # Fields required when creating a user via createsuperuser command
    # Email is automatically required since it's the USERNAME_FIELD
    REQUIRED_FIELDS = []

    # Use custom manager for email-based user creation
    objects = UserManager()

    class Meta:
        verbose_name = "user"
        verbose_name_plural = "users"
        ordering = ["-date_joined"]

    def __str__(self):
        """Return the user's email as string representation."""
        return self.email

    def get_full_name(self):
        """
        Return the user's full name from profile.

        Returns:
            str: Full name from profile, or email if no profile/name set.
        """
        try:
            return self.profile.full_name or self.email
        except Profile.DoesNotExist:
            return self.email

    def get_short_name(self):
        """
        Return the user's short name from profile.

        Returns:
            str: First name from profile, or email local part if not set.
        """
        try:
            return self.profile.first_name or self.email.split("@")[0]
        except Profile.DoesNotExist:
            return self.email.split("@")[0]

    @property
    def has_completed_profile(self):
        """Check if user has completed profile setup (has username)."""
        try:
            return bool(self.profile.username)
        except Profile.DoesNotExist:
            return False


class Profile(BaseModel):
    """
    Extended user profile data.

    This model stores user profile information including:
    - Identity: username, first_name, last_name
    - Display: profile_picture
    - Preferences: timezone, preferences JSON

    Fields:
        user: OneToOne link to User (also serves as primary key)
        username: Unique username (3-30 chars, alphanumeric + _ + -)
        first_name: User's first name
        last_name: User's last name
        profile_picture: User's profile picture (ImageField)
        timezone: User's preferred timezone
        preferences: JSON field for flexible user preferences

    Usage:
        # Get or create profile for a user
        profile, created = Profile.objects.get_or_create(user=user)

        # Access profile from user
        user.profile.username

    Note:
        Profile is automatically created via signals when a User is created.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
        primary_key=True,
        help_text="User this profile belongs to",
    )

    # Identity fields (moved from User)
    first_name = models.CharField(
        max_length=150,
        blank=True,
        help_text="User's first name",
    )
    last_name = models.CharField(
        max_length=150,
        blank=True,
        help_text="User's last name",
    )

    # Username with validation
    username = models.CharField(
        max_length=30,
        blank=True,
        db_index=True,
        validators=[validate_username_format, validate_username_not_reserved],
        help_text="Unique username (3-30 chars, alphanumeric + _ + -)",
    )

    # Profile picture (ImageField instead of URLField)
    profile_picture = models.ImageField(
        upload_to="profile_pictures/",
        blank=True,
        null=True,
        help_text="User's profile picture",
    )

    timezone = models.CharField(
        max_length=50,
        default="UTC",
        help_text="User's preferred timezone (e.g., 'America/New_York')",
    )

    # Flexible preferences storage
    preferences = models.JSONField(
        default=dict,
        blank=True,
        help_text="User preferences as JSON (e.g., theme, language, email_frequency)",
    )
    # Example preferences structure:
    # {
    #     "theme": "dark",
    #     "language": "en",
    #     "email_frequency": "daily"
    # }

    # Biometric authentication (Face ID / Touch ID)
    bio_public_key = models.TextField(
        blank=True,
        null=True,
        help_text="Base64-encoded EC public key (DER format) for biometric auth",
    )

    class Meta:
        db_table = "authentication_profile"
        verbose_name = "profile"
        verbose_name_plural = "profiles"
        constraints = [
            # Case-insensitive unique constraint for username
            models.UniqueConstraint(
                Lower("username"),
                name="unique_username_case_insensitive",
                condition=models.Q(username__gt=""),  # Only for non-empty usernames
            ),
        ]

    def __str__(self):
        """Return username or user email."""
        return self.username or str(self.user)

    @property
    def full_name(self):
        """Return full name or empty string."""
        return f"{self.first_name} {self.last_name}".strip()

    def clean(self):
        """Validate and normalize username."""
        super().clean()
        if self.username:
            # Normalize to lowercase for case-insensitive uniqueness
            self.username = self.username.lower()

    def save(self, *args, **kwargs):
        """Normalize username before saving."""
        if self.username:
            self.username = self.username.lower()
        super().save(*args, **kwargs)


class LinkedAccount(BaseModel):
    """
    Tracks authentication providers linked to a user account.

    This model allows users to link multiple authentication methods
    (email, Google, Apple) to a single account.

    Fields:
        user: User this account belongs to
        provider: Authentication provider (email, google, apple)
        provider_user_id: Unique identifier from the provider
        created_at: When this link was created (from BaseModel)
        updated_at: When this link was last modified (from BaseModel)

    Usage:
        # Check if user has Google linked
        user.linked_accounts.filter(provider='google').exists()

        # Get all providers for a user
        providers = user.linked_accounts.values_list('provider', flat=True)

        # Link a new provider
        LinkedAccount.objects.create(
            user=user,
            provider=LinkedAccount.Provider.GOOGLE,
            provider_user_id='google123',
        )
    """

    class Provider(models.TextChoices):
        """Authentication provider choices."""

        EMAIL = "email", "Email"
        GOOGLE = "google", "Google"
        APPLE = "apple", "Apple"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="linked_accounts",
        help_text="User this linked account belongs to",
    )

    provider = models.CharField(
        max_length=20,
        choices=Provider.choices,
        db_index=True,
        help_text="Authentication provider",
    )

    provider_user_id = models.CharField(
        max_length=255,
        help_text="Unique identifier from the provider",
    )

    class Meta:
        db_table = "authentication_linked_account"
        verbose_name = "linked account"
        verbose_name_plural = "linked accounts"
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "provider_user_id"],
                name="unique_provider_user",
            ),
        ]
        indexes = [
            models.Index(
                fields=["user", "provider"],
                name="auth_linked_user_provider_idx",
            ),
        ]

    def __str__(self):
        return f"{self.get_provider_display()} account for {self.user}"


class EmailVerificationToken(BaseModel):
    """
    Tokens for email verification and password reset.

    This model stores secure tokens used for:
    - Email address verification after registration
    - Password reset functionality

    Fields:
        user: User this token belongs to
        token: Unique, cryptographically random token string
        token_type: Type of token (email_verification or password_reset)
        expires_at: When this token expires
        used_at: When this token was used (null if unused)

    Security:
        - Tokens are 64-character cryptographically random strings
        - Tokens expire after a configurable period
        - Tokens are single-use (marked with used_at after use)
        - Old tokens should be cleaned up periodically

    Usage:
        # Create a verification token
        token = EmailVerificationToken.objects.create(
            user=user,
            token=generate_token(),
            token_type=EmailVerificationToken.TokenType.EMAIL_VERIFICATION,
            expires_at=timezone.now() + timedelta(hours=24)
        )

        # Verify a token
        token = EmailVerificationToken.objects.get(
            token=token_string,
            used_at__isnull=True,
            expires_at__gt=timezone.now()
        )
    """

    class TokenType(models.TextChoices):
        """Types of verification tokens."""

        EMAIL_VERIFICATION = "email_verification", "Email Verification"
        PASSWORD_RESET = "password_reset", "Password Reset"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="verification_tokens",
        help_text="User this token belongs to",
    )
    token = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text="Unique verification token",
    )
    token_type = models.CharField(
        max_length=20,
        choices=TokenType.choices,
        help_text="Type of verification token",
    )
    expires_at = models.DateTimeField(
        db_index=True,
        help_text="When this token expires",
    )
    used_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this token was used (null if unused)",
    )

    class Meta:
        db_table = "authentication_email_verification_token"
        verbose_name = "email verification token"
        verbose_name_plural = "email verification tokens"
        indexes = [
            models.Index(
                fields=["user", "token_type", "used_at"],
                name="auth_token_user_type_used_idx",
            ),
        ]

    def __str__(self):
        """Return token type and user."""
        return f"{self.get_token_type_display()} for {self.user}"

    @property
    def is_valid(self):
        """Check if token is valid (not used and not expired)."""
        from django.utils import timezone

        return self.used_at is None and self.expires_at > timezone.now()
