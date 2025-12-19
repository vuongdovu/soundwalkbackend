"""
Create PostgreSQL trigger for automatic search_vector updates.

This migration:
1. Creates a trigger function that updates search_vector on INSERT/UPDATE
2. Attaches the trigger to the chat_message table
3. Backfills search_vector for existing text messages

Trigger Behavior:
    - Only updates search_vector for text messages (message_type = 'text')
    - System messages get search_vector = NULL
    - Uses 'english' text search configuration
    - Weight 'A' (highest) for message content
"""

from django.db import migrations


# SQL for creating the trigger function
CREATE_TRIGGER_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION chat_message_search_vector_trigger()
RETURNS trigger AS $$
BEGIN
    IF NEW.message_type = 'text' THEN
        NEW.search_vector := setweight(
            to_tsvector('english', COALESCE(NEW.content, '')),
            'A'
        );
    ELSE
        NEW.search_vector := NULL;
    END IF;
    RETURN NEW;
END
$$ LANGUAGE plpgsql;
"""

# SQL for creating the trigger
CREATE_TRIGGER_SQL = """
CREATE TRIGGER chat_message_search_vector_update
    BEFORE INSERT OR UPDATE OF content, message_type
    ON chat_message
    FOR EACH ROW
    EXECUTE FUNCTION chat_message_search_vector_trigger();
"""

# SQL for backfilling existing text messages
BACKFILL_SQL = """
UPDATE chat_message
SET search_vector = setweight(
    to_tsvector('english', COALESCE(content, '')),
    'A'
)
WHERE message_type = 'text' AND search_vector IS NULL;
"""

# Reverse SQL
DROP_TRIGGER_SQL = (
    "DROP TRIGGER IF EXISTS chat_message_search_vector_update ON chat_message;"
)
DROP_FUNCTION_SQL = "DROP FUNCTION IF EXISTS chat_message_search_vector_trigger();"


def create_search_trigger(apps, schema_editor):
    """Create the trigger function, trigger, and backfill existing data."""
    if schema_editor.connection.vendor != "postgresql":
        return

    # Create trigger function
    schema_editor.execute(CREATE_TRIGGER_FUNCTION_SQL)

    # Create trigger
    schema_editor.execute(CREATE_TRIGGER_SQL)

    # Backfill existing text messages
    schema_editor.execute(BACKFILL_SQL)


def remove_search_trigger(apps, schema_editor):
    """Remove the trigger and function."""
    if schema_editor.connection.vendor != "postgresql":
        return

    schema_editor.execute(DROP_TRIGGER_SQL)
    schema_editor.execute(DROP_FUNCTION_SQL)


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0003_message_search_vector"),
    ]

    operations = [
        migrations.RunPython(
            create_search_trigger,
            remove_search_trigger,
        ),
    ]
