"""
Toolkit - Domain-Specific Utilities & Services.

This app provides domain-aware utilities and services specific to SaaS applications:
- EmailService: Centralized email sending with templates
- Helper functions: PII masking, slug generation, user-agent parsing
- Decorators: Subscription requirement checks
- Validators: Phone number validation
- Protocols: Domain-specific service interfaces (email, payments, notifications)

Key components:
    - services/email.py: EmailService class
    - helpers.py: Domain-aware utility functions (mask_email, mask_phone, slugify_unique)
    - decorators.py: Domain-aware decorators (require_subscription)
    - validators.py: PII validation (validate_phone_number)
    - protocols.py: Domain-specific service interfaces

Usage:
    from toolkit.services.email import EmailService
    from toolkit.helpers import mask_email, slugify_unique, parse_user_agent
    from toolkit.decorators import require_subscription
    from toolkit.protocols import EmailSender, PaymentProcessor

Note:
    - This app has no models. It's focused on domain-specific utilities.
    - For generic infrastructure (tokens, hashing, rate limiting, caching), see core/
    - For model-layer patterns, see core/ (BaseModel, model_mixins, managers).
    - For generic DRF mixins, see core.serializer_mixins and core.viewset_mixins.
"""

default_app_config = "toolkit.apps.ToolkitConfig"
