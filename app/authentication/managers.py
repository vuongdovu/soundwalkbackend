"""
Custom user manager for email-based authentication.

This module provides the UserManager class that handles user creation
with email as the primary identifier instead of username.

Related files:
    - models.py: User model that uses this manager

Security:
    - Passwords are automatically hashed via set_password()
    - Email addresses are normalized (lowercase domain)
"""

from django.contrib.auth.models import BaseUserManager


class UserManager(BaseUserManager):
    """
    Custom manager for User model with email-based authentication.

    This manager provides methods for creating regular users and superusers
    using email instead of username as the primary identifier.

    Note:
        Profile data (first_name, last_name, etc.) should be set on the
        Profile model, not on User. Profile is auto-created via signals.

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

    def create_user(self, email, password=None, **extra_fields):
        """
        Create and save a regular user with the given email and password.

        Note: first_name/last_name should be set on Profile, not User.

        Args:
            email: User's email address (required)
            password: User's password (optional for OAuth users)
            **extra_fields: Additional fields to set on the user

        Returns:
            User: The created user instance

        Raises:
            ValueError: If email is not provided
        """
        if not email:
            raise ValueError("The Email field must be set")

        # Normalize email (lowercase the domain portion)
        email = self.normalize_email(email)

        # Set default values for required fields
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)

        # Remove any profile-related fields that might be passed
        # These belong on the Profile model, not User
        extra_fields.pop("first_name", None)
        extra_fields.pop("last_name", None)

        # Create the user instance
        user = self.model(email=email, **extra_fields)

        # Hash the password if provided
        if password:
            user.set_password(password)
        else:
            # For OAuth users who don't have a password
            user.set_unusable_password()

        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        """
        Create and save a superuser with the given email and password.

        Args:
            email: Superuser's email address (required)
            password: Superuser's password (required)
            **extra_fields: Additional fields to set on the user

        Returns:
            User: The created superuser instance

        Raises:
            ValueError: If is_staff or is_superuser is not True
        """
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("email_verified", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        return self.create_user(email, password, **extra_fields)
