"""
Create MessageEditHistory model for optional full edit audit trail.

This model stores the complete history of message edits, allowing
users to see all previous versions of an edited message.

Note: This is optional/complementary to the edit_count and original_content
fields on Message. Those fields provide quick "was edited?" checks while
this model provides full history.
"""

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0005_message_reactions"),
    ]

    operations = [
        migrations.CreateModel(
            name="MessageEditHistory",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                        db_index=True,
                        help_text="Timestamp when this record was created",
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        auto_now=True,
                        help_text="Timestamp when this record was last modified",
                    ),
                ),
                (
                    "content",
                    models.TextField(
                        help_text="Message content at this edit version",
                    ),
                ),
                (
                    "edit_number",
                    models.PositiveSmallIntegerField(
                        help_text="Sequential edit number (1 = first edit, 2 = second, etc.)",
                    ),
                ),
                (
                    "message",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="edit_history",
                        to="chat.message",
                        help_text="Message this edit belongs to",
                    ),
                ),
            ],
            options={
                "db_table": "chat_message_edit_history",
                "ordering": ["-created_at"],
                "verbose_name": "Message Edit History",
                "verbose_name_plural": "Message Edit Histories",
            },
        ),
        # Unique constraint: one entry per edit number per message
        migrations.AddConstraint(
            model_name="messageedithistory",
            constraint=models.UniqueConstraint(
                fields=["message", "edit_number"],
                name="unique_message_edit_number",
            ),
        ),
        # Index for querying edit history by message (most recent first)
        migrations.AddIndex(
            model_name="messageedithistory",
            index=models.Index(
                fields=["message", "-created_at"],
                name="chat_edit_hist_msg_recent_idx",
            ),
        ),
    ]
