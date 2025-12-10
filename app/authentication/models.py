"""
Authentication models.

This module defines the core authentication models:
- User: Custom user model with email-based authentication
- Profile: Extended user profile data (OneToOne with User)
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

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.conf import settings
from django.db import models

from core.models import BaseModel
from authentication.managers import UserManager


class User(AbstractBaseUser, PermissionsMixin):
    """
    Custom User model using email as the primary identifier.

    This model provides:
    - Email-based authentication (no username)
    - OAuth provider tracking (google, apple, email)
    - Email verification status
    - Standard Django permission system via PermissionsMixin

    Fields:
        email: Primary identifier, unique, used for login
        first_name: User's first name (optional)
        last_name: User's last name (optional)
        oauth_provider: How the user registered (google, apple, email)
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

    class OAuthProvider(models.TextChoices):
        """Choices for OAuth provider that registered the user."""

        EMAIL = "email", "Email"
        GOOGLE = "google", "Google"
        APPLE = "apple", "Apple"

    # Primary identifier (replaces username)
    email = models.EmailField(
        unique=True,
        db_index=True,
        max_length=254,
        help_text="User's email address (primary identifier)",
    )

    # Profile information
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

    # OAuth tracking
    oauth_provider = models.CharField(
        max_length=20,
        choices=OAuthProvider.choices,
        default=OAuthProvider.EMAIL,
        db_index=True,
        help_text="OAuth provider used for registration",
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
        Return the user's full name.

        Returns:
            str: First name and last name separated by a space, or email
                 if no name is set.
        """
        full_name = f"{self.first_name} {self.last_name}".strip()
        return full_name if full_name else self.email

    def get_short_name(self):
        """
        Return the user's short name.

        Returns:
            str: First name if set, otherwise the local part of their email.
        """
        return self.first_name if self.first_name else self.email.split("@")[0]

    @property
    def is_oauth_user(self):
        """Check if the user registered via OAuth (not email/password)."""
        return self.oauth_provider != self.OAuthProvider.EMAIL


class Profile(BaseModel):
    """
    Extended user profile data.

    This model stores additional user information that is optional
    and not required for authentication. It has a one-to-one
    relationship with the User model.

    Fields:
        user: OneToOne link to User (also serves as primary key)
        avatar_url: URL to user's profile picture
        display_name: Optional display name (different from first/last name)
        timezone: User's preferred timezone
        preferences: JSON field for flexible user preferences

    Usage:
        # Get or create profile for a user
        profile, created = Profile.objects.get_or_create(user=user)

        # Access profile from user
        user.profile.display_name

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

    # Display settings
    avatar_url = models.URLField(
        max_length=500,
        blank=True,
        help_text="URL to user's profile picture",
    )
    display_name = models.CharField(
        max_length=50,
        blank=True,
        help_text="Optional display name",
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

    class Meta:
        db_table = "authentication_profile"
        verbose_name = "profile"
        verbose_name_plural = "profiles"

    def __str__(self):
        """Return display name or user email."""
        return self.display_name or str(self.user)


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
