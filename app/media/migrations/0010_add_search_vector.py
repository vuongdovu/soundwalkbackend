"""
Add search_vector field with GIN index for PostgreSQL full-text search.

This migration:
1. Adds SearchVectorField to MediaFile model
2. Creates GIN index for fast full-text search
3. Populates initial vectors from original_filename (data migration)
"""

from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.db import migrations


def populate_search_vectors(apps, schema_editor):
    """
    Populate search_vector with original_filename for existing records.

    Uses raw SQL for efficiency on large tables. Weight A (highest) is used
    for filenames as they should rank highest in search results.
    """
    if schema_editor.connection.vendor != "postgresql":
        return

    schema_editor.execute("""
        UPDATE media_mediafile
        SET search_vector = setweight(
            to_tsvector('english', COALESCE(original_filename, '')),
            'A'
        )
        WHERE search_vector IS NULL
    """)


def reverse_populate_search_vectors(apps, schema_editor):
    """No-op reverse - field will be removed anyway."""
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("media", "0009_add_celery_beat_schedules"),
    ]

    operations = [
        # Add SearchVectorField
        migrations.AddField(
            model_name="mediafile",
            name="search_vector",
            field=SearchVectorField(null=True, blank=True),
        ),
        # Add GIN index for fast full-text search
        migrations.AddIndex(
            model_name="mediafile",
            index=GinIndex(
                fields=["search_vector"],
                name="idx_media_search_vector",
            ),
        ),
        # Populate initial vectors (filename only)
        migrations.RunPython(
            populate_search_vectors,
            reverse_populate_search_vectors,
        ),
    ]
