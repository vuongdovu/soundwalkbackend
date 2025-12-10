"""
Celery tasks for utils app.

This module defines async tasks for:
- Email sending
- Temporary file cleanup
- External service health checks

Usage:
    from utils.tasks import send_email_task
    send_email_task.delay(to="user@example.com", subject="Hello", ...)
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
def send_email_task(
    self,
    to: str | list[str],
    subject: str,
    template_name: str,
    context: dict,
    **kwargs,
) -> bool:
    """
    Async email sending task.

    Args:
        to: Recipient email(s)
        subject: Email subject
        template_name: Template name
        context: Template context
        **kwargs: Additional EmailService.send() arguments

    Returns:
        True if email was sent successfully
    """
    from utils.services.email import EmailService

    try:
        success = EmailService.send(
            to=to,
            subject=subject,
            template_name=template_name,
            context=context,
            **kwargs,
        )
        if success:
            logger.info(f"Email sent successfully to {to}")
        return success
    except Exception as e:
        logger.error(f"Failed to send email to {to}: {e}")
        raise


@shared_task
def cleanup_temp_files(directory: str, max_age_hours: int = 24) -> int:
    """
    Clean up temporary files older than max_age.

    This is a periodic task that should be scheduled via celery-beat.

    Args:
        directory: Directory to clean
        max_age_hours: Delete files older than this (hours)

    Returns:
        Number of files deleted
    """
    # TODO: Implement file cleanup
    # import os
    # from datetime import datetime, timedelta
    # from pathlib import Path
    #
    # deleted = 0
    # cutoff = datetime.now() - timedelta(hours=max_age_hours)
    #
    # path = Path(directory)
    # if not path.exists():
    #     logger.warning(f"Directory does not exist: {directory}")
    #     return 0
    #
    # for file in path.iterdir():
    #     if file.is_file():
    #         mtime = datetime.fromtimestamp(file.stat().st_mtime)
    #         if mtime < cutoff:
    #             try:
    #                 file.unlink()
    #                 deleted += 1
    #             except Exception as e:
    #                 logger.error(f"Failed to delete {file}: {e}")
    #
    # logger.info(f"Cleaned up {deleted} temp files from {directory}")
    # return deleted
    logger.info(
        f"cleanup_temp_files called for {directory} (max_age={max_age_hours}h) (not implemented)"
    )
    return 0


@shared_task
def health_check_external_services() -> dict:
    """
    Check external service availability.

    This is a periodic task for monitoring external dependencies.

    Returns:
        Dict with service status for each external service
    """
    # TODO: Implement health checks
    # results = {}
    #
    # # Check Redis
    # try:
    #     from django.core.cache import cache
    #     cache.set("health_check", "ok", timeout=10)
    #     results["redis"] = "healthy" if cache.get("health_check") == "ok" else "unhealthy"
    # except Exception as e:
    #     results["redis"] = f"unhealthy: {e}"
    #
    # # Check Database
    # try:
    #     from django.db import connection
    #     with connection.cursor() as cursor:
    #         cursor.execute("SELECT 1")
    #     results["database"] = "healthy"
    # except Exception as e:
    #     results["database"] = f"unhealthy: {e}"
    #
    # # Check Stripe (if configured)
    # try:
    #     from django.conf import settings
    #     if settings.STRIPE_SECRET_KEY:
    #         import stripe
    #         stripe.api_key = settings.STRIPE_SECRET_KEY
    #         stripe.Balance.retrieve()
    #         results["stripe"] = "healthy"
    #     else:
    #         results["stripe"] = "not configured"
    # except Exception as e:
    #     results["stripe"] = f"unhealthy: {e}"
    #
    # logger.info(f"External service health check: {results}")
    # return results
    logger.info("health_check_external_services called (not implemented)")
    return {
        "redis": "not checked",
        "database": "not checked",
        "stripe": "not checked",
    }
