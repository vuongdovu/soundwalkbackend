"""
Chat app for real-time messaging.

This app handles:
- Conversations (direct and group)
- Message sending and history
- WebSocket real-time updates
- Read receipts and typing indicators
- AI chat integration

Related apps:
    - authentication: User model for participants
    - notifications: Message notifications
    - ai: AI chat conversations

WebSocket Support:
    Uses Django Channels for real-time communication.
    See consumers.py for WebSocket handlers.
    See routing.py for WebSocket URL patterns.

Usage:
    from chat.services import ChatService

    # Create conversation
    conversation = ChatService.create_conversation(
        creator=user,
        participants=[user, other_user],
        conversation_type="direct",
    )

    # Send message
    message = ChatService.send_message(
        conversation=conversation,
        sender=user,
        content="Hello!",
    )
"""
