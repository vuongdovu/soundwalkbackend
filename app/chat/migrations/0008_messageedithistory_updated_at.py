"""
Add missing updated_at field to MessageEditHistory.

This field was inadvertently omitted from migration 0006. BaseModel includes
both created_at and updated_at, but 0006 only included created_at.
"""

from django.db import migrations, models


from django.db import migrations, models

def set_default_updated_at(apps, schema_editor):
    schema_editor.execute(
        "UPDATE chat_message_edit_history SET updated_at = created_at WHERE updated_at IS NULL"
    )

def noop(apps, schema_editor):
    pass

class Migration(migrations.Migration):
    dependencies = [("chat", "0007_message_attachments")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    "ALTER TABLE chat_message_edit_history "
                    "ADD COLUMN IF NOT EXISTS updated_at timestamptz",
                    "ALTER TABLE chat_message_edit_history "
                    "DROP COLUMN IF EXISTS updated_at",
                )
            ],
            state_operations=[
                migrations.AddField(
                    model_name="messageedithistory",
                    name="updated_at",
                    field=models.DateTimeField(
                        auto_now=True,
                        help_text="Timestamp when this record was last modified",
                        null=True,
                    ),
                ),
            ],
        ),
        migrations.RunPython(set_default_updated_at, noop),
        migrations.AlterField(
            model_name="messageedithistory",
            name="updated_at",
            field=models.DateTimeField(
                auto_now=True,
                help_text="Timestamp when this record was last modified",
            ),
        ),
    ]
