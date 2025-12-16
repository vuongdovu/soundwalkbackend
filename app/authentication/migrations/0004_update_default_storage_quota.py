# Generated manually - Update default storage quota from 5GB to 25GB

from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Update the default storage quota from 5GB to 25GB.

    This only changes the default value for new profiles - existing profiles
    keep their current quota (which would be 5GB if they were created before
    this migration).

    To update existing profiles to the new default, a data migration would be
    needed (not included here as it may not be desired for all deployments).
    """

    dependencies = [
        ("authentication", "0003_add_storage_quota_fields"),
    ]

    operations = [
        migrations.AlterField(
            model_name="profile",
            name="storage_quota_bytes",
            field=models.BigIntegerField(
                default=26843545600,  # 25 * 1024 * 1024 * 1024 = 25GB
                help_text="Maximum storage allowed for this user in bytes (default 25GB)",
            ),
        ),
    ]
