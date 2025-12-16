# Generated manually for versioning implementation

"""
Data migration to set version_group=self for all existing MediaFile records.

This migration must run BEFORE the schema migration (0004) that makes
version_group non-nullable. It ensures all existing files have a valid
version_group pointing to themselves (the self-referential pattern).

Why self-referential? NULL values don't participate in unique constraints
the way we need. By having all files point to themselves (originals) or
their version group root, we can enforce constraints like "only one
current version per group" at the database level.
"""

from django.db import migrations


def populate_version_groups(apps, schema_editor):
    """
    Set version_group to self for all records where it's NULL.

    This is safe to run multiple times - only affects NULL values.
    """
    MediaFile = apps.get_model("media", "MediaFile")

    # Get all MediaFile records with NULL version_group
    null_version_group = MediaFile.objects.filter(version_group__isnull=True)

    # Update each to point to itself
    # Using a loop instead of bulk_update because we need F('pk')
    # and the self-reference requires the instance to exist first
    for media_file in null_version_group.iterator():
        media_file.version_group = media_file
        media_file.save(update_fields=["version_group"])


def reverse_populate(apps, schema_editor):
    """
    Reverse migration: set version_group to NULL where it points to self.

    This only reverses the data migration effect - files that were
    already part of a version chain (pointing to another file) are
    left unchanged.
    """
    MediaFile = apps.get_model("media", "MediaFile")

    # Find files where version_group points to self (pk == version_group_id)
    # These are the ones we set in the forward migration
    from django.db.models import F

    self_referencing = MediaFile.objects.filter(version_group=F("pk"))

    for media_file in self_referencing.iterator():
        media_file.version_group = None
        media_file.save(update_fields=["version_group"])


class Migration(migrations.Migration):
    """
    Data migration to populate version_group field.

    Dependencies:
        - 0002_add_media_asset_model: Previous migration
    """

    dependencies = [
        ("media", "0002_add_media_asset_model"),
    ]

    operations = [
        migrations.RunPython(
            populate_version_groups,
            reverse_populate,
            elidable=True,  # Can be optimized away in squashed migrations
        ),
    ]
