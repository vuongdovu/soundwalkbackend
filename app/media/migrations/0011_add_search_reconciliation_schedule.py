"""
Add Celery Beat schedule for search vector reconciliation.

This migration adds a periodic task to recompute search vectors for
documents with extracted text content on a weekly basis.
"""

from django.db import migrations


def create_reconciliation_task(apps, schema_editor):
    """Create the search vector reconciliation periodic task."""
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

    # Weekly on Sunday at 3 AM UTC (same time as quota recalculation)
    crontab_weekly_sun_3am, _ = CrontabSchedule.objects.get_or_create(
        minute="30",  # 3:30 AM to avoid overlap with quota recalculation
        hour="3",
        day_of_week="0",  # Sunday
        day_of_month="*",
        month_of_year="*",
    )

    PeriodicTask.objects.get_or_create(
        name="Media: Reconcile Search Vectors",
        defaults={
            "task": "media.tasks.reconcile_search_vectors",
            "crontab": crontab_weekly_sun_3am,
            "enabled": True,
            "description": (
                "Weekly reconciliation of search vectors for documents with "
                "extracted text content. Ensures vectors include document content "
                "that may have been added after initial upload or processing."
            ),
        },
    )


def remove_reconciliation_task(apps, schema_editor):
    """Remove the search vector reconciliation task on rollback."""
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(name="Media: Reconcile Search Vectors").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("media", "0010_add_search_vector"),
        ("django_celery_beat", "0019_alter_periodictasks_options"),
    ]

    operations = [
        migrations.RunPython(create_reconciliation_task, remove_reconciliation_task),
    ]
