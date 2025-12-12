"""
Ledger service layer for financial operations.

This module provides the LedgerService class which encapsulates all
business logic for ledger operations. All ledger writes should go
through this service to ensure proper validation, transaction handling,
and audit trails.

Usage:
    from payments.ledger.services import LedgerService, ledger
    from payments.ledger.types import RecordEntryParams

    # Using the singleton
    account = ledger.get_or_create_account(AccountType.PLATFORM_ESCROW)
    balance = ledger.get_balance(account.id)

    # Recording an entry
    entry = ledger.record_entry(RecordEntryParams(
        debit_account_id=external.id,
        credit_account_id=escrow.id,
        amount_cents=5000,
        entry_type=EntryType.PAYMENT_RECEIVED,
        idempotency_key='payment:pi_123',
    ))
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from django.db import IntegrityError, transaction
from django.db.models import Q

from .exceptions import AccountNotFound, InactiveAccount, InsufficientBalance
from .models import AccountType, EntryType, LedgerAccount, LedgerEntry
from .types import Money, RecordEntryParams

if TYPE_CHECKING:
    pass


class LedgerService:
    """
    Service class for ledger operations.

    All ledger writes should go through this service to ensure proper
    validation, transaction handling, and audit trails.

    Key features:
    - Atomic transactions for multi-entry operations
    - Idempotency via unique keys (safe to retry)
    - Balance validation before debits
    - Account locking to prevent race conditions

    All methods are static - no instance state is maintained.
    """

    @staticmethod
    def get_or_create_account(
        account_type: AccountType | str,
        owner_id: uuid.UUID | None = None,
        currency: str = "usd",
        allow_negative: bool = False,
    ) -> LedgerAccount:
        """
        Get existing account or create new one.

        Looks up an account by (type, owner_id, currency). If not found,
        creates a new account with the specified parameters.

        Args:
            account_type: Type of account (e.g., USER_BALANCE, PLATFORM_ESCROW)
            owner_id: Optional UUID linking to business entity (e.g., user ID)
            currency: ISO 4217 currency code (default: 'usd')
            allow_negative: Whether account can have negative balance

        Returns:
            The existing or newly created LedgerAccount

        Example:
            # Platform escrow account
            escrow = LedgerService.get_or_create_account(
                AccountType.PLATFORM_ESCROW,
            )

            # User balance account
            user_balance = LedgerService.get_or_create_account(
                AccountType.USER_BALANCE,
                owner_id=user.id,
            )
        """
        account, _ = LedgerAccount.objects.get_or_create(
            type=account_type,
            owner_id=owner_id,
            currency=currency,
            defaults={"allow_negative": allow_negative},
        )
        return account

    @staticmethod
    def get_account(account_id: uuid.UUID) -> LedgerAccount:
        """
        Get account by ID.

        Args:
            account_id: UUID of the account

        Returns:
            The LedgerAccount

        Raises:
            AccountNotFound: If account doesn't exist
        """
        try:
            return LedgerAccount.objects.get(id=account_id)
        except LedgerAccount.DoesNotExist:
            raise AccountNotFound(
                f"Account {account_id} not found",
                details={"account_id": str(account_id)},
            )

    @staticmethod
    def get_account_by_owner(
        account_type: AccountType | str,
        owner_id: uuid.UUID,
        currency: str = "usd",
    ) -> LedgerAccount | None:
        """
        Get account by type, owner, and currency.

        Args:
            account_type: Type of account
            owner_id: UUID of the owner
            currency: ISO 4217 currency code (default: 'usd')

        Returns:
            The LedgerAccount if found, None otherwise
        """
        try:
            return LedgerAccount.objects.get(
                type=account_type,
                owner_id=owner_id,
                currency=currency,
            )
        except LedgerAccount.DoesNotExist:
            return None

    @staticmethod
    def _validate_account_for_debit(account: LedgerAccount, amount_cents: int) -> None:
        """
        Validate that an account can be debited.

        Checks:
        1. Account is active
        2. Account has sufficient balance (unless allow_negative is True)

        Args:
            account: The account to validate
            amount_cents: Amount to debit in cents

        Raises:
            InactiveAccount: If account is inactive
            InsufficientBalance: If account lacks funds
        """
        if not account.is_active:
            raise InactiveAccount(
                f"Account {account.id} is inactive",
                details={"account_id": str(account.id)},
            )

        if not account.allow_negative:
            current_balance = account.get_balance()
            if current_balance < amount_cents:
                raise InsufficientBalance(
                    account_id=account.id,
                    required=amount_cents,
                    available=current_balance,
                )

    @staticmethod
    def _validate_account_for_credit(account: LedgerAccount) -> None:
        """
        Validate that an account can be credited.

        Currently only checks that the account is active.

        Args:
            account: The account to validate

        Raises:
            InactiveAccount: If account is inactive
        """
        if not account.is_active:
            raise InactiveAccount(
                f"Account {account.id} is inactive",
                details={"account_id": str(account.id)},
            )

    @staticmethod
    def record_entry(params: RecordEntryParams) -> LedgerEntry:
        """
        Record a single ledger entry.

        Idempotent - safe to call multiple times with the same idempotency_key.
        If an entry with the same key already exists, returns that entry.

        Args:
            params: Entry parameters including accounts, amount, and idempotency key

        Returns:
            The created or existing LedgerEntry

        Raises:
            AccountNotFound: If either account doesn't exist
            InactiveAccount: If either account is inactive
            InsufficientBalance: If debit account lacks funds
        """
        return LedgerService.record_entries([params])[0]

    @staticmethod
    def record_entries(entries: list[RecordEntryParams]) -> list[LedgerEntry]:
        """
        Record multiple ledger entries atomically.

        All entries succeed or all fail - provides atomic transaction guarantee.
        Idempotent for individual entries - existing entries by idempotency_key
        are returned without modification.

        Important: Entries are processed sequentially, so balance changes from
        earlier entries in the batch affect validation of later entries.

        Args:
            entries: List of entry parameters

        Returns:
            List of created or existing LedgerEntry objects

        Raises:
            AccountNotFound: If any account doesn't exist
            InactiveAccount: If any account is inactive
            InsufficientBalance: If any debit account lacks funds
        """
        if not entries:
            return []

        results: list[LedgerEntry] = []

        with transaction.atomic():
            # Collect all account IDs
            account_ids: set[uuid.UUID] = set()
            for params in entries:
                account_ids.add(params.debit_account_id)
                account_ids.add(params.credit_account_id)

            # Lock accounts in consistent order to prevent deadlocks
            # ORDER BY id ensures all concurrent transactions acquire locks
            # in the same order, preventing circular wait conditions
            accounts = {
                acc.id: acc
                for acc in LedgerAccount.objects.filter(id__in=account_ids)
                .select_for_update()
                .order_by("id")
            }

            # Validate all accounts exist
            for account_id in account_ids:
                if account_id not in accounts:
                    raise AccountNotFound(
                        f"Account {account_id} not found",
                        details={"account_id": str(account_id)},
                    )

            # Process each entry
            for params in entries:
                debit_account = accounts[params.debit_account_id]
                credit_account = accounts[params.credit_account_id]

                # Step 1: Check idempotency FIRST
                # This is critical - we must check before validation because
                # validation queries the balance, and if we created the entry
                # first (via get_or_create), the balance would be wrong.
                try:
                    existing = LedgerEntry.objects.get(
                        idempotency_key=params.idempotency_key
                    )
                    results.append(existing)
                    continue
                except LedgerEntry.DoesNotExist:
                    pass

                # Step 2: Validate BEFORE creating
                # This ensures balance checks see the correct state
                LedgerService._validate_account_for_debit(
                    debit_account, params.amount_cents
                )
                LedgerService._validate_account_for_credit(credit_account)

                # Step 3: Create entry
                # Handle potential race condition where another process
                # created an entry with the same idempotency key between
                # our check and create
                try:
                    entry = LedgerEntry.objects.create(
                        idempotency_key=params.idempotency_key,
                        debit_account=debit_account,
                        credit_account=credit_account,
                        amount_cents=params.amount_cents,
                        currency=debit_account.currency,
                        entry_type=params.entry_type,
                        reference_id=params.reference_id,
                        reference_type=params.reference_type,
                        description=params.description,
                        metadata=params.metadata or {},
                        created_by=params.created_by,
                    )
                except IntegrityError:
                    # Race condition: another process created it
                    # This is safe because idempotency_key is unique
                    entry = LedgerEntry.objects.get(
                        idempotency_key=params.idempotency_key
                    )

                results.append(entry)

        return results

    @staticmethod
    def transfer(
        from_account_id: uuid.UUID,
        to_account_id: uuid.UUID,
        amount_cents: int,
        idempotency_key: str,
        description: str | None = None,
        reference_type: str | None = None,
        reference_id: uuid.UUID | None = None,
        created_by: str | None = None,
    ) -> LedgerEntry:
        """
        Convenience method for simple account-to-account transfers.

        Creates a TRANSFER type entry. For more complex operations
        (fee collection, etc.), use record_entry or record_entries directly.

        Args:
            from_account_id: UUID of account to debit
            to_account_id: UUID of account to credit
            amount_cents: Amount to transfer in cents
            idempotency_key: Unique key to prevent duplicates
            description: Optional description
            reference_type: Optional type of related entity
            reference_id: Optional UUID of related entity
            created_by: Optional identifier of who created this

        Returns:
            The created or existing LedgerEntry

        Example:
            entry = LedgerService.transfer(
                from_account_id=escrow.id,
                to_account_id=provider.id,
                amount_cents=8500,
                idempotency_key=f'release:{booking_id}',
                description='Payment released to provider',
                reference_type='booking',
                reference_id=booking_id,
                created_by='booking_service',
            )
        """
        return LedgerService.record_entry(
            RecordEntryParams(
                debit_account_id=from_account_id,
                credit_account_id=to_account_id,
                amount_cents=amount_cents,
                entry_type=EntryType.TRANSFER,
                idempotency_key=idempotency_key,
                description=description,
                reference_type=reference_type,
                reference_id=reference_id,
                created_by=created_by,
            )
        )

    @staticmethod
    def get_balance(account_id: uuid.UUID) -> Money:
        """
        Get current balance for an account.

        Args:
            account_id: UUID of the account

        Returns:
            Money object with balance in cents and currency

        Raises:
            AccountNotFound: If account doesn't exist
        """
        account = LedgerService.get_account(account_id)
        return Money(cents=account.get_balance(), currency=account.currency)

    @staticmethod
    def get_entries_for_account(
        account_id: uuid.UUID,
        limit: int = 100,
        offset: int = 0,
    ) -> list[LedgerEntry]:
        """
        Get entries where account is debited or credited.

        Returns entries ordered by created_at descending (newest first).

        Args:
            account_id: UUID of the account
            limit: Maximum number of entries to return (default: 100)
            offset: Number of entries to skip (default: 0)

        Returns:
            List of LedgerEntry objects
        """
        return list(
            LedgerEntry.objects.filter(
                Q(debit_account_id=account_id) | Q(credit_account_id=account_id)
            ).order_by("-created_at")[offset : offset + limit]
        )

    @staticmethod
    def get_entries_by_reference(
        reference_type: str,
        reference_id: uuid.UUID,
    ) -> list[LedgerEntry]:
        """
        Get all entries for a given reference.

        Useful for auditing all financial activity related to a
        business entity (e.g., all entries for a booking).

        Args:
            reference_type: Type of related entity (e.g., 'booking')
            reference_id: UUID of related entity

        Returns:
            List of LedgerEntry objects ordered by created_at ascending
        """
        return list(
            LedgerEntry.objects.filter(
                reference_type=reference_type,
                reference_id=reference_id,
            ).order_by("created_at")
        )

    @staticmethod
    def deactivate_account(account_id: uuid.UUID) -> LedgerAccount:
        """
        Soft-delete an account by marking it inactive.

        Inactive accounts cannot be used in new transactions but
        their history is preserved.

        Args:
            account_id: UUID of the account

        Returns:
            The updated LedgerAccount

        Raises:
            AccountNotFound: If account doesn't exist
        """
        account = LedgerService.get_account(account_id)
        account.is_active = False
        account.save(update_fields=["is_active"])
        return account

    @staticmethod
    def reactivate_account(account_id: uuid.UUID) -> LedgerAccount:
        """
        Reactivate a previously deactivated account.

        Args:
            account_id: UUID of the account

        Returns:
            The updated LedgerAccount

        Raises:
            AccountNotFound: If account doesn't exist
        """
        account = LedgerService.get_account(account_id)
        account.is_active = True
        account.save(update_fields=["is_active"])
        return account


# Singleton instance for convenience
# Usage: from payments.ledger.services import ledger
ledger = LedgerService()
