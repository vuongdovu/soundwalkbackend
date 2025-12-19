"""
Constants and configuration for chat module features.

This module centralizes configuration values for:
- Message operations (editing, search, content limits)
- Attachment handling (via MediaFile infrastructure)
- Reaction management (emoji restrictions, limits)

These values can be overridden via Django settings if needed.
Import example:
    from chat.constants import MESSAGE_CONFIG, REACTION_CONFIG
"""

from typing import Final


# =============================================================================
# Message Configuration
# =============================================================================


class MESSAGE_CONFIG:
    """Configuration for message operations."""

    # Content limits
    MAX_CONTENT_LENGTH: Final[int] = 10000  # Characters
    MIN_CONTENT_LENGTH: Final[int] = 1

    # Edit settings
    EDIT_TIME_LIMIT_SECONDS: Final[int] = 900  # 15 minutes
    MAX_EDIT_COUNT: Final[int] = 10  # Maximum times a message can be edited
    PRESERVE_ORIGINAL_CONTENT: Final[bool] = (
        True  # Store original in original_content field
    )

    # Search settings
    SEARCH_MIN_QUERY_LENGTH: Final[int] = 2
    SEARCH_MAX_RESULTS: Final[int] = 100
    SEARCH_DEFAULT_PAGE_SIZE: Final[int] = 20


# =============================================================================
# Attachment Configuration
# =============================================================================


class ATTACHMENT_CONFIG:
    """
    Configuration for message attachments.

    Chat attachments reuse the MediaFile infrastructure from app/media/,
    which provides S3 storage, malware scanning, and thumbnail generation.
    """

    # Limits
    MAX_ATTACHMENTS_PER_MESSAGE: Final[int] = 10

    # Allowed media types (subset of MediaFile.MediaType)
    # These correspond to MediaFile.MediaType choices
    ALLOWED_MEDIA_TYPES: Final[tuple] = ("image", "video", "document", "audio")

    # Visibility for chat attachments
    # "private" means only conversation participants can access
    DEFAULT_VISIBILITY: Final[str] = "private"


# =============================================================================
# Reaction Configuration
# =============================================================================


class REACTION_CONFIG:
    """Configuration for message reactions."""

    # Emoji restrictions
    MAX_REACTIONS_PER_MESSAGE: Final[int] = 20  # Total unique emojis per message
    MAX_USER_REACTIONS_PER_MESSAGE: Final[int] = (
        5  # Max reactions from one user per message
    )
    MAX_EMOJI_LENGTH: Final[int] = (
        8  # Max characters for a single emoji (handles compound emojis)
    )

    # Allowed emoji set
    # None = allow any valid emoji
    # Set to tuple of strings to restrict to specific emojis
    ALLOWED_EMOJIS: Final[tuple | None] = None

    # Common quick reactions for UI hints (suggestions only, not restrictions)
    QUICK_REACTIONS: Final[tuple] = ("👍", "❤️", "😂", "😮", "😢", "🎉")


# =============================================================================
# Presence Configuration
# =============================================================================


class PRESENCE_CONFIG:
    """Configuration for presence tracking."""

    # TTL for presence entries (seconds) - how long before considered stale
    PRESENCE_TTL_SECONDS: Final[int] = 60  # 1 minute

    # TTL for conversation presence (seconds)
    CONVERSATION_PRESENCE_TTL_SECONDS: Final[int] = 30  # 30 seconds

    # Redis key prefixes
    KEY_PREFIX_USER_PRESENCE: Final[str] = "presence:user"
    KEY_PREFIX_CONVERSATION_PRESENCE: Final[str] = "presence:conv"

    # Heartbeat settings
    HEARTBEAT_INTERVAL_SECONDS: Final[int] = (
        30  # How often clients should send heartbeat
    )
