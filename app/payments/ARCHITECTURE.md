# Payments Architecture

> Last generated: 2025-12-15 UTC

**Related Apps:** [core](../core/ARCHITECTURE.md) | [authentication](../authentication/ARCHITECTURE.md)

---

## Overview

The payments app provides comprehensive payment processing via Stripe, including direct payments, escrow workflows, and recurring subscriptions. It implements a double-entry bookkeeping ledger for financial tracking and uses state machines for lifecycle management.

### Dependencies

| Direction | Apps | Notes |
|-----------|------|-------|
| **Imports from** | `core` | BaseModel, BaseService, ServiceResult |
| **Imports from** | `authentication` | User model for payer/recipient relationships |
| **Imported by** | None | Standalone payment processing |

### External Packages
- `stripe` - Payment processing API
- `django-fsm` concepts - State machine transitions (custom implementation)

---

## Data Models

### Payment Models

```mermaid
erDiagram
    PaymentOrder {
        uuid id PK
        uuid payer_id FK "-> auth.User"
        string strategy_type
        string state
        int amount_cents
        string currency
        uuid reference_id
        string reference_type
        string stripe_payment_intent_id
        string stripe_invoice_id
        uuid subscription_id FK
        json metadata
        datetime created_at
        datetime updated_at
    }
    ConnectedAccount {
        uuid id PK
        uuid profile_id FK "-> auth.Profile"
        string stripe_account_id UK
        string onboarding_status
        boolean payouts_enabled
        boolean charges_enabled
        datetime created_at
    }
    Payout {
        uuid id PK
        uuid payment_order_id FK
        uuid connected_account_id FK
        string state
        int amount_cents
        string currency
        string stripe_transfer_id
        string failure_reason
        datetime created_at
    }
    Refund {
        uuid id PK
        uuid payment_order_id FK
        string state
        int amount_cents
        string currency
        string stripe_refund_id
        string reason
        datetime created_at
    }
    Subscription {
        uuid id PK
        uuid payer_id FK "-> auth.User"
        uuid recipient_profile_id FK "-> auth.Profile"
        string state
        string stripe_subscription_id UK
        string stripe_customer_id
        string stripe_price_id
        int amount_cents
        string currency
        string billing_interval
        boolean cancel_at_period_end
        datetime current_period_start
        datetime current_period_end
        datetime cancelled_at
        datetime created_at
    }
    WebhookEvent {
        uuid id PK
        string stripe_event_id UK
        string event_type
        string status
        json payload
        int retry_count
        string error_message
        datetime created_at
        datetime processed_at
    }

    PaymentOrder ||--o{ Refund : "has many"
    PaymentOrder ||--o| Payout : "has one"
    PaymentOrder }o--|| Subscription : "belongs to"
    Payout }o--|| ConnectedAccount : "paid to"
```

### Ledger Models

```mermaid
erDiagram
    LedgerAccount {
        uuid id PK
        string type
        uuid owner_id
        string currency
        boolean allow_negative
        boolean is_active
        datetime created_at
    }
    LedgerEntry {
        uuid id PK
        uuid debit_account_id FK
        uuid credit_account_id FK
        int amount_cents
        string currency
        string entry_type
        uuid reference_id
        string reference_type
        string description
        json metadata
        string idempotency_key UK
        string created_by
        datetime created_at
    }

    LedgerAccount ||--o{ LedgerEntry : "debit entries"
    LedgerAccount ||--o{ LedgerEntry : "credit entries"
```

---

## State Machines

### PaymentOrder States

```mermaid
stateDiagram-v2
    [*] --> DRAFT: Created
    DRAFT --> PENDING: submit()
    DRAFT --> CANCELLED: cancel()
    PENDING --> PROCESSING: process()
    PENDING --> CANCELLED: cancel()
    PROCESSING --> CAPTURED: capture()
    PROCESSING --> FAILED: fail()
    CAPTURED --> SETTLED: settle_from_captured()
    CAPTURED --> HELD: hold()
    HELD --> RELEASED: release()
    RELEASED --> SETTLED: settle_from_released()
    CAPTURED --> PARTIALLY_REFUNDED: refund_partial()
    CAPTURED --> REFUNDED: refund_full()
    SETTLED --> PARTIALLY_REFUNDED: refund_partial()
    SETTLED --> REFUNDED: refund_full()
```

### Subscription States

```mermaid
stateDiagram-v2
    [*] --> PENDING: Created
    PENDING --> ACTIVE: activate()
    ACTIVE --> PAST_DUE: mark_past_due()
    PAST_DUE --> ACTIVE: reactivate()
    ACTIVE --> CANCELLED: cancel()
    PAST_DUE --> CANCELLED: cancel()
    PENDING --> CANCELLED: cancel()
```

### Payout States

```mermaid
stateDiagram-v2
    [*] --> PENDING: Created
    PENDING --> PROCESSING: process()
    PROCESSING --> SCHEDULED: mark_scheduled()
    SCHEDULED --> PAID: complete()
    PROCESSING --> PAID: complete()
    PROCESSING --> FAILED: fail()
    SCHEDULED --> FAILED: fail()
```

### Refund States

```mermaid
stateDiagram-v2
    [*] --> REQUESTED: Created
    REQUESTED --> PROCESSING: process()
    PROCESSING --> COMPLETED: complete()
    PROCESSING --> FAILED: fail()
```

---

## Component Flow

### Payment Strategy Pattern

```mermaid
flowchart TD
    subgraph Orchestrator["PaymentOrchestrator"]
        Init[initiate_payment]
        GetStrategy[get_strategy]
    end

    subgraph Strategies["Payment Strategies"]
        Direct[DirectPaymentStrategy]
        Escrow[EscrowPaymentStrategy]
        Subscription[SubscriptionPaymentStrategy]
    end

    subgraph Stripe["Stripe API"]
        PI[PaymentIntent]
        Transfer[Transfer]
        Sub[Subscription]
    end

    Init --> GetStrategy
    GetStrategy --> Direct
    GetStrategy --> Escrow
    GetStrategy --> Subscription

    Direct --> PI
    Escrow --> PI
    Subscription --> Sub

    PI --> |webhook| Handlers
    Transfer --> |webhook| Handlers
    Sub --> |webhook| Handlers
```

### Direct Payment Flow

```mermaid
flowchart TD
    Create[Create PaymentOrder] --> Intent[Create PaymentIntent]
    Intent --> ClientSecret[Return client_secret]
    ClientSecret --> Frontend[Frontend collects payment]
    Frontend --> Webhook((payment_intent.succeeded))
    Webhook --> Handler[handle_payment_succeeded]
    Handler --> Ledger1[Debit EXTERNAL_STRIPE]
    Ledger1 --> Ledger2[Credit USER_BALANCE]
    Ledger2 --> Settle[State: SETTLED]
```

### Escrow Payment Flow

```mermaid
flowchart TD
    Create[Create PaymentOrder] --> Intent[Create PaymentIntent]
    Intent --> ClientSecret[Return client_secret]
    ClientSecret --> Frontend[Frontend collects payment]
    Frontend --> Webhook((payment_intent.succeeded))
    Webhook --> Handler[handle_payment_succeeded]
    Handler --> Ledger1[Debit EXTERNAL_STRIPE]
    Ledger1 --> Ledger2[Credit PLATFORM_ESCROW]
    Ledger2 --> Hold[State: HELD]

    Hold --> |Service complete| Release[release_hold]
    Release --> Ledger3[Debit PLATFORM_ESCROW]
    Ledger3 --> Ledger4[Credit USER_BALANCE]
    Ledger4 --> Payout[Create Payout]
    Payout --> Transfer[Stripe Transfer]
    Transfer --> TransferWebhook((transfer.paid))
    TransferWebhook --> Settle[State: SETTLED]
```

### Subscription Payment Flow

```mermaid
flowchart TD
    Create[Create Subscription] --> StripeSub[Stripe Subscription]
    StripeSub --> LocalSub[Local Subscription: PENDING]

    StripeSub --> Invoice((invoice.paid))
    Invoice --> CreateOrder[Create PaymentOrder]
    CreateOrder --> Activate[Subscription: ACTIVE]
    Activate --> Ledger1[Debit EXTERNAL_STRIPE]
    Ledger1 --> Ledger2[Credit PLATFORM_ESCROW]
    Ledger2 --> Ledger3[Debit PLATFORM_ESCROW - Fee]
    Ledger3 --> Ledger4[Credit PLATFORM_REVENUE]
    Ledger4 --> Ledger5[Debit PLATFORM_ESCROW - Net]
    Ledger5 --> Ledger6[Credit USER_BALANCE]
```

---

## External Interfaces

### API Endpoints

| Method | Path | View | Description |
|--------|------|------|-------------|
| POST | `/api/v1/payments/webhooks/stripe/` | `stripe_webhook` | Stripe webhook receiver |

**URL Namespace:** `payments`

### Webhook Events Handled

| Event Type | Handler | Action |
|------------|---------|--------|
| `payment_intent.succeeded` | `handle_payment_intent_succeeded` | Process successful payment |
| `payment_intent.payment_failed` | `handle_payment_intent_failed` | Handle payment failure |
| `payment_intent.canceled` | `handle_payment_intent_canceled` | Handle cancellation |
| `transfer.created` | `handle_transfer_created` | Mark payout scheduled |
| `transfer.paid` | `handle_transfer_paid` | Complete payout, settle order |
| `transfer.failed` | `handle_transfer_failed` | Mark payout failed |
| `charge.refunded` | `handle_charge_refunded` | Process refund |
| `account.updated` | `handle_account_updated` | Update connected account status |
| `invoice.paid` | `handle_invoice_paid` | Process subscription payment |
| `invoice.payment_failed` | `handle_invoice_payment_failed` | Mark subscription past_due |
| `customer.subscription.created` | `handle_subscription_created` | Acknowledge subscription |
| `customer.subscription.updated` | `handle_subscription_updated` | Sync subscription state |
| `customer.subscription.deleted` | `handle_subscription_deleted` | Cancel subscription |

---

## Ledger System

### Account Types

| Type | Purpose | Can Be Negative |
|------|---------|-----------------|
| `USER_BALANCE` | User's available balance (per owner) | No |
| `PLATFORM_ESCROW` | Money held during transactions | No |
| `PLATFORM_REVENUE` | Platform's earned fees | No |
| `EXTERNAL_STRIPE` | External money flow representation | Yes |

### Entry Types

| Type | Description |
|------|-------------|
| `PAYMENT_RECEIVED` | Money received from Stripe |
| `PAYMENT_RELEASED` | Money released from escrow |
| `FEE_COLLECTED` | Platform fee deducted |
| `PAYOUT` | Money sent to connected account |
| `REFUND` | Money returned to payer |
| `ADJUSTMENT` | Manual correction |
| `TRANSFER` | Direct account-to-account transfer |

### Double-Entry Flow Examples

**Direct Payment:**
```
1. EXTERNAL_STRIPE (debit) -> PLATFORM_ESCROW (credit) [PAYMENT_RECEIVED]
2. PLATFORM_ESCROW (debit) -> PLATFORM_REVENUE (credit) [FEE_COLLECTED]
3. PLATFORM_ESCROW (debit) -> USER_BALANCE (credit) [PAYMENT_RELEASED]
```

**Payout:**
```
4. USER_BALANCE (debit) -> EXTERNAL_STRIPE (credit) [PAYOUT]
```

---

## Service Layer

### PaymentOrchestrator

Central coordinator for payment operations:

```python
PaymentOrchestrator.initiate_payment(params) -> ServiceResult[PaymentResult]
PaymentOrchestrator.get_payment_order(payment_order_id) -> PaymentOrder | None
PaymentOrchestrator.get_payment_by_intent(intent_id) -> PaymentOrder | None
PaymentOrchestrator.get_strategy_for_order(order) -> PaymentStrategy
```

### LedgerService

Double-entry bookkeeping operations:

```python
LedgerService.get_or_create_account(type, owner_id, currency) -> LedgerAccount
LedgerService.record_entry(params) -> LedgerEntry
LedgerService.record_entries(entries) -> list[LedgerEntry]
LedgerService.get_balance(account) -> int
```

---

## Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `STRIPE_SECRET_KEY` | - | Stripe API secret key |
| `STRIPE_PUBLISHABLE_KEY` | - | Stripe publishable key |
| `STRIPE_WEBHOOK_SECRET` | - | Webhook signature verification |
| `STRIPE_API_TIMEOUT_SECONDS` | 10 | API request timeout |
| `STRIPE_MAX_RETRIES` | 3 | Max retry attempts |
| `PLATFORM_FEE_PERCENT` | 15 | Platform fee percentage |
| `ESCROW_DEFAULT_HOLD_DURATION_DAYS` | 42 | Default escrow hold period |
| `ESCROW_MAX_HOLD_DURATION_DAYS` | 90 | Maximum escrow hold period |
