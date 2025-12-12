"""
Ledger models for double-entry bookkeeping.

This module defines the core models for a financial ledger system:
- LedgerAccount: Holds monetary value (user balances, escrow, revenue)
- LedgerEntry: Records movements between accounts

Following double-entry bookkeeping principles, every entry debits one
account and credits another, ensuring the books always balance.

Usage:
    from payments.ledger.models import LedgerAccount, LedgerEntry, AccountType, EntryType

    # Create accounts
    escrow = LedgerAccount.objects.create(
        type=AccountType.PLATFORM_ESCROW,
        currency='usd'
    )

    # Get balance
    balance = escrow.get_balance()  # Returns balance in cents
"""

from __future__ import annotations


from django.db import models
from django.db.models import Case, Q, Sum, Value, When
from django.db.models.functions import Coalesce

from core.model_mixins import UUIDPrimaryKeyMixin


class AccountType(models.TextChoices):
    """
    Types of ledger accounts.

    These represent different categories of accounts in the system.
    Extend this enum to add new account types as business needs evolve.

    Values:
        USER_BALANCE: Individual user's available balance
        PLATFORM_ESCROW: Money held temporarily during transactions
        PLATFORM_REVENUE: Platform's earned fees and revenue
        EXTERNAL_STRIPE: Represents money in/out of Stripe (external world)
    """

    USER_BALANCE = "user_balance", "User Balance"
    PLATFORM_ESCROW = "platform_escrow", "Platform Escrow"
    PLATFORM_REVENUE = "platform_revenue", "Platform Revenue"
    EXTERNAL_STRIPE = "external_stripe", "External Stripe"


class EntryType(models.TextChoices):
    """
    Types of ledger entries.

    These categorize the nature of monetary movements.
    Extend this enum to add new entry types as business needs evolve.

    Values:
        PAYMENT_RECEIVED: Money received from external source (e.g., Stripe)
        PAYMENT_RELEASED: Money released from escrow to recipient
        FEE_COLLECTED: Platform fee taken from a transaction
        PAYOUT: Money sent to external destination (e.g., bank transfer)
        REFUND: Money returned due to cancellation/dispute
        ADJUSTMENT: Manual correction or adjustment
        TRANSFER: Direct transfer between accounts
    """

    PAYMENT_RECEIVED = "payment_received", "Payment Received"
    PAYMENT_RELEASED = "payment_released", "Payment Released"
    FEE_COLLECTED = "fee_collected", "Fee Collected"
    PAYOUT = "payout", "Payout"
    REFUND = "refund", "Refund"
    ADJUSTMENT = "adjustment", "Adjustment"
    TRANSFER = "transfer", "Transfer"


class LedgerAccount(UUIDPrimaryKeyMixin, models.Model):
    """
    A ledger account that holds monetary value.

    Accounts are categorized by type and can be associated with an owner
    (e.g., a user's balance account). The balance is computed from the
    sum of all credits minus debits in related entries.

    Fields:
        id: UUID primary key (from UUIDPrimaryKeyMixin)
        type: Account category (user_balance, escrow, revenue, external)
        owner_id: Optional UUID linking to a business entity (e.g., user)
        currency: ISO 4217 currency code (default: 'usd')
        allow_negative: Whether balance can go negative (for external accounts)
        is_active: Whether the account is active (soft delete pattern)
        created_at: Timestamp when account was created

    Constraints:
        - Unique combination of (type, owner_id, currency)

    Example:
        # Platform escrow account
        escrow = LedgerAccount.objects.create(
            type=AccountType.PLATFORM_ESCROW,
            currency='usd'
        )

        # User balance account
        user_balance = LedgerAccount.objects.create(
            type=AccountType.USER_BALANCE,
            owner_id=user.id,
            currency='usd'
        )

        # Check balance
        balance_cents = user_balance.get_balance()
    """

    type = models.CharField(
        max_length=50,
        choices=AccountType.choices,
        help_text="Category of this account",
    )
    owner_id = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        help_text="UUID of the entity that owns this account (e.g., user ID)",
    )
    currency = models.CharField(
        max_length=3,
        default="usd",
        help_text="ISO 4217 currency code",
    )
    allow_negative = models.BooleanField(
        default=False,
        help_text="Whether this account can have a negative balance",
    )
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Whether this account is active",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        help_text="Timestamp when this account was created",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["type", "owner_id", "currency"],
                name="unique_account_per_owner",
            )
        ]
        indexes = [
            models.Index(fields=["type", "currency"]),
        ]

    def __str__(self) -> str:
        """Return string representation."""
        if self.owner_id:
            return f"{self.get_type_display()} ({self.owner_id})"
        return self.get_type_display()

    def get_balance(self) -> int:
        """
        Compute current balance from entries.

        Balance is calculated as the sum of all credits to this account
        minus the sum of all debits from this account.

        Returns:
            Balance in cents (can be negative if allow_negative is True)

        Note:
            This performs a database query. For performance with many entries,
            consider caching or maintaining a denormalized balance field.
        """
        result = LedgerEntry.objects.filter(
            Q(credit_account=self) | Q(debit_account=self)
        ).aggregate(
            credits=Coalesce(
                Sum(
                    Case(
                        When(credit_account=self, then="amount_cents"),
                        default=Value(0),
                        output_field=models.BigIntegerField(),
                    )
                ),
                Value(0),
                output_field=models.BigIntegerField(),
            ),
            debits=Coalesce(
                Sum(
                    Case(
                        When(debit_account=self, then="amount_cents"),
                        default=Value(0),
                        output_field=models.BigIntegerField(),
                    )
                ),
                Value(0),
                output_field=models.BigIntegerField(),
            ),
        )
        return result["credits"] - result["debits"]


class LedgerEntry(UUIDPrimaryKeyMixin, models.Model):
    """
    A ledger entry recording movement of money between accounts.

    Each entry follows double-entry bookkeeping: money is debited from
    one account and credited to another. Entries are immutable once
    created - corrections are made via new adjustment entries.

    Fields:
        id: UUID primary key (from UUIDPrimaryKeyMixin)
        created_at: Timestamp when entry was recorded
        debit_account: Account money is taken from
        credit_account: Account money is added to
        amount_cents: Amount in cents (always positive)
        currency: ISO 4217 currency code
        entry_type: Category of this entry
        reference_id: Optional UUID of related business entity
        reference_type: Type of related entity (e.g., 'booking')
        description: Human-readable description
        metadata: Arbitrary JSON data
        created_by: Identifier of service/user that created this
        idempotency_key: Unique key to prevent duplicate entries

    Constraints:
        - amount_cents must be positive
        - idempotency_key must be unique

    Example:
        # Record payment received
        entry = LedgerEntry.objects.create(
            debit_account=external_stripe,
            credit_account=platform_escrow,
            amount_cents=10000,
            entry_type=EntryType.PAYMENT_RECEIVED,
            idempotency_key='payment:pi_123',
            description='Payment from customer',
        )
    """

    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        help_text="Timestamp when this entry was recorded",
    )

    debit_account = models.ForeignKey(
        LedgerAccount,
        on_delete=models.PROTECT,
        related_name="debit_entries",
        help_text="Account money is taken from",
    )
    credit_account = models.ForeignKey(
        LedgerAccount,
        on_delete=models.PROTECT,
        related_name="credit_entries",
        help_text="Account money is added to",
    )
    amount_cents = models.PositiveBigIntegerField(
        help_text="Amount in cents (always positive)",
    )
    currency = models.CharField(
        max_length=3,
        default="usd",
        help_text="ISO 4217 currency code",
    )

    entry_type = models.CharField(
        max_length=50,
        choices=EntryType.choices,
        help_text="Category of this entry",
    )
    reference_id = models.UUIDField(
        null=True,
        blank=True,
        help_text="UUID of related business entity (e.g., booking ID)",
    )
    reference_type = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        help_text="Type of related entity (e.g., 'booking', 'payment_intent')",
    )

    description = models.TextField(
        null=True,
        blank=True,
        help_text="Human-readable description of this entry",
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Arbitrary JSON data for extensibility",
    )

    created_by = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="Identifier of service/user that created this entry",
    )
    idempotency_key = models.CharField(
        max_length=255,
        unique=True,
        help_text="Unique key to prevent duplicate entries",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["reference_type", "reference_id"]),
            models.Index(fields=["entry_type"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=Q(amount_cents__gt=0),
                name="ledger_entry_amount_cents_positive",
            )
        ]

    def __str__(self) -> str:
        """Return string representation."""
        return f"{self.get_entry_type_display()}: {self.amount_cents} cents"
