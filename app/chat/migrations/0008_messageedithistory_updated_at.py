"""
Add missing updated_at field to MessageEditHistory.

This field was inadvertently omitted from migration 0006. BaseModel includes
both created_at and updated_at, but 0006 only included created_at.
"""

from django.db import migrations, models


def set_default_updated_at(apps, schema_editor):
    """Set updated_at to created_at for existing records."""
    schema_editor.execute(
        "UPDATE chat_message_edit_history SET updated_at = created_at WHERE updated_at IS NULL"
    )


def noop(apps, schema_editor):
    """No-op reverse."""
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0007_message_attachments"),
    ]

    operations = [
        # First add as nullable
        migrations.AddField(
            model_name="messageedithistory",
            name="updated_at",
            field=models.DateTimeField(
                auto_now=True,
                help_text="Timestamp when this record was last modified",
                null=True,  # Temporarily nullable
            ),
        ),
        # Set defaults for existing rows
        migrations.RunPython(set_default_updated_at, noop),
        # Then alter to not null (auto_now handles this automatically)
        migrations.AlterField(
            model_name="messageedithistory",
            name="updated_at",
            field=models.DateTimeField(
                auto_now=True,
                help_text="Timestamp when this record was last modified",
            ),
        ),
    ]
