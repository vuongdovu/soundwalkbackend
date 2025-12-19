"""
Add message editing fields to Message model.

Fields added:
    - edited_at: Timestamp of last edit (null if never edited)
    - edit_count: Number of times message has been edited (default 0)
    - original_content: Original message content before any edits (blank for unedited)
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="message",
            name="edited_at",
            field=models.DateTimeField(
                null=True,
                blank=True,
                help_text="Timestamp when message was last edited",
            ),
        ),
        migrations.AddField(
            model_name="message",
            name="edit_count",
            field=models.PositiveSmallIntegerField(
                default=0,
                help_text="Number of times this message has been edited",
            ),
        ),
        migrations.AddField(
            model_name="message",
            name="original_content",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Original content before any edits (empty if never edited)",
            ),
        ),
    ]
