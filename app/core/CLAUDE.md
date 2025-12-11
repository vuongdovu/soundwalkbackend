# Core - Domain-Agnostic Infrastructure

This directory contains infrastructure code that has **no knowledge of what kind of application you are building**. Everything here should be portable—a developer could copy the entire `core/` directory into a completely different project (e-commerce, social network, CRM) and use it unchanged.

## What Belongs Here

Code that solves universal problems every Django application faces:

- **Base classes** that other models inherit from (timestamps, soft deletion)
- **Generic mixins** that add reusable functionality (ordering, slugs, metadata)
- **Service patterns** for returning success/failure from methods (`ServiceResult`)
- **Exception hierarchies** for structured error responses
- **ViewSet utilities** for common API patterns (pagination, bulk actions)
- **Protocols** that define interfaces for infrastructure concerns (`CacheBackend`)
- **Decorators** that implement pure mechanisms (`rate_limit`, `cache_response`, `log_request`)
- **Cryptographic utilities** (token generation, hashing)
- **Format validators** that check structure, not business meaning (file size, JSON schema, XSS prevention)
- **Helper functions** that perform arithmetic or format checking (pagination calculation, UUID validation)

## What Does NOT Belong Here

Any code that references domain-specific concepts:

- Users, profiles, accounts
- Subscriptions, tiers, plans
- Payments, invoices, credits
- Any business model or entity
- Feature flags tied to subscription levels
- Business rules or policies

## Red Flags

If you find yourself doing any of these inside `core/`, the code is in the wrong place:

```python
# WRONG - importing a business model
from authentication.models import User

# WRONG - checking subscription status
if user.subscription.is_active:

# WRONG - referencing a tier
if plan_name in ["pro", "enterprise"]:

# WRONG - business-aware validation
def validate_premium_feature(user):
```

## Key Files

| File | Purpose |
|------|---------|
| `models.py` | `BaseModel` with timestamps |
| `model_mixins.py` | `SoftDeleteMixin`, `UUIDPrimaryKeyMixin`, `SlugMixin`, `OrderableMixin`, `MetadataMixin` |
| `managers.py` | `SoftDeleteManager`, `BaseQuerySet` |
| `services.py` | `BaseService`, `ServiceResult` |
| `exceptions.py` | `ValidationError`, `NotFoundError`, `PermissionDeniedError`, etc. |
| `protocols.py` | `CacheBackend` |
| `decorators.py` | `rate_limit`, `cache_response`, `log_request` |
| `helpers.py` | `generate_token`, `hash_string`, `validate_uuid`, `calculate_pagination`, `get_client_ip` |
| `validators.py` | `validate_file_size`, `validate_file_extension`, `validate_no_html`, `validate_json_schema` |
| `serializer_mixins.py` | `TimestampMixin` |
| `viewset_mixins.py` | `PaginationMixin`, `BulkActionMixin`, `SoftDeleteViewSetMixin` |

## The Test

Before adding code to `core/`, ask: "Would this code make sense in a completely different application?"

- If yes → it belongs in `core/`
- If no → it belongs in `toolkit/` or a domain app
