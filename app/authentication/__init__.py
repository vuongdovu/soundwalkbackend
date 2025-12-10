"""
Authentication application.

This app provides user authentication, profile management, OAuth integration,
and token-based verification functionality for the application.

Key components:
    - User model: Custom email-based user authentication
    - Profile model: Extended user profile data
    - AuthService: Business logic for auth operations
    - OAuth adapters: Google and Apple social authentication

Usage:
    from authentication.models import User, Profile
    from authentication.services import AuthService
"""

default_app_config = "authentication.apps.AuthenticationConfig"
