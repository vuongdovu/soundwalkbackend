"""
Add Celery Beat schedules for media maintenance tasks.

This migration creates periodic task schedules for:
- Processing queue management (stuck, failed, pending rescans)
- Health monitoring (ClamAV)
- Cleanup tasks (expired uploads, orphaned files, soft-deleted files)
- Quota maintenance (recalculation)
"""

from django.db import migrations


def create_periodic_tasks(apps, schema_editor):
    """Create periodic tasks for media maintenance."""
    IntervalSchedule = apps.get_model("django_celery_beat", "IntervalSchedule")
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

    # =========================================================================
    # Interval Schedules
    # =========================================================================

    # Every 5 minutes
    schedule_5min, _ = IntervalSchedule.objects.get_or_create(
        every=5,
        period="minutes",
    )

    # Every 15 minutes
    schedule_15min, _ = IntervalSchedule.objects.get_or_create(
        every=15,
        period="minutes",
    )

    # Every 30 minutes
    schedule_30min, _ = IntervalSchedule.objects.get_or_create(
        every=30,
        period="minutes",
    )

    # Every 1 hour
    schedule_1hour, _ = IntervalSchedule.objects.get_or_create(
        every=1,
        period="hours",
    )

    # Every 6 hours
    schedule_6hours, _ = IntervalSchedule.objects.get_or_create(
        every=6,
        period="hours",
    )

    # =========================================================================
    # Crontab Schedules (for specific times)
    # =========================================================================

    # Daily at 2 AM UTC
    crontab_daily_2am, _ = CrontabSchedule.objects.get_or_create(
        minute="0",
        hour="2",
        day_of_week="*",
        day_of_month="*",
        month_of_year="*",
    )

    # Weekly on Sunday at 3 AM UTC
    crontab_weekly_sun_3am, _ = CrontabSchedule.objects.get_or_create(
        minute="0",
        hour="3",
        day_of_week="0",  # Sunday
        day_of_month="*",
        month_of_year="*",
    )

    # =========================================================================
    # Periodic Tasks - Processing Queue Management
    # =========================================================================

    # Reset stuck processing jobs
    PeriodicTask.objects.get_or_create(
        name="Media: Cleanup Stuck Processing",
        defaults={
            "task": "media.tasks.cleanup_stuck_processing",
            "interval": schedule_30min,
            "enabled": True,
            "description": (
                "Resets files stuck in PROCESSING status for more than 30 minutes. "
                "Handles worker crashes and timeouts."
            ),
        },
    )

    # Retry failed processing jobs
    PeriodicTask.objects.get_or_create(
        name="Media: Retry Failed Processing",
        defaults={
            "task": "media.tasks.retry_failed_processing",
            "interval": schedule_15min,
            "enabled": True,
            "description": (
                "Re-queues failed media file processing jobs that haven't "
                "exceeded the maximum retry count."
            ),
        },
    )

    # Rescan files skipped due to scanner outage
    PeriodicTask.objects.get_or_create(
        name="Media: Rescan Skipped Files",
        defaults={
            "task": "media.tasks.rescan_skipped_files",
            "interval": schedule_5min,
            "enabled": True,
            "description": (
                "Re-queues files that were skipped during malware scanning "
                "due to scanner unavailability (fail-open behavior)."
            ),
        },
    )

    # =========================================================================
    # Periodic Tasks - Health Monitoring
    # =========================================================================

    # Check antivirus health
    PeriodicTask.objects.get_or_create(
        name="Media: Check Antivirus Health",
        defaults={
            "task": "media.tasks.check_antivirus_health",
            "interval": schedule_1hour,
            "enabled": True,
            "description": (
                "Checks ClamAV connectivity, circuit breaker state, and "
                "virus definition freshness. Logs warnings on issues."
            ),
        },
    )

    # =========================================================================
    # Periodic Tasks - Cleanup
    # =========================================================================

    # Cleanup expired upload sessions
    PeriodicTask.objects.get_or_create(
        name="Media: Cleanup Expired Upload Sessions",
        defaults={
            "task": "media.tasks.cleanup_expired_upload_sessions",
            "interval": schedule_1hour,
            "enabled": True,
            "description": (
                "Cleans up expired chunked upload sessions, aborting S3 "
                "multipart uploads and removing local temp directories."
            ),
        },
    )

    # Cleanup orphaned local temp directories
    PeriodicTask.objects.get_or_create(
        name="Media: Cleanup Orphaned Temp Directories",
        defaults={
            "task": "media.tasks.cleanup_orphaned_local_temp_dirs",
            "interval": schedule_6hours,
            "enabled": True,
            "description": (
                "Safety net cleanup for local temp directories without matching "
                "upload sessions. Handles app crashes and edge cases."
            ),
        },
    )

    # Cleanup orphaned S3 multipart uploads
    PeriodicTask.objects.get_or_create(
        name="Media: Cleanup Orphaned S3 Multipart Uploads",
        defaults={
            "task": "media.tasks.cleanup_orphaned_s3_multipart_uploads",
            "interval": schedule_6hours,
            "enabled": True,
            "description": (
                "Safety net cleanup for S3 multipart uploads without matching "
                "upload sessions. Prevents S3 storage accumulation."
            ),
        },
    )

    # Hard delete expired soft-deleted files
    PeriodicTask.objects.get_or_create(
        name="Media: Hard Delete Expired Files",
        defaults={
            "task": "media.tasks.hard_delete_expired_files",
            "crontab": crontab_daily_2am,
            "enabled": True,
            "description": (
                "Permanently deletes soft-deleted files past the retention period "
                "(default 30 days). Removes files, assets, shares, and tags."
            ),
        },
    )

    # =========================================================================
    # Periodic Tasks - Quota Maintenance
    # =========================================================================

    # Recalculate all storage quotas
    PeriodicTask.objects.get_or_create(
        name="Media: Recalculate All Storage Quotas",
        defaults={
            "task": "media.tasks.recalculate_all_storage_quotas",
            "crontab": crontab_weekly_sun_3am,
            "enabled": True,
            "description": (
                "Weekly recalculation of all user storage quotas from actual "
                "file sizes. Fixes quota drift from edge cases and bugs."
            ),
        },
    )


def remove_periodic_tasks(apps, schema_editor):
    """Remove all media periodic tasks on migration rollback."""
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

    task_names = [
        "Media: Cleanup Stuck Processing",
        "Media: Retry Failed Processing",
        "Media: Rescan Skipped Files",
        "Media: Check Antivirus Health",
        "Media: Cleanup Expired Upload Sessions",
        "Media: Cleanup Orphaned Temp Directories",
        "Media: Cleanup Orphaned S3 Multipart Uploads",
        "Media: Hard Delete Expired Files",
        "Media: Recalculate All Storage Quotas",
    ]

    PeriodicTask.objects.filter(name__in=task_names).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("media", "0008_add_partial_indexes"),
        ("django_celery_beat", "0019_alter_periodictasks_options"),
    ]

    operations = [
        migrations.RunPython(create_periodic_tasks, remove_periodic_tasks),
    ]
