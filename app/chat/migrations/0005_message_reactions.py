"""
Add reaction support to messages.

Changes:
    - Add reaction_counts JSONField to Message for cached counts
    - Create MessageReaction model for storing individual reactions
    - Add appropriate indexes and constraints
"""

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0004_message_search_trigger"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # Add reaction_counts JSONField to Message for cached aggregates
        # Format: {"👍": 3, "❤️": 5}
        migrations.AddField(
            model_name="message",
            name="reaction_counts",
            field=models.JSONField(
                default=dict,
                blank=True,
                help_text="Cached count of reactions by emoji (e.g., {'👍': 3, '❤️': 5})",
            ),
        ),
        # Create MessageReaction model
        migrations.CreateModel(
            name="MessageReaction",
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
                    "emoji",
                    models.CharField(
                        max_length=8,
                        help_text="Emoji character(s) used for this reaction",
                    ),
                ),
                (
                    "message",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="reactions",
                        to="chat.message",
                        help_text="Message this reaction belongs to",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="message_reactions",
                        to=settings.AUTH_USER_MODEL,
                        help_text="User who added this reaction",
                    ),
                ),
            ],
            options={
                "db_table": "chat_message_reaction",
                "ordering": ["-created_at"],
            },
        ),
        # Unique constraint: one reaction per user per emoji per message
        migrations.AddConstraint(
            model_name="messagereaction",
            constraint=models.UniqueConstraint(
                fields=["message", "user", "emoji"],
                name="unique_user_message_emoji_reaction",
            ),
        ),
        # Index for querying reactions by message and emoji (for aggregation)
        migrations.AddIndex(
            model_name="messagereaction",
            index=models.Index(
                fields=["message", "emoji"],
                name="chat_reaction_msg_emoji_idx",
            ),
        ),
        # Index for querying a user's recent reactions
        migrations.AddIndex(
            model_name="messagereaction",
            index=models.Index(
                fields=["user", "-created_at"],
                name="chat_reaction_user_recent_idx",
            ),
        ),
    ]
