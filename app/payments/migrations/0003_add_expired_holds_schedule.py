"""
Add celery-beat schedule for processing expired escrow holds.

This migration creates the periodic task schedule for the
process_expired_holds task, which runs every 15 minutes to
check for and release expired FundHolds.
"""

from django.db import migrations


def create_periodic_task(apps, schema_editor):
    """Create the periodic task for processing expired holds."""
    IntervalSchedule = apps.get_model("django_celery_beat", "IntervalSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

    # Create interval schedule: every 15 minutes
    schedule, _ = IntervalSchedule.objects.get_or_create(
        every=15,
        period="minutes",
    )

    # Create periodic task
    PeriodicTask.objects.get_or_create(
        name="Process Expired Escrow Holds",
        defaults={
            "task": "payments.workers.hold_manager.process_expired_holds",
            "interval": schedule,
            "enabled": True,
            "description": (
                "Scans for expired FundHolds and queues release tasks. "
                "Auto-releases escrowed funds to recipients when holds expire."
            ),
        },
    )


def remove_periodic_task(apps, schema_editor):
    """Remove the periodic task on migration rollback."""
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

    PeriodicTask.objects.filter(
        name="Process Expired Escrow Holds",
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        (
            "payments",
            "0002_connectedaccount_paymentorder_payout_fundhold_refund_and_more",
        ),
        ("django_celery_beat", "0019_alter_periodictasks_options"),
    ]

    operations = [
        migrations.RunPython(create_periodic_task, remove_periodic_task),
    ]
