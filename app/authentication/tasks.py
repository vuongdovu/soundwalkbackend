"""
Celery tasks for authentication.

This module defines async tasks for:
- Sending verification emails
- Sending password reset emails
- Sending welcome emails
- Cleaning up expired tokens
- Deactivating unverified accounts

Related files:
    - services.py: AuthService that may trigger these tasks
    - models.py: EmailVerificationToken model

Usage:
    from authentication.tasks import send_verification_email
    send_verification_email.delay(user_id=123)
"""

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def send_verification_email(self, user_id: int) -> bool:
    """
    Send email verification link to user.

    Args:
        user_id: ID of the user to send email to

    Returns:
        True if email was sent successfully
    """
    # TODO: Implement email sending
    # from django.conf import settings
    # from authentication.models import User, EmailVerificationToken
    # from toolkit.services.email import EmailService
    #
    # try:
    #     user = User.objects.get(id=user_id)
    #     token = EmailVerificationToken.objects.filter(
    #         user=user,
    #         token_type=EmailVerificationToken.TokenType.EMAIL_VERIFICATION,
    #         used_at__isnull=True,
    #         expires_at__gt=timezone.now()
    #     ).latest('created_at')
    #
    #     verification_url = f"{settings.FRONTEND_URL}/verify-email?token={token.token}"
    #
    #     EmailService.send(
    #         to=user.email,
    #         subject="Verify your email address",
    #         template_name="authentication/verification_email",
    #         context={
    #             "user": user,
    #             "verification_url": verification_url,
    #         }
    #     )
    #
    #     logger.info(f"Verification email sent to {user.email}")
    #     return True
    #
    # except User.DoesNotExist:
    #     logger.error(f"User {user_id} not found for verification email")
    #     return False
    # except EmailVerificationToken.DoesNotExist:
    #     logger.error(f"No valid verification token for user {user_id}")
    #     return False
    logger.info(
        f"send_verification_email called for user_id={user_id} (not implemented)"
    )
    return True


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def send_password_reset_email(self, user_id: int) -> bool:
    """
    Send password reset link to user.

    Args:
        user_id: ID of the user to send email to

    Returns:
        True if email was sent successfully
    """
    # TODO: Implement password reset email
    # from django.conf import settings
    # from authentication.models import User, EmailVerificationToken
    # from toolkit.services.email import EmailService
    #
    # try:
    #     user = User.objects.get(id=user_id)
    #     token = EmailVerificationToken.objects.filter(
    #         user=user,
    #         token_type=EmailVerificationToken.TokenType.PASSWORD_RESET,
    #         used_at__isnull=True,
    #         expires_at__gt=timezone.now()
    #     ).latest('created_at')
    #
    #     reset_url = f"{settings.FRONTEND_URL}/reset-password?token={token.token}"
    #
    #     EmailService.send(
    #         to=user.email,
    #         subject="Reset your password",
    #         template_name="authentication/password_reset_email",
    #         context={
    #             "user": user,
    #             "reset_url": reset_url,
    #         }
    #     )
    #
    #     logger.info(f"Password reset email sent to {user.email}")
    #     return True
    #
    # except User.DoesNotExist:
    #     logger.error(f"User {user_id} not found for password reset email")
    #     return False
    logger.info(
        f"send_password_reset_email called for user_id={user_id} (not implemented)"
    )
    return True


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def send_welcome_email(self, user_id: int) -> bool:
    """
    Send welcome email after email verification.

    Args:
        user_id: ID of the user to send email to

    Returns:
        True if email was sent successfully
    """
    # TODO: Implement welcome email
    # from authentication.models import User
    # from toolkit.services.email import EmailService
    #
    # try:
    #     user = User.objects.get(id=user_id)
    #
    #     EmailService.send(
    #         to=user.email,
    #         subject="Welcome to our platform!",
    #         template_name="authentication/welcome_email",
    #         context={"user": user}
    #     )
    #
    #     logger.info(f"Welcome email sent to {user.email}")
    #     return True
    #
    # except User.DoesNotExist:
    #     logger.error(f"User {user_id} not found for welcome email")
    #     return False
    logger.info(f"send_welcome_email called for user_id={user_id} (not implemented)")
    return True


@shared_task
def cleanup_expired_tokens() -> int:
    """
    Remove expired verification tokens.

    This is a periodic task that should be scheduled via celery-beat.
    Recommended schedule: Daily

    Returns:
        Number of tokens deleted
    """
    # TODO: Implement token cleanup
    # from django.utils import timezone
    # from authentication.models import EmailVerificationToken
    #
    # # Delete tokens that are either:
    # # - Expired (expires_at < now)
    # # - Already used (used_at is not null)
    # deleted, _ = EmailVerificationToken.objects.filter(
    #     Q(expires_at__lt=timezone.now()) | Q(used_at__isnull=False)
    # ).delete()
    #
    # logger.info(f"Cleaned up {deleted} expired tokens")
    # return deleted
    logger.info("cleanup_expired_tokens called (not implemented)")
    return 0


@shared_task
def deactivate_unverified_accounts(days: int = 30) -> int:
    """
    Deactivate accounts that haven't verified email after N days.

    This is a periodic task that should be scheduled via celery-beat.
    Recommended schedule: Weekly

    Args:
        days: Number of days after which unverified accounts are deactivated

    Returns:
        Number of accounts deactivated
    """
    # TODO: Implement unverified account deactivation
    # from datetime import timedelta
    # from django.utils import timezone
    # from authentication.models import User
    #
    # cutoff_date = timezone.now() - timedelta(days=days)
    #
    # # Find users who:
    # # - Are not email verified
    # # - Were created more than N days ago
    # # - Are still active
    # # - Registered via email (not OAuth)
    # users_to_deactivate = User.objects.filter(
    #     email_verified=False,
    #     date_joined__lt=cutoff_date,
    #     is_active=True,
    #     oauth_provider='email'
    # )
    #
    # count = users_to_deactivate.count()
    # users_to_deactivate.update(is_active=False)
    #
    # logger.info(f"Deactivated {count} unverified accounts (older than {days} days)")
    # return count
    logger.info(
        f"deactivate_unverified_accounts called with days={days} (not implemented)"
    )
    return 0
