"""
Ledger - Double-entry bookkeeping for financial transactions.

This module provides a complete ledger system for tracking monetary movements
with precision and auditability. It follows double-entry bookkeeping principles
where every transaction debits one account and credits another.

Public API:
    Models:
        LedgerAccount - Holds monetary value (balances, escrow, revenue)
        LedgerEntry - Records movements between accounts
        AccountType - Enum of account categories
        EntryType - Enum of transaction types

    Service:
        ledger - Singleton instance of LedgerService
        LedgerService - Class with all ledger operations

    Types:
        Money - Represents monetary amount in cents
        RecordEntryParams - Parameters for recording entries

    Exceptions:
        LedgerError - Base exception for ledger operations
        AccountNotFound - Account lookup failures
        InsufficientBalance - Balance validation failures
        InactiveAccount - Operations on inactive accounts

Usage:
    from payments.ledger import (
        ledger, AccountType, EntryType, RecordEntryParams,
        InsufficientBalance
    )

    # Create accounts
    external = ledger.get_or_create_account(
        AccountType.EXTERNAL_STRIPE,
        allow_negative=True
    )
    escrow = ledger.get_or_create_account(AccountType.PLATFORM_ESCROW)

    # Record a payment
    entry = ledger.record_entry(RecordEntryParams(
        debit_account_id=external.id,
        credit_account_id=escrow.id,
        amount_cents=5000,
        entry_type=EntryType.PAYMENT_RECEIVED,
        idempotency_key='payment:pi_123',
    ))

    # Check balance
    balance = ledger.get_balance(escrow.id)
    print(balance)  # Money(cents=5000, currency='usd')

    # Handle insufficient balance
    try:
        ledger.transfer(
            from_account_id=escrow.id,
            to_account_id=provider.id,
            amount_cents=10000,
            idempotency_key='release:123',
        )
    except InsufficientBalance as e:
        print(f"Need {e.required}, have {e.available}")
"""

from .exceptions import (
    AccountNotFound,
    InactiveAccount,
    InsufficientBalance,
    LedgerError,
)
from .models import AccountType, EntryType, LedgerAccount, LedgerEntry
from .services import LedgerService, ledger
from .types import Money, RecordEntryParams

__all__ = [
    # Models
    "LedgerAccount",
    "LedgerEntry",
    "AccountType",
    "EntryType",
    # Service
    "ledger",
    "LedgerService",
    # Types
    "Money",
    "RecordEntryParams",
    # Exceptions
    "LedgerError",
    "AccountNotFound",
    "InsufficientBalance",
    "InactiveAccount",
]
