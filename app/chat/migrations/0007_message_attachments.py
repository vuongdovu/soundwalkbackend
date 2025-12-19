"""
Add attachments field to Message via M2M to media.MediaFile.

Design Decision:
    Instead of creating a new MessageAttachment model, we reuse the existing
    MediaFile model via ManyToManyField. This leverages existing infrastructure:
    - S3 storage configuration
    - Malware scanning (ClamAV)
    - Thumbnail generation
    - File type validation
    - Access control
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0006_message_edit_history"),
        ("media", "0011_add_search_reconciliation_schedule"),
    ]

    operations = [
        migrations.AddField(
            model_name="message",
            name="attachments",
            field=models.ManyToManyField(
                to="media.mediafile",
                blank=True,
                related_name="chat_messages",
                help_text="Media files attached to this message",
            ),
        ),
    ]
