# Project Architecture

> Last generated: 2025-12-22 UTC

## Table of Contents

- [System Overview](#system-overview)
- [System Context](#system-context)
- [App Dependencies](#app-dependencies)
- [Data Model Overview](#data-model-overview)
- [API Structure](#api-structure)
- [App Reference](#app-reference)
- [Quick Links](#quick-links)

---

## System Overview

This is a Django 5.2 LTS SaaS backend skeleton providing pre-configured infrastructure for building modern web applications. The project implements a comprehensive set of features including REST API with JWT authentication, social OAuth (Google, Apple), biometric authentication, real-time chat via WebSockets, payment processing with Stripe (including escrow and subscriptions), push notifications, and background task processing with Celery.

The architecture follows a clean separation between domain-agnostic infrastructure (`core/`) and business-aware utilities (`toolkit/`), making it easy to extend without modifying the skeleton code. The service layer pattern with `ServiceResult` provides consistent error handling across all operations.

### Key Technical Decisions

| Decision | Implementation | Rationale |
|----------|----------------|-----------|
| **Auth Model** | Email-only (no username) | Modern UX, reduces friction |
| **Primary Keys** | UUID for User, auto-increment elsewhere | Security for user-facing IDs |
| **Service Pattern** | ServiceResult with error codes | Consistent API responses |
| **Payment Strategy** | Strategy pattern (Direct/Escrow/Subscription) | Flexible payment types |
| **Financial Tracking** | Double-entry ledger | Audit trail, reconciliation |
| **Real-time** | Django Channels + Redis | Scalable WebSocket support |
| **Background Tasks** | Celery + Redis | Reliable async processing |

---

## System Context

```mermaid
flowchart TD
    subgraph External["External Actors"]
        Users([Mobile/Web Users])
        Admins([Admin Users])
    end

    subgraph ExternalServices["External Services"]
        Google([Google OAuth])
        Apple([Apple Sign-In])
        Stripe([Stripe API])
        FCM([Firebase/APNS])
        Email([Email Service])
    end

    subgraph Infrastructure["Infrastructure"]
        LB[Nginx Reverse Proxy]
        Cache[(Redis Cache)]
        DB[(PostgreSQL 16)]
        Queue[(Redis Queue)]
    end

    subgraph Django["Django Application"]
        API[REST API<br/>Uvicorn ASGI]
        WS[WebSocket<br/>Channels]
        AdminSite[Django Admin]
        Workers[Celery Workers]
        Beat[Celery Beat]
    end

    Users --> LB
    Admins --> LB

    LB --> API
    LB --> WS
    LB --> AdminSite

    API --> Cache
    API --> DB
    API --> Queue

    WS --> Cache
    WS --> DB

    Workers --> Queue
    Workers --> DB
    Workers --> Stripe
    Workers --> FCM
    Workers --> Email

    Beat --> Queue

    API --> Google
    API --> Apple
    API --> Stripe

    Stripe -.->|Webhooks| API
```

---

## App Dependencies

```mermaid
flowchart TD
    subgraph Infrastructure["Infrastructure Layer"]
        core[core<br/><i>Base models, services, exceptions</i>]
        toolkit[toolkit<br/><i>SaaS utilities, protocols</i>]
    end

    subgraph Domain["Domain Layer"]
        auth[authentication<br/><i>Users, profiles, OAuth</i>]
        payments[payments<br/><i>Stripe, escrow, ledger</i>]
        notifications[notifications<br/><i>Push, email, websocket</i>]
        chat[chat<br/><i>Conversations, messages</i>]
        media[media<br/><i>Files, uploads, search</i>]
    end

    subgraph Config["Configuration"]
        config[config<br/><i>Settings, URLs, Celery</i>]
    end

    %% Core dependencies
    auth --> core
    payments --> core
    notifications --> core
    chat --> core
    media --> core

    %% Auth dependencies
    payments --> auth
    notifications --> auth
    chat --> auth
    media --> auth

    %% Toolkit is used by domain apps
    auth -.-> toolkit
    payments -.-> toolkit
    notifications -.-> toolkit

    %% Config orchestrates everything
    config --> auth
    config --> payments
    config --> notifications
    config --> chat
    config --> media

    style core fill:#e1f5fe
    style toolkit fill:#e1f5fe
    style auth fill:#fff3e0
    style payments fill:#fff3e0
    style notifications fill:#fff3e0
    style chat fill:#fff3e0
    style media fill:#fff3e0
    style config fill:#f3e5f5
```

**Arrow Direction:** Dependent app points TO the app it imports from.

---

## Data Model Overview

### Authentication Domain

```mermaid
erDiagram
    User {
        uuid id PK
        string email UK
        boolean is_email_verified
        boolean is_active
    }
    Profile {
        uuid user_id PK,FK
        string username UK
        string first_name
        string last_name
    }
    LinkedAccount {
        int id PK
        uuid user_id FK
        string provider
        string provider_user_id
    }

    User ||--|| Profile : has
    User ||--o{ LinkedAccount : has
```

### Payments Domain

```mermaid
erDiagram
    PaymentOrder {
        uuid id PK
        uuid payer_id FK
        string strategy_type
        string state
        int amount_cents
    }
    Subscription {
        uuid id PK
        uuid payer_id FK
        string state
        string stripe_subscription_id
    }
    ConnectedAccount {
        uuid id PK
        uuid profile_id FK
        string stripe_account_id
        string onboarding_status
    }
    LedgerEntry {
        uuid id PK
        uuid debit_account_id FK
        uuid credit_account_id FK
        int amount_cents
    }

    PaymentOrder }o--|| Subscription : belongs_to
    PaymentOrder ||--o| Payout : has
    Payout }o--|| ConnectedAccount : paid_to
```

### Chat Domain

```mermaid
erDiagram
    Conversation {
        uuid id PK
        string conversation_type
        string title
        int participant_count
    }
    Participant {
        uuid id PK
        uuid conversation_id FK
        uuid user_id FK
        string role
    }
    Message {
        uuid id PK
        uuid conversation_id FK
        uuid sender_id FK
        text content
        string message_type
    }

    Conversation ||--o{ Participant : has
    Conversation ||--o{ Message : contains
    Message ||--o{ Message : replies_to
```

### Notifications Domain

```mermaid
erDiagram
    NotificationType {
        int id PK
        string key UK
        string category
        string title_template
        boolean supports_push
        boolean supports_email
        boolean supports_websocket
    }
    Notification {
        uuid id PK
        int notification_type_id FK
        uuid recipient_id FK
        uuid actor_id FK
        boolean is_read
        string idempotency_key UK
    }
    UserGlobalPreference {
        uuid user_id PK,FK
        boolean all_disabled
    }
    UserCategoryPreference {
        uuid id PK
        uuid user_id FK
        string category
        boolean disabled
    }
    UserNotificationPreference {
        uuid id PK
        uuid user_id FK
        int notification_type_id FK
        boolean disabled
        boolean push_enabled
        boolean email_enabled
    }
    NotificationDelivery {
        uuid id PK
        uuid notification_id FK
        string channel
        string status
        string provider_message_id
    }

    NotificationType ||--o{ Notification : defines
    Notification ||--o{ NotificationDelivery : has
    NotificationType ||--o{ UserNotificationPreference : configures
```

### Media Domain

```mermaid
erDiagram
    MediaFile {
        uuid id PK
        uuid uploader_id FK
        string media_type
        string visibility
        string processing_status
        string scan_status
        int version
        boolean is_current
        uuid version_group_id FK
    }
    MediaAsset {
        uuid id PK
        uuid media_file_id FK
        string asset_type
    }
    MediaFileShare {
        uuid id PK
        uuid media_file_id FK
        uuid shared_with_id FK
        boolean can_download
        datetime expires_at
    }
    Tag {
        uuid id PK
        string name
        string tag_type
        uuid owner_id FK
    }
    UploadSession {
        uuid id PK
        uuid uploader_id FK
        string backend
        string status
    }

    MediaFile ||--o{ MediaAsset : "has"
    MediaFile ||--o{ MediaFileShare : "has"
    MediaFile }o--o{ Tag : "tagged_with"
    MediaFile ||--|| MediaFile : "version_group"
```

---

## API Structure

All API routes are prefixed with `/api/v1/`.

### Authentication (`/api/v1/auth/`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/login/` | POST | Email/password login |
| `/logout/` | POST | Logout (blacklist token) |
| `/registration/` | POST | User registration |
| `/user/` | GET/PUT/PATCH | Current user details |
| `/password/reset/` | POST | Request password reset |
| `/password/change/` | POST | Change password |
| `/profile/` | GET/PUT/PATCH | User profile |
| `/verify-email/` | POST | Verify email with token |
| `/google/` | POST | Google OAuth login |
| `/apple/` | POST | Apple Sign-In login |
| `/biometric/enroll/` | POST | Start biometric enrollment |
| `/biometric/authenticate/` | POST | Biometric authentication |

### Chat (`/api/v1/chat/`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/conversations/` | GET/POST | List/create conversations |
| `/conversations/{id}/` | GET/PATCH/DELETE | Conversation detail |
| `/conversations/{id}/read/` | POST | Mark as read |
| `/conversations/{id}/leave/` | POST | Leave conversation |
| `/conversations/{id}/participants/` | GET/POST | Manage participants |
| `/conversations/{id}/messages/` | GET/POST | List/send messages |

### Notifications (`/api/v1/notifications/`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | List notifications |
| `/{id}/` | GET | Notification detail |
| `/{id}/read/` | POST | Mark as read |
| `/read-all/` | POST | Mark all as read |
| `/unread-count/` | GET | Get unread count |
| `/preferences/` | GET | Get all preferences |
| `/preferences/global/` | PATCH | Update global mute |
| `/preferences/category/` | PATCH | Update category preference |
| `/preferences/type/` | PATCH | Update type preference |
| `/preferences/bulk/` | POST | Bulk update preferences |
| `/preferences/reset/` | POST | Reset preferences |
| `/types/` | GET | List notification types |
| `/types/{key}/` | GET | Get type by key |
| `/webhooks/fcm/` | POST | FCM delivery callback |
| `/webhooks/email/` | POST | Email delivery callback |

### Payments (`/api/v1/payments/`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/webhooks/stripe/` | POST | Stripe webhook receiver |

### Media (`/api/v1/media/`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/search/` | GET | Full-text search with filters |
| `/quota/` | GET | Storage quota status |
| `/upload/` | POST | Standard file upload |
| `/files/{id}/` | GET | File metadata |
| `/files/{id}/download/` | GET | Download file |
| `/files/{id}/view/` | GET | View file inline |
| `/files/{id}/shares/` | GET/POST | Manage file shares |
| `/shared-with-me/` | GET | Files shared with user |
| `/chunked/sessions/` | POST | Create upload session |
| `/chunked/sessions/{id}/finalize/` | POST | Complete chunked upload |
| `/tags/` | GET/POST | List/create tags |
| `/files/{id}/tags/` | GET/POST/DELETE | Manage file tags |
| `/files/by-tags/` | GET | Query files by tags |

### WebSocket Endpoints

| Path | Description |
|------|-------------|
| `ws/chat/{conversation_id}/` | Real-time chat connection |

---

## App Reference

| App | Models | Endpoints | Primary Responsibility |
|-----|--------|-----------|------------------------|
| [`core`](./app/core/ARCHITECTURE.md) | 0 | 0 | Base models, mixins, services, exceptions |
| [`toolkit`](./app/toolkit/ARCHITECTURE.md) | 0 | 0 | SaaS protocols, helpers, decorators |
| [`authentication`](./app/authentication/ARCHITECTURE.md) | 4 | 15+ | User management, OAuth, biometric auth |
| [`payments`](./app/payments/ARCHITECTURE.md) | 8+ | 1 | Stripe payments, escrow, subscriptions, ledger |
| [`notifications`](./app/notifications/ARCHITECTURE.md) | 6 | 15 | Multi-channel notification delivery with preferences |
| [`chat`](./app/chat/ARCHITECTURE.md) | 4 | 12+ | Real-time messaging, WebSocket |
| [`media`](./app/media/ARCHITECTURE.md) | 6 | 25+ | File uploads, storage, sharing, search, tagging |

---

## Quick Links

### By Functionality

| Category | Apps |
|----------|------|
| **Authentication** | [authentication](./app/authentication/ARCHITECTURE.md) |
| **Payments & Billing** | [payments](./app/payments/ARCHITECTURE.md) |
| **Real-time Communication** | [chat](./app/chat/ARCHITECTURE.md) |
| **User Notifications** | [notifications](./app/notifications/ARCHITECTURE.md) |
| **File Management** | [media](./app/media/ARCHITECTURE.md) |
| **Infrastructure** | [core](./app/core/ARCHITECTURE.md), [toolkit](./app/toolkit/ARCHITECTURE.md) |

### Key Entry Points

| Entry Point | Path | Purpose |
|-------------|------|---------|
| API Documentation | `/` | ReDoc OpenAPI docs |
| OpenAPI Schema | `/schema/` | YAML schema download |
| Admin Interface | `/admin/` | Django admin |
| Health Check | `/health/` | Docker/K8s health probe |
| API Root | `/api/v1/` | REST API base |

### Docker Services

| Service | Container | Port | Purpose |
|---------|-----------|------|---------|
| web | app-web | 8080 | Django + Uvicorn |
| db | app-db | 5432 | PostgreSQL 16 |
| redis | app-redis | 6379 | Cache + Celery broker |
| nginx | app-nginx | 80 | Reverse proxy |
| celery-worker | app-celery-worker | - | Background tasks |
| celery-beat | app-celery-beat | - | Scheduled tasks |

---

## Development Commands

```bash
# Start all services
docker-compose up --build

# Run tests
docker-compose exec web pytest

# Run specific test file
docker-compose exec web pytest authentication/tests/test_views.py

# Database migrations
docker-compose exec web python manage.py makemigrations
docker-compose exec web python manage.py migrate

# Django shell
docker-compose exec web python manage.py shell

# View logs
docker-compose logs -f web
docker-compose logs -f celery-worker
```

---

## Configuration

Configuration is driven by environment variables. Key settings:

| Setting | Default | Description |
|---------|---------|-------------|
| `DEBUG` | `False` | Django debug mode |
| `SECRET_KEY` | - | Django secret key |
| `DATABASE_URL` | `postgres://...` | Database connection |
| `REDIS_URL` | `redis://redis:6379/0` | Redis connection |
| `STRIPE_SECRET_KEY` | - | Stripe API key |
| `STRIPE_WEBHOOK_SECRET` | - | Webhook signature |
| `PLATFORM_FEE_PERCENT` | `15` | Platform fee % |
| `GOOGLE_CLIENT_ID` | - | Google OAuth |
| `APPLE_CLIENT_ID` | - | Apple Sign-In |

See `.env.example` for the complete list.
