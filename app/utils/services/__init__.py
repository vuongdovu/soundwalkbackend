"""
Service classes for utils app.

This package contains service classes for common operations:
- EmailService: Email sending with template support

Usage:
    from utils.services import EmailService
    from utils.services.email import EmailService  # Alternative import
"""

from utils.services.email import EmailService

__all__ = ["EmailService"]
