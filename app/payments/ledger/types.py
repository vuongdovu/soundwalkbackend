"""
Data types for ledger operations.

This module defines dataclasses used throughout the ledger system
for type-safe data transfer between layers.

Types:
    Money: Represents a monetary amount in cents with currency
    RecordEntryParams: Parameters for recording a ledger entry

Usage:
    from payments.ledger.types import Money, RecordEntryParams

    # Create a money object
    amount = Money(cents=5000, currency='usd')
    print(amount)  # "$50.00 USD"

    # Create entry params
    params = RecordEntryParams(
        debit_account_id=external_account.id,
        credit_account_id=escrow_account.id,
        amount_cents=5000,
        entry_type='payment_received',
        idempotency_key='payment:pi_123',
    )
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Money:
    """
    Represents a monetary amount.

    All amounts are stored in cents (smallest currency unit) to avoid
    floating-point precision issues. The currency is stored as a
    3-letter ISO 4217 code.

    Attributes:
        cents: Amount in the smallest currency unit (e.g., cents for USD)
        currency: ISO 4217 currency code (default: 'usd')

    Example:
        amount = Money(cents=5000, currency='usd')
        print(amount)  # "$50.00 USD"

        # Negative amounts for debits
        debit = Money(cents=-2500)
        print(debit)  # "$-25.00 USD"
    """

    cents: int
    currency: str = "usd"

    def __str__(self) -> str:
        """Format as currency string (e.g., '$50.00 USD')."""
        dollars = self.cents / 100
        return f"${dollars:.2f} {self.currency.upper()}"

    def __repr__(self) -> str:
        """Return detailed representation for debugging."""
        return f"Money(cents={self.cents}, currency={self.currency!r})"

    def __eq__(self, other: object) -> bool:
        """Compare two Money objects."""
        if not isinstance(other, Money):
            return NotImplemented
        return self.cents == other.cents and self.currency == other.currency

    def __add__(self, other: Money) -> Money:
        """Add two Money objects (must have same currency)."""
        if not isinstance(other, Money):
            return NotImplemented
        if self.currency != other.currency:
            raise ValueError(
                f"Cannot add Money with different currencies: "
                f"{self.currency} and {other.currency}"
            )
        return Money(cents=self.cents + other.cents, currency=self.currency)

    def __sub__(self, other: Money) -> Money:
        """Subtract two Money objects (must have same currency)."""
        if not isinstance(other, Money):
            return NotImplemented
        if self.currency != other.currency:
            raise ValueError(
                f"Cannot subtract Money with different currencies: "
                f"{self.currency} and {other.currency}"
            )
        return Money(cents=self.cents - other.cents, currency=self.currency)


@dataclass
class RecordEntryParams:
    """
    Parameters for recording a ledger entry.

    This dataclass encapsulates all the information needed to record
    a single ledger entry. It follows the double-entry bookkeeping
    pattern where every entry debits one account and credits another.

    Required Attributes:
        debit_account_id: UUID of the account being debited (money out)
        credit_account_id: UUID of the account being credited (money in)
        amount_cents: Amount in cents (must be positive)
        entry_type: Type of entry (e.g., 'payment_received', 'transfer')
        idempotency_key: Unique key to prevent duplicate entries

    Optional Attributes:
        reference_id: UUID of related business entity (e.g., booking ID)
        reference_type: Type of related entity (e.g., 'booking', 'payment_intent')
        description: Human-readable description
        metadata: Arbitrary JSON-serializable data
        created_by: Identifier of the service/user creating the entry

    Example:
        params = RecordEntryParams(
            debit_account_id=external.id,
            credit_account_id=escrow.id,
            amount_cents=10000,
            entry_type=EntryType.PAYMENT_RECEIVED,
            idempotency_key=f'payment:{payment_intent_id}',
            reference_type='payment_intent',
            description='Payment from customer',
            metadata={'stripe_id': 'pi_123'},
            created_by='stripe_webhook',
        )
    """

    # Required fields
    debit_account_id: uuid.UUID
    credit_account_id: uuid.UUID
    amount_cents: int
    entry_type: str
    idempotency_key: str

    # Optional fields
    reference_id: uuid.UUID | None = None
    reference_type: str | None = None
    description: str | None = None
    metadata: dict[str, Any] | None = field(default_factory=dict)
    created_by: str | None = None

    def __post_init__(self) -> None:
        """Validate params after initialization."""
        if self.amount_cents <= 0:
            raise ValueError("amount_cents must be positive")
        if not self.idempotency_key:
            raise ValueError("idempotency_key is required")
        if self.debit_account_id == self.credit_account_id:
            raise ValueError("debit_account_id and credit_account_id must be different")
