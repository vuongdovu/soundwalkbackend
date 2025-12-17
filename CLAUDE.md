# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Codebase Exploration

**Always use Serena MCP tools for codebase exploration.** Do not use the built-in Explore agent or raw grep/glob searches. Serena provides semantic code understanding.

Preferred tools for exploration:
- `get_symbols_overview` - High-level view of classes/functions in a file
- `find_symbol` - Search by symbol name path (e.g., `MyClass/my_method`)
- `find_referencing_symbols` - Find all references to a symbol
- `search_for_pattern` - Regex search when symbol name is unknown

## Project Overview

Django 5.2 LTS skeleton with pre-configured SaaS infrastructure: REST API (DRF), JWT auth (dj-rest-auth + allauth), PostgreSQL 16, Redis, Celery, WebSocket (Channels), Stripe, AI providers (OpenAI/Anthropic), push notifications.

## Common Commands

### Development
```bash
# Start all services
docker-compose up --build

# Start detached
docker-compose up -d

# View logs
docker-compose logs -f web
docker-compose logs -f celery-worker
```

### Running Tests
```bash
# Run all tests (from container) - coverage enabled by default
docker-compose exec web pytest

# Run specific test file
docker-compose exec web pytest authentication/tests/test_views.py

# Run specific test class or method
docker-compose exec web pytest authentication/tests/test_views.py::TestLogin::test_login_success

# Run with HTML coverage report
docker-compose exec web pytest --cov-report=html

# Run tests by marker
docker-compose exec web pytest -m "not slow"
docker-compose exec web pytest -m integration
docker-compose exec web pytest -m security
```

### Database & Migrations
```bash
docker-compose exec web python manage.py makemigrations
docker-compose exec web python manage.py migrate
docker-compose exec web python manage.py createsuperuser
```

### Django Management
```bash
docker-compose exec web python manage.py shell
docker-compose exec web python manage.py collectstatic
```

## Architecture

### Directory Structure

```
app/                         # Django project root
├── config/                  # Django settings, URLs, ASGI/WSGI
│   ├── settings.py          # Single settings file (env-driven)
│   └── urls.py              # Root URL configuration
├── core/                    # Domain-agnostic infrastructure (see app/core/CLAUDE.md)
├── toolkit/                 # Domain-aware SaaS utilities (see app/toolkit/CLAUDE.md)
├── authentication/          # User, Profile, LinkedAccount, social auth, biometric
├── payments/                # Stripe integration, escrow, subscriptions, ledger
├── notifications/           # Push notifications (FCM)
├── chat/                    # Real-time messaging (Channels)
└── ai/                      # AI provider integrations
```

### Core vs Toolkit

**`core/`** - Portable infrastructure with no domain knowledge:
- `BaseModel` with timestamps, `SoftDeleteMixin`, `UUIDPrimaryKeyMixin`
- `ServiceResult` pattern for service layer returns
- `BaseService` with transaction handling
- Exception hierarchy (`ValidationError`, `NotFoundError`, etc.)
- Generic decorators (`rate_limit`, `cache_response`)

**`toolkit/`** - Business-aware utilities:
- Protocols for external services (`EmailSender`, `PaymentProcessor`)
- PII handling (`mask_email`, `mask_phone`)
- Subscription decorators (`require_subscription`)

### Key Architectural Patterns

**Service Layer with ServiceResult:** All business logic lives in service classes that return `ServiceResult[T]` objects:
```python
result = SomeService.do_action(params)
if result.success:
    return result.data
else:
    # result.error contains error code and message
```

**State Machines:** Payment entities (PaymentOrder, Subscription, Payout, Refund) use state machine transitions. Always use transition methods, never set state directly.

**Strategy Pattern (Payments):** Payment types use `PaymentOrchestrator` which selects the appropriate strategy:
- `DirectPaymentStrategy` - Immediate settlement
- `EscrowPaymentStrategy` - Held funds with release workflow
- `SubscriptionPaymentStrategy` - Recurring billing

**Double-Entry Ledger:** All financial operations create balanced ledger entries (debit + credit). Account types: `USER_BALANCE`, `PLATFORM_ESCROW`, `PLATFORM_REVENUE`, `EXTERNAL_STRIPE`.

### API Structure

All API routes under `/api/v1/`:
- `/auth/` - Authentication (dj-rest-auth + custom endpoints)
- `/payments/` - Stripe payments
- `/notifications/` - Push notifications
- `/chat/` - Messaging
- `/ai/` - AI completions

### Authentication Model

Custom User model (`authentication.User`) with email-only authentication (no username). Profile stored separately. Social auth via allauth (Google, Apple). Biometric auth via ECDSA signatures with Redis-backed challenges.

### Test Configuration

- pytest-django with PostgreSQL (same as production)
- Throttling disabled during tests (in `conftest.py`)
- Fast password hasher (MD5) for test speed
- Markers: `@pytest.mark.slow`, `@pytest.mark.integration`, `@pytest.mark.security`
- App-specific fixtures in `app/{app}/tests/conftest.py`

### Docker Services

| Service | Container | Purpose |
|---------|-----------|---------|
| web | app-web | Django + Uvicorn (port 8080) |
| db | app-db | PostgreSQL 16 |
| redis | app-redis | Cache + Celery broker |
| nginx | app-nginx | Reverse proxy (port 80) |
| celery-worker | app-celery-worker | Background tasks |
| celery-beat | app-celery-beat | Scheduled tasks |

### Configuration

Settings driven by environment variables (django-environ). See `.env.example` for all options. Environment files:
- `.env.development` - Local development
- `.env.production` - Production deployment
