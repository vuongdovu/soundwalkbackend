"""
Service classes for toolkit app.

This package contains service classes for common operations:
- EmailService: Email sending with template support

Usage:
    from toolkit.services import EmailService
    from toolkit.services.email import EmailService  # Alternative import
"""

from toolkit.services.email import EmailService

__all__ = ["EmailService"]
