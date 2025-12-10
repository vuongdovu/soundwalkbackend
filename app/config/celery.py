"""
Celery configuration for the Django application.

Celery is a distributed task queue that enables:
- Background task processing (email sending, data processing)
- Scheduled/periodic tasks (daily reports, cleanup jobs)
- Async task execution without blocking web requests

This configuration uses Redis as both the message broker and result backend.
Tasks are auto-discovered from all installed Django apps.

Usage:
    # Define a task in any app's tasks.py:
    from celery import shared_task

    @shared_task
    def send_welcome_email(user_id):
        # Task implementation
        pass

    # Call the task asynchronously:
    send_welcome_email.delay(user.id)

For more information, see:
https://docs.celeryq.dev/en/stable/django/first-steps-with-django.html
"""

import os

from celery import Celery

# Set the default Django settings module for the Celery worker
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

# Create Celery application instance
# The name should match the Django project name
app = Celery("config")

# Load configuration from Django settings
# All Celery settings should be prefixed with CELERY_ in settings.py
app.config_from_object("django.conf:settings", namespace="CELERY")

# Auto-discover tasks from all registered Django apps
# Celery will look for a tasks.py module in each installed app
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """
    Debug task for testing Celery connectivity.

    Usage:
        from config.celery import debug_task
        debug_task.delay()

    Check worker logs to verify task execution.
    """
    print(f"Request: {self.request!r}")
