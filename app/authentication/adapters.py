"""
Custom adapters for django-allauth social authentication.

This module provides custom adapters that handle special logic for
email/password registration and social authentication (Google, Apple).

Related files:
    - models.py: User model with oauth_provider field
    - settings.py: ACCOUNT_ADAPTER and SOCIALACCOUNT_ADAPTER settings

Security:
    - OAuth users automatically have email_verified=True
    - Apple Sign-In quirk handled (email only on first login)
"""

import logging

from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter

logger = logging.getLogger(__name__)


class CustomAccountAdapter(DefaultAccountAdapter):
    """
    Custom adapter for email/password registration.

    This adapter sets the oauth_provider to 'email' for users
    who register with email/password (not OAuth).

    Usage:
        Configure in settings.py:
        ACCOUNT_ADAPTER = 'authentication.adapters.CustomAccountAdapter'
    """

    def save_user(self, request, user, form, commit=True):
        """
        Save the user and set oauth_provider to 'email'.

        Args:
            request: The HTTP request
            user: The user instance being created
            form: The registration form
            commit: Whether to save the user to database

        Returns:
            User: The saved user instance
        """
        user = super().save_user(request, user, form, commit=False)

        # Set oauth_provider for email/password registration
        user.oauth_provider = "email"

        if commit:
            user.save()

        return user


class CustomSocialAccountAdapter(DefaultSocialAccountAdapter):
    """
    Custom adapter for social authentication (Google, Apple).

    This adapter handles:
    - Setting oauth_provider based on the social login provider
    - Extracting user data from social provider response
    - Handling Apple Sign-In quirk (email only sent on first login)
    - Auto-verifying email for OAuth users

    Usage:
        Configure in settings.py:
        SOCIALACCOUNT_ADAPTER = 'authentication.adapters.CustomSocialAccountAdapter'
    """

    def save_user(self, request, sociallogin, form=None):
        """
        Save the user from social login and set oauth_provider.

        Args:
            request: The HTTP request
            sociallogin: The social login object with provider info
            form: Optional form data

        Returns:
            User: The saved user instance
        """
        user = super().save_user(request, sociallogin, form)

        # Set oauth_provider based on social provider
        provider = sociallogin.account.provider
        user.oauth_provider = provider
        user.email_verified = True  # OAuth emails are pre-verified

        user.save(update_fields=["oauth_provider", "email_verified"])

        logger.info(
            "Social user created",
            extra={
                "user_id": user.id,
                "provider": provider,
                "email": user.email,
            },
        )

        return user

    def populate_user(self, request, sociallogin, data):
        """
        Populate user data from social provider response.

        This method extracts user information from the social login
        and populates the user model fields.

        Args:
            request: The HTTP request
            sociallogin: The social login object
            data: Data from the social provider

        Returns:
            User: The populated user instance (not saved)
        """
        user = super().populate_user(request, sociallogin, data)
        provider = sociallogin.account.provider

        # Handle Apple Sign-In quirk
        # Apple only sends user info (name, email) on the FIRST login
        # After that, we need to get it from the stored account data
        if provider == "apple":
            self._handle_apple_signin(request, user, sociallogin, data)
        elif provider == "google":
            self._handle_google_signin(user, data)

        return user

    def _handle_apple_signin(self, request, user, sociallogin, data):
        """
        Handle Apple Sign-In specific logic.

        Apple only sends user info on the first authentication.
        We capture it from the request body if available.

        Args:
            request: The HTTP request (may contain user data in body)
            user: The user instance being populated
            sociallogin: The social login object
            data: Data from Apple
        """
        # Try to get user data from request body (first login only)
        if hasattr(request, "data"):
            apple_user = request.data.get("user", {})
            if isinstance(apple_user, dict):
                name = apple_user.get("name", {})
                if isinstance(name, dict):
                    user.first_name = name.get("firstName", "") or user.first_name
                    user.last_name = name.get("lastName", "") or user.last_name

                # Apple may send email in user data
                if not user.email:
                    user.email = apple_user.get("email", "")

        logger.debug(
            "Apple Sign-In user populated",
            extra={
                "email": user.email,
                "first_name": user.first_name,
            },
        )

    def _handle_google_signin(self, user, data):
        """
        Handle Google Sign-In specific logic.

        Extract first_name and last_name from Google's response.

        Args:
            user: The user instance being populated
            data: Data from Google
        """
        # Google provides given_name and family_name
        user.first_name = data.get("given_name", "") or user.first_name
        user.last_name = data.get("family_name", "") or user.last_name

        logger.debug(
            "Google Sign-In user populated",
            extra={
                "email": user.email,
                "first_name": user.first_name,
            },
        )

    def authentication_error(
        self, request, provider_id, error=None, exception=None, extra_context=None
    ):
        """
        Handle social authentication errors.

        Log errors for debugging and monitoring.

        Args:
            request: The HTTP request
            provider_id: The social provider ID
            error: Error message
            exception: Exception that was raised
            extra_context: Additional context
        """
        logger.error(
            "Social authentication error",
            extra={
                "provider": provider_id,
                "error": error,
                "exception": str(exception) if exception else None,
                "extra_context": extra_context,
            },
            exc_info=exception,
        )
        return super().authentication_error(
            request, provider_id, error, exception, extra_context
        )
