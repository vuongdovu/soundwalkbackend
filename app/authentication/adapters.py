"""
Custom adapters for django-allauth social authentication.

This module provides custom adapters that handle special logic for
email/password registration and social authentication (Google, Apple).

Related files:
    - models.py: User and LinkedAccount models
    - settings.py: ACCOUNT_ADAPTER and SOCIALACCOUNT_ADAPTER settings

Security:
    - OAuth users automatically have email_verified=True
    - Apple Sign-In quirk handled (email only on first login)
    - LinkedAccount created for each authentication provider
"""

import logging

from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter

logger = logging.getLogger(__name__)


class CustomAccountAdapter(DefaultAccountAdapter):
    """
    Custom adapter for email/password registration.

    This adapter creates a LinkedAccount with provider='email' for users
    who register with email/password (not OAuth).

    Usage:
        Configure in settings.py:
        ACCOUNT_ADAPTER = 'authentication.adapters.CustomAccountAdapter'
    """

    def save_user(self, request, user, form, commit=True):
        """
        Save the user and create LinkedAccount for email registration.

        Args:
            request: The HTTP request
            user: The user instance being created
            form: The registration form
            commit: Whether to save the user to database

        Returns:
            User: The saved user instance
        """
        user = super().save_user(request, user, form, commit=commit)

        if commit:
            from authentication.models import LinkedAccount

            # Create LinkedAccount for email/password registration
            LinkedAccount.objects.get_or_create(
                user=user,
                provider=LinkedAccount.Provider.EMAIL,
                provider_user_id=user.email,
            )

            logger.info(
                "Email user registered",
                extra={"user_id": user.id, "email": user.email},
            )

        return user


class CustomSocialAccountAdapter(DefaultSocialAccountAdapter):
    """
    Custom adapter for social authentication (Google, Apple).

    This adapter handles:
    - Creating LinkedAccount for the social provider
    - Extracting user data from social provider response
    - Handling Apple Sign-In quirk (email only sent on first login)
    - Auto-verifying email for OAuth users
    - Storing name data in extra_data for signal to populate Profile

    Usage:
        Configure in settings.py:
        SOCIALACCOUNT_ADAPTER = 'authentication.adapters.CustomSocialAccountAdapter'
    """

    def save_user(self, request, sociallogin, form=None):
        """
        Save the user from social login and create LinkedAccount.

        Args:
            request: The HTTP request
            sociallogin: The social login object with provider info
            form: Optional form data

        Returns:
            User: The saved user instance
        """
        user = super().save_user(request, sociallogin, form)

        provider = sociallogin.account.provider
        provider_user_id = sociallogin.account.uid

        # Mark email as verified for OAuth users
        user.email_verified = True
        user.save(update_fields=["email_verified", "updated_at"])

        # Create LinkedAccount for this social provider
        from authentication.models import LinkedAccount

        LinkedAccount.objects.get_or_create(
            provider=provider,
            provider_user_id=provider_user_id,
            defaults={"user": user},
        )

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

        Note: Since first_name/last_name are now on Profile (not User),
        we store the name data in extra_data for the signal handler to use.

        Args:
            request: The HTTP request
            sociallogin: The social login object
            data: Data from the social provider

        Returns:
            User: The populated user instance (not saved)
        """
        user = super().populate_user(request, sociallogin, data)
        provider = sociallogin.account.provider

        # Extract name data and store in extra_data for signal handler
        first_name = ""
        last_name = ""

        if provider == "apple":
            first_name, last_name = self._extract_apple_name(request, data)
        elif provider == "google":
            first_name, last_name = self._extract_google_name(data)

        # Store in extra_data for the social_account_added signal to use
        sociallogin.account.extra_data["first_name"] = first_name
        sociallogin.account.extra_data["last_name"] = last_name

        logger.debug(
            f"{provider.title()} Sign-In user populated",
            extra={
                "email": user.email,
                "first_name": first_name,
                "last_name": last_name,
            },
        )

        return user

    def _extract_apple_name(self, request, data):
        """
        Extract name from Apple Sign-In response.

        Apple only sends user info on the first authentication.
        We capture it from the request body if available.

        Args:
            request: The HTTP request (may contain user data in body)
            data: Data from Apple

        Returns:
            Tuple of (first_name, last_name)
        """
        first_name = ""
        last_name = ""

        # Try to get user data from request body (first login only)
        if hasattr(request, "data"):
            apple_user = request.data.get("user", {})
            if isinstance(apple_user, dict):
                name = apple_user.get("name", {})
                if isinstance(name, dict):
                    first_name = name.get("firstName", "")
                    last_name = name.get("lastName", "")

        return first_name, last_name

    def _extract_google_name(self, data):
        """
        Extract name from Google Sign-In response.

        Args:
            data: Data from Google

        Returns:
            Tuple of (first_name, last_name)
        """
        first_name = data.get("given_name", "")
        last_name = data.get("family_name", "")
        return first_name, last_name

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
