"""
Authentication services.

This module provides the AuthService class for user authentication,
profile management, email verification, and password reset functionality.

Related files:
    - models.py: User, Profile, LinkedAccount, EmailVerificationToken
    - tasks.py: Async email sending
    - signals.py: Profile auto-creation

Security:
    - Tokens are cryptographically random (32 bytes)
    - Passwords hashed with Django's PBKDF2
    - Token expiration enforced
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from authentication.models import User, Profile, LinkedAccount

logger = logging.getLogger(__name__)


class AuthService:
    """
    Centralized authentication business logic.

    This service encapsulates all authentication-related operations,
    providing a clean interface for views and other services.

    Usage:
        from authentication.services import AuthService

        # Get or create profile
        profile = AuthService.get_or_create_profile(user)

        # Validate username
        is_valid, message = AuthService.validate_username('johndoe')

        # Create linked account
        linked = AuthService.create_linked_account(user, 'google', 'google123')

    Note:
        Profile updates are typically handled via ProfileUpdateSerializer
        which provides conditional username validation. Service methods
        like update_profile() are available for programmatic updates.
    """

    # Token expiration times (in hours)
    EMAIL_VERIFICATION_EXPIRY_HOURS = 24
    PASSWORD_RESET_EXPIRY_HOURS = 1

    @staticmethod
    def complete_profile(
        user: User,
        username: str,
        first_name: str = "",
        last_name: str = "",
        profile_picture=None,
    ) -> Profile:
        """
        Complete or update user profile.

        This method is used after OAuth signup or initial registration
        to set the user's username and optional profile data.

        Args:
            user: User instance
            username: Required username (3-30 chars, alphanumeric + _ + -)
            first_name: Optional first name
            last_name: Optional last name
            profile_picture: Optional uploaded image file

        Returns:
            Updated Profile instance

        Raises:
            ValueError: If username validation fails
        """
        from authentication.models import Profile

        # Get or create profile
        profile, created = Profile.objects.get_or_create(user=user)

        # Normalize and set username
        profile.username = username.lower().strip()
        profile.first_name = first_name.strip() if first_name else ""
        profile.last_name = last_name.strip() if last_name else ""

        # Handle profile picture upload
        if profile_picture:
            profile.profile_picture = profile_picture

        # Run validators and save
        profile.full_clean()
        profile.save()

        logger.info(
            f"Profile {'created' if created else 'updated'} for user: {user.email}",
            extra={"user_id": user.id, "username": username},
        )

        return profile

    @staticmethod
    def validate_username(username: str, exclude_user: User = None) -> tuple[bool, str]:
        """
        Validate a username for format, reserved names, and uniqueness.

        Args:
            username: The username to validate
            exclude_user: User to exclude from uniqueness check (for updates)

        Returns:
            Tuple of (is_valid: bool, message: str)
        """
        from authentication.models import Profile, RESERVED_USERNAMES

        username = username.lower().strip()

        # Check format: 3-30 chars, alphanumeric + _ + -
        if not re.match(r"^[a-zA-Z0-9_-]{3,30}$", username):
            return False, (
                "Username must be 3-30 characters and contain only "
                "letters, numbers, underscores, and hyphens."
            )

        # Check reserved names
        if username in RESERVED_USERNAMES:
            return False, f"The username '{username}' is reserved."

        # Check uniqueness (case-insensitive)
        existing = Profile.objects.filter(username__iexact=username)
        if exclude_user:
            existing = existing.exclude(user=exclude_user)
        if existing.exists():
            return False, "This username is already taken."

        return True, "Username is available."

    @staticmethod
    def create_linked_account(
        user: User,
        provider: str,
        provider_user_id: str,
    ) -> LinkedAccount:
        """
        Create or get a linked account for a user.

        Args:
            user: User to link the account to
            provider: Provider name (email, google, apple)
            provider_user_id: Unique ID from the provider

        Returns:
            LinkedAccount instance
        """
        from authentication.models import LinkedAccount

        linked_account, created = LinkedAccount.objects.get_or_create(
            provider=provider,
            provider_user_id=provider_user_id,
            defaults={"user": user},
        )

        if created:
            logger.info(
                f"Linked {provider} account for user: {user.email}",
                extra={
                    "user_id": user.id,
                    "provider": provider,
                    "provider_user_id": provider_user_id,
                },
            )

        return linked_account

    @staticmethod
    def get_or_create_profile(user: User) -> Profile:
        """
        Get or create user profile.

        Args:
            user: User instance

        Returns:
            Profile instance for the user
        """
        from authentication.models import Profile

        profile, created = Profile.objects.get_or_create(user=user)
        if created:
            logger.debug(f"Profile created for user: {user.email}")
        return profile

    @staticmethod
    def update_profile(user: User, **data) -> Profile:
        """
        Update user profile data.

        Args:
            user: User instance
            **data: Profile fields to update (first_name, last_name, timezone, etc.)

        Returns:
            Updated Profile instance
        """
        profile = AuthService.get_or_create_profile(user)

        for field, value in data.items():
            if hasattr(profile, field):
                setattr(profile, field, value)

        profile.save()
        logger.info(f"Profile updated for user: {user.email}")
        return profile

    @staticmethod
    def create_user(email: str, password: str | None = None, **kwargs) -> User:
        """
        Create a new user with optional profile.

        Args:
            email: User's email address
            password: Password (None for OAuth users)
            **kwargs: Additional user fields

        Returns:
            Created User instance

        Raises:
            ValueError: If email is invalid or already exists
        """
        from authentication.models import User, LinkedAccount

        # Normalize email
        email = email.lower().strip()

        # Check for existing user
        if User.objects.filter(email__iexact=email).exists():
            raise ValueError("A user with this email already exists")

        # Create user
        user = User.objects.create_user(
            email=email,
            password=password,
            **kwargs
        )

        # Create LinkedAccount for email registration
        if password:
            LinkedAccount.objects.create(
                user=user,
                provider=LinkedAccount.Provider.EMAIL,
                provider_user_id=email,
            )

        logger.info(f"User created: {user.email}")
        return user

    @staticmethod
    def verify_email(token: str) -> tuple[bool, str]:
        """
        Verify email with token.

        Args:
            token: The verification token string

        Returns:
            Tuple of (success: bool, message: str)
        """
        from django.utils import timezone
        from authentication.models import EmailVerificationToken

        try:
            token_obj = EmailVerificationToken.objects.get(
                token=token,
                token_type=EmailVerificationToken.TokenType.EMAIL_VERIFICATION,
                used_at__isnull=True,
                expires_at__gt=timezone.now()
            )
        except EmailVerificationToken.DoesNotExist:
            return False, "Invalid or expired token"

        # Mark user as verified
        user = token_obj.user
        user.email_verified = True
        user.save(update_fields=["email_verified", "updated_at"])

        # Mark token as used
        token_obj.used_at = timezone.now()
        token_obj.save(update_fields=["used_at"])

        logger.info(f"Email verified for user: {user.email}")
        return True, "Email verified successfully"

    @staticmethod
    def request_password_reset(email: str) -> bool:
        """
        Send password reset email.

        Args:
            email: User's email address

        Returns:
            True if email was sent (or would be sent - don't reveal user existence)

        Note:
            Always returns True to prevent user enumeration attacks.
            Email is only sent if user exists.
        """
        from datetime import timedelta
        from django.utils import timezone
        from authentication.models import User, EmailVerificationToken
        from core.helpers import generate_token

        try:
            user = User.objects.get(email=email.lower().strip())
        except User.DoesNotExist:
            # Don't reveal whether user exists
            logger.debug(f"Password reset requested for non-existent email: {email}")
            return True

        # Create reset token
        EmailVerificationToken.objects.create(
            user=user,
            token=generate_token(),
            token_type=EmailVerificationToken.TokenType.PASSWORD_RESET,
            expires_at=timezone.now() + timedelta(hours=AuthService.PASSWORD_RESET_EXPIRY_HOURS)
        )

        # TODO: Send email asynchronously
        # send_password_reset_email.delay(user.id)

        logger.info(f"Password reset requested for user: {user.email}")
        return True

    @staticmethod
    def reset_password(token: str, new_password: str) -> tuple[bool, str]:
        """
        Reset password with token.

        Args:
            token: The password reset token string
            new_password: The new password to set

        Returns:
            Tuple of (success: bool, message: str)
        """
        from django.utils import timezone
        from authentication.models import EmailVerificationToken

        try:
            token_obj = EmailVerificationToken.objects.get(
                token=token,
                token_type=EmailVerificationToken.TokenType.PASSWORD_RESET,
                used_at__isnull=True,
                expires_at__gt=timezone.now()
            )
        except EmailVerificationToken.DoesNotExist:
            return False, "Invalid or expired token"

        # Set new password
        user = token_obj.user
        user.set_password(new_password)
        user.save(update_fields=["password", "updated_at"])

        # Mark token as used
        token_obj.used_at = timezone.now()
        token_obj.save(update_fields=["used_at"])

        # Invalidate all other reset tokens for this user
        EmailVerificationToken.objects.filter(
            user=user,
            token_type=EmailVerificationToken.TokenType.PASSWORD_RESET,
            used_at__isnull=True
        ).exclude(pk=token_obj.pk).update(used_at=timezone.now())

        logger.info(f"Password reset for user: {user.email}")
        return True, "Password reset successfully"

    @staticmethod
    def deactivate_user(user: User, reason: str = "") -> None:
        """
        Soft-delete user account.

        Sets is_active=False rather than deleting the user,
        preserving data integrity and audit trail.

        Args:
            user: User to deactivate
            reason: Optional reason for deactivation (for logging)
        """
        user.is_active = False
        user.save(update_fields=["is_active", "updated_at"])

        logger.warning(
            f"User deactivated: {user.email}",
            extra={"user_id": user.id, "reason": reason}
        )

    @staticmethod
    def send_verification_email(user: User) -> None:
        """
        Send email verification to user.

        Creates a verification token and queues the email for sending.

        Args:
            user: User to send verification email to
        """
        from datetime import timedelta
        from django.utils import timezone
        from authentication.models import EmailVerificationToken
        from core.helpers import generate_token

        # Create verification token
        EmailVerificationToken.objects.create(
            user=user,
            token=generate_token(),
            token_type=EmailVerificationToken.TokenType.EMAIL_VERIFICATION,
            expires_at=timezone.now() + timedelta(hours=AuthService.EMAIL_VERIFICATION_EXPIRY_HOURS)
        )

        # TODO: Queue email for sending
        # send_email_task.delay(user.id)

        logger.info(f"Verification email queued for user: {user.email}")
