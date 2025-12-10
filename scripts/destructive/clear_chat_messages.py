#!/usr/bin/env python
"""
Script to clear all ChatMessage objects from the database.
This is useful for testing and development purposes.
"""

import os
import sys

import django
from django.db import transaction


# Add the spot directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "spot"))

# Setup Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "app.settings")
django.setup()

from social.models import ChatMessage


def clear_chat_messages():
    """Clear all ChatMessage objects from the database"""
    print("🔍 Checking ChatMessage count...")

    # Get initial count
    initial_count = ChatMessage.objects.count()
    print(f"📊 Found {initial_count} chat messages in database")

    if initial_count == 0:
        print("✅ No chat messages to clear!")
        return

    # Confirm deletion
    confirm = input(
        f"\n⚠️  Are you sure you want to delete ALL {initial_count} chat messages? (yes/no): "
    )

    if confirm.lower() != "yes":
        print("❌ Operation cancelled.")
        return

    print("\n🗑️  Clearing chat messages...")

    try:
        with transaction.atomic():
            # Delete all ChatMessage objects
            deleted_count, deleted_objects = ChatMessage.objects.all().delete()

            print(f"✅ Successfully deleted {deleted_count} chat messages")

            # Show breakdown of deleted objects if available
            if deleted_objects:
                print("\n📋 Deleted objects breakdown:")
                for model, count in deleted_objects.items():
                    print(f"   - {model}: {count}")

    except Exception as e:
        print(f"❌ Error clearing chat messages: {str(e)}")
        return

    # Verify deletion
    final_count = ChatMessage.objects.count()
    print(f"\n📊 Final count: {final_count} chat messages remaining")

    if final_count == 0:
        print("🎉 All chat messages successfully cleared!")
    else:
        print(f"⚠️  Warning: {final_count} messages still remain")


def main():
    print("🧹 Chat Message Cleaner Script")
    print("=" * 40)

    try:
        clear_chat_messages()
    except KeyboardInterrupt:
        print("\n\n⚠️  Operation interrupted by user")
    except Exception as e:
        print(f"\n❌ Unexpected error: {str(e)}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()
