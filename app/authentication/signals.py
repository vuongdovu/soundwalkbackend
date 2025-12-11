"""
Django signals for authentication.

This module defines signal handlers for:
- Auto-creating Profile when User is created
- Populating Profile from social login data
- Sending verification email on registration
- Logging authentication events

Related files:
    - models.py: User, Profile, and LinkedAccount models
    - apps.py: Signal import in ready()
    - adapters.py: Sets extra_data with name info for social logins

Usage:
    Signals are automatically connected when the app is ready.
    See apps.py for the import that triggers connection.
"""

import logging

from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user_profile(sender, instance, created, **kwargs):
    """
    Create a Profile for newly created users.

    This signal handler automatically creates a Profile instance
    whenever a new User is created. Profile starts with empty fields
    until the user completes their profile.

    Args:
        sender: The User model class
        instance: The User instance that was saved
        created: Boolean indicating if this is a new record
        **kwargs: Additional signal arguments
    """
    if created:
        from authentication.models import Profile

        Profile.objects.get_or_create(user=instance)
        logger.debug(f"Profile created for user: {instance.email}")


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def send_verification_on_registration(sender, instance, created, **kwargs):
    """
    Send verification email when a new email user is created.

    Only sends for users who registered via email (not OAuth).
    OAuth users have pre-verified emails.

    Args:
        sender: The User model class
        instance: The User instance that was saved
        created: Boolean indicating if this is a new record
        **kwargs: Additional signal arguments
    """
    if created and not instance.email_verified:
        # Check if this is an email registration (has email LinkedAccount)
        from authentication.models import LinkedAccount

        is_email_registration = LinkedAccount.objects.filter(
            user=instance, provider=LinkedAccount.Provider.EMAIL
        ).exists()

        if is_email_registration:
            # TODO: Implement verification email sending
            # from authentication.services import AuthService
            # AuthService.send_verification_email(instance)
            logger.debug(
                f"Verification email would be sent to {instance.email} (not implemented)"
            )


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def log_email_verification(sender, instance, created, update_fields, **kwargs):
    """
    Log when a user's email is verified.

    Args:
        sender: The User model class
        instance: The User instance that was saved
        created: Boolean indicating if this is a new record
        update_fields: Fields that were updated (if using update_fields)
        **kwargs: Additional signal arguments
    """
    if not created and update_fields:
        if "email_verified" in update_fields and instance.email_verified:
            logger.info(f"Email verified for user: {instance.email}")

            # TODO: Send welcome email after verification
            # from authentication.tasks import send_welcome_email
            # send_welcome_email.delay(instance.id)


# Connect to allauth's social_account_added signal
try:
    from allauth.socialaccount.signals import social_account_added

    @receiver(social_account_added)
    def populate_profile_from_social(sender, request, sociallogin, **kwargs):
        """
        Populate Profile with name data from social login.

        This signal fires after a social account is linked to a user,
        allowing us to extract name data from the social provider
        and store it in the Profile.

        Args:
            sender: The signal sender
            request: The HTTP request
            sociallogin: The social login object containing provider data
            **kwargs: Additional signal arguments
        """
        from authentication.models import Profile

        user = sociallogin.user
        extra_data = sociallogin.account.extra_data

        try:
            profile = user.profile
        except Profile.DoesNotExist:
            profile = Profile.objects.create(user=user)

        # Extract name from extra_data (set by adapter)
        first_name = extra_data.get("first_name", "")
        last_name = extra_data.get("last_name", "")

        # Also try provider-specific fields if not in extra_data
        provider = sociallogin.account.provider
        if not first_name and not last_name:
            if provider == "google":
                first_name = extra_data.get("given_name", "")
                last_name = extra_data.get("family_name", "")

        # Only update if we have new data and profile doesn't already have it
        updated = False
        if first_name and not profile.first_name:
            profile.first_name = first_name
            updated = True
        if last_name and not profile.last_name:
            profile.last_name = last_name
            updated = True

        if updated:
            profile.save(update_fields=["first_name", "last_name", "updated_at"])
            logger.debug(
                f"Profile populated from {provider} for user: {user.email}",
                extra={
                    "first_name": profile.first_name,
                    "last_name": profile.last_name,
                },
            )

except ImportError:
    # allauth not installed, skip social signals
    logger.debug("allauth not installed, skipping social signals")
