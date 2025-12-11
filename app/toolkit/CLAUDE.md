# Toolkit - Domain-Aware SaaS Utilities

This directory contains utilities that are **aware of your business domain** and exist because you are building a specific kind of application—a SaaS platform with users, subscriptions, external service integrations, and communication needs.

## What Belongs Here

Code that knows about your users and their relationship with your product:

- **Protocols for business actions** (`EmailSender`, `PaymentProcessor`, `NotificationSender`, `MessageBroker`)—because sending an email or processing a payment are not generic operations, they are communications and transactions with real-world consequences tied to your users and revenue model
- **Decorators that make business decisions** (`require_subscription`)—checking subscription tiers, plan levels, feature access
- **PII handling utilities** (`mask_email`, `mask_phone`)—because your application handles personally identifiable information and needs to display or protect it appropriately
- **Analytics and UX helpers** (`parse_user_agent`, `slugify_unique`)—utilities that support understanding user behavior or creating user-facing content
- **Service classes for external providers** (`EmailService`)—orchestrating communication with services that fulfill business needs (email delivery, payment processing, notifications)

## What Does NOT Belong Here

Generic infrastructure that any application needs:

- Base model classes → `core/models.py`
- Soft deletion, timestamps → `core/model_mixins.py`
- Rate limiting, caching mechanisms → `core/decorators.py`
- Token generation, hashing → `core/helpers.py`
- File validation, JSON schema checking → `core/validators.py`
- Exception hierarchies → `core/exceptions.py`

## The Defining Characteristic

Code in `toolkit/` either:

1. **Knows about users** and their relationship with your product, OR
2. **Integrates with external services** that exist to serve business purposes rather than infrastructure purposes

## Key Files

| File | Purpose |
|------|---------|
| `protocols.py` | `EmailSender`, `NotificationSender`, `PaymentProcessor`, `MessageBroker` |
| `decorators.py` | `require_subscription` |
| `helpers.py` | `mask_email`, `mask_phone`, `slugify_unique`, `parse_user_agent` |
| `validators.py` | `validate_phone_number` |
| `services/email.py` | `EmailService` for sending emails |
| `tasks.py` | Async tasks (email sending, etc.) |

## Examples

### This belongs in toolkit

```python
# Business-aware decorator
@require_subscription(["pro", "enterprise"])
def premium_feature(request):
    ...

# PII handling
masked = mask_email("john.doe@example.com")  # "j***@example.com"

# Domain-specific protocol
class StripePaymentProcessor:
    def create_subscription(self, customer_id, price_id): ...
```

### This does NOT belong in toolkit (use core instead)

```python
# Generic rate limiting → core/decorators.py
@rate_limit(key="api", limit=100, period=3600)

# Token generation → core/helpers.py
token = generate_token(32)

# File validation → core/validators.py
validators=[validate_file_size(max_mb=5)]
```

## The Test

Before adding code to `toolkit/`, ask: "Does this code need to know about users, subscriptions, or external business services?"

- If yes → it belongs in `toolkit/`
- If no → it probably belongs in `core/`
