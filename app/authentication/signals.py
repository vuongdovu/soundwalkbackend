"""
Django signals for authentication.

This module defines signal handlers for:
- Auto-creating Profile when User is created
- Sending verification email on registration
- Logging authentication events

Related files:
    - models.py: User and Profile models
    - apps.py: Signal import in ready()

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
    whenever a new User is created.

    Args:
        sender: The User model class
        instance: The User instance that was saved
        created: Boolean indicating if this is a new record
        **kwargs: Additional signal arguments
    """
    if created:
        # TODO: Implement profile creation
        # from authentication.models import Profile
        #
        # Profile.objects.create(user=instance)
        # logger.debug(f"Profile created for user: {instance.email}")
        logger.debug(f"create_user_profile called for {instance.email} (not implemented)")


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
        # Only send for email registrations (not OAuth)
        if getattr(instance, "oauth_provider", "email") == "email":
            # TODO: Implement verification email sending
            # from authentication.tasks import send_verification_email
            # from authentication.services import AuthService
            #
            # # Create token and send email
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
