"""
Utilities application.

This app provides shared utilities and services used across the application:
- EmailService: Centralized email sending with templates
- Helper functions: Token generation, hashing, validation
- Decorators: Rate limiting, caching, subscription checks
- Mixins: DRF viewset and serializer mixins

Key components:
    - services/email.py: EmailService class
    - helpers.py: Utility functions
    - decorators.py: Custom decorators
    - mixins.py: DRF mixins

Usage:
    from utils.services.email import EmailService
    from utils.helpers import generate_token, mask_email
    from utils.decorators import rate_limit
    from utils.mixins import BulkActionMixin

Note:
    This app has no models. It's a pure utility module.
"""

default_app_config = "utils.apps.UtilsConfig"
