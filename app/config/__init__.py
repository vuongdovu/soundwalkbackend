# =============================================================================
# Django Project Configuration Package
# =============================================================================
# This package contains all Django configuration including settings, URLs,
# ASGI/WSGI applications, and Celery configuration.
#
# Import Celery app to ensure it's loaded when Django starts.
# This is required for Celery to auto-discover tasks in all installed apps.
# =============================================================================

from config.celery import app as celery_app

__all__ = ("celery_app",)
