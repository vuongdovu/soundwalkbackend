# Generated manually for versioning implementation

"""
Schema migration to make version_group non-nullable and add versioning constraints.

This migration:
1. Alters version_group from nullable/SET_NULL to non-nullable/CASCADE
2. Adds UniqueConstraint for one current version per group
3. Adds UniqueConstraint for unique version numbers within a group

IMPORTANT: Migration 0003 (data migration) MUST run first to ensure
all existing records have a valid version_group value.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Schema migration for versioning constraints.

    Dependencies:
        - 0003_populate_version_group: Data migration that sets version_group values
    """

    dependencies = [
        ("media", "0003_populate_version_group"),
    ]

    operations = [
        # Step 1: Alter the version_group field
        # Change from nullable/SET_NULL to non-nullable/CASCADE
        migrations.AlterField(
            model_name="mediafile",
            name="version_group",
            field=models.ForeignKey(
                help_text="Reference to the original file in a version chain",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="versions",
                to="media.mediafile",
            ),
        ),
        # Step 2: Add constraint - only one current version per group
        # Uses partial unique index: UNIQUE(version_group) WHERE is_current = TRUE
        migrations.AddConstraint(
            model_name="mediafile",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_current", True)),
                fields=("version_group",),
                name="media_file_unique_current_per_group",
            ),
        ),
        # Step 3: Add constraint - unique version numbers within a group
        # UNIQUE(version_group, version)
        migrations.AddConstraint(
            model_name="mediafile",
            constraint=models.UniqueConstraint(
                fields=("version_group", "version"),
                name="media_file_unique_version_in_group",
            ),
        ),
    ]
