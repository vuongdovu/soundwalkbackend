"""
Add SearchVectorField with GIN index for PostgreSQL full-text search on messages.

This migration adds the search_vector field but does NOT set up the trigger.
The trigger is created in migration 0004 for better separation of concerns.
"""

from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0002_message_edit_fields"),
    ]

    operations = [
        # Add SearchVectorField
        migrations.AddField(
            model_name="message",
            name="search_vector",
            field=SearchVectorField(
                null=True,
                blank=True,
                help_text="PostgreSQL full-text search vector for message content",
            ),
        ),
        # Add GIN index for fast full-text search
        migrations.AddIndex(
            model_name="message",
            index=GinIndex(
                fields=["search_vector"],
                name="chat_msg_search_vector_idx",
            ),
        ),
    ]
