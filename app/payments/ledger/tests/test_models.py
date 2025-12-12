"""
Tests for ledger models.

This module tests the LedgerAccount and LedgerEntry models,
including field constraints, defaults, and balance calculations.
"""

import uuid

import pytest
from django.db import IntegrityError

from payments.ledger.models import AccountType, EntryType, LedgerAccount, LedgerEntry
from payments.ledger.tests.factories import LedgerAccountFactory, LedgerEntryFactory


class TestLedgerAccount:
    """Tests for the LedgerAccount model."""

    def test_account_created_with_uuid_primary_key(self, db):
        """Account should have a UUID as primary key."""
        account = LedgerAccountFactory()
        assert isinstance(account.id, uuid.UUID)

    def test_account_defaults(self, db):
        """Account should have correct default values."""
        account = LedgerAccount.objects.create(
            type=AccountType.USER_BALANCE,
            owner_id=uuid.uuid4(),
        )

        assert account.currency == "usd"
        assert account.allow_negative is False
        assert account.is_active is True
        assert account.created_at is not None

    def test_account_str_with_owner(self, db):
        """Account string representation should include owner ID when present."""
        owner_id = uuid.uuid4()
        account = LedgerAccountFactory(
            type=AccountType.USER_BALANCE,
            owner_id=owner_id,
        )

        assert str(owner_id) in str(account)
        assert "User Balance" in str(account)

    def test_account_str_without_owner(self, db):
        """Account string representation without owner shows type only."""
        account = LedgerAccountFactory(
            type=AccountType.PLATFORM_ESCROW,
            owner_id=None,
        )

        assert str(account) == "Platform Escrow"

    def test_account_unique_constraint_same_type_owner_currency(self, db):
        """Cannot create duplicate accounts with same type, owner, and currency."""
        owner_id = uuid.uuid4()
        LedgerAccountFactory(
            type=AccountType.USER_BALANCE,
            owner_id=owner_id,
            currency="usd",
        )

        with pytest.raises(IntegrityError):
            LedgerAccountFactory(
                type=AccountType.USER_BALANCE,
                owner_id=owner_id,
                currency="usd",
            )

    def test_account_allows_different_currencies_same_owner(self, db):
        """Same owner can have accounts in different currencies."""
        owner_id = uuid.uuid4()
        usd_account = LedgerAccountFactory(
            type=AccountType.USER_BALANCE,
            owner_id=owner_id,
            currency="usd",
        )
        eur_account = LedgerAccountFactory(
            type=AccountType.USER_BALANCE,
            owner_id=owner_id,
            currency="eur",
        )

        assert usd_account.id != eur_account.id

    def test_account_allows_different_types_same_owner(self, db):
        """Same owner can have accounts of different types."""
        owner_id = uuid.uuid4()
        balance = LedgerAccountFactory(
            type=AccountType.USER_BALANCE,
            owner_id=owner_id,
        )
        # This would be unusual but should be allowed by constraints
        escrow = LedgerAccountFactory(
            type=AccountType.PLATFORM_ESCROW,
            owner_id=owner_id,
        )

        assert balance.id != escrow.id


class TestLedgerAccountBalance:
    """Tests for LedgerAccount.get_balance() method."""

    def test_balance_empty_account_returns_zero(self, db):
        """New account with no entries should have zero balance."""
        account = LedgerAccountFactory()
        assert account.get_balance() == 0

    def test_balance_after_single_credit(self, db, external_account):
        """Balance should reflect credits to the account."""
        account = LedgerAccountFactory(
            type=AccountType.PLATFORM_ESCROW,
            owner_id=None,
        )

        LedgerEntryFactory(
            debit_account=external_account,
            credit_account=account,
            amount_cents=5000,
        )

        assert account.get_balance() == 5000

    def test_balance_after_single_debit(self, db, external_account):
        """Balance should reflect debits from the account."""
        # First fund the account
        account = LedgerAccountFactory(
            type=AccountType.USER_BALANCE,
        )
        LedgerEntryFactory(
            debit_account=external_account,
            credit_account=account,
            amount_cents=10000,
        )

        # Then debit
        dest = LedgerAccountFactory(type=AccountType.PLATFORM_REVENUE, owner_id=None)
        LedgerEntryFactory(
            debit_account=account,
            credit_account=dest,
            amount_cents=3000,
        )

        assert account.get_balance() == 7000

    def test_balance_after_multiple_operations(self, db, external_account):
        """Balance should correctly aggregate multiple credits and debits."""
        account = LedgerAccountFactory(type=AccountType.PLATFORM_ESCROW, owner_id=None)
        dest = LedgerAccountFactory(type=AccountType.USER_BALANCE)

        # Credit 10000
        LedgerEntryFactory(
            debit_account=external_account,
            credit_account=account,
            amount_cents=10000,
            idempotency_key=f"credit1-{uuid.uuid4()}",
        )

        # Credit 5000
        LedgerEntryFactory(
            debit_account=external_account,
            credit_account=account,
            amount_cents=5000,
            idempotency_key=f"credit2-{uuid.uuid4()}",
        )

        # Debit 3000
        LedgerEntryFactory(
            debit_account=account,
            credit_account=dest,
            amount_cents=3000,
            idempotency_key=f"debit1-{uuid.uuid4()}",
        )

        # Debit 2000
        LedgerEntryFactory(
            debit_account=account,
            credit_account=dest,
            amount_cents=2000,
            idempotency_key=f"debit2-{uuid.uuid4()}",
        )

        # 10000 + 5000 - 3000 - 2000 = 10000
        assert account.get_balance() == 10000

    def test_balance_negative_for_external_account(self, db):
        """External accounts can have negative balance."""
        external = LedgerAccountFactory(
            type=AccountType.EXTERNAL_STRIPE,
            owner_id=None,
            allow_negative=True,
        )
        escrow = LedgerAccountFactory(
            type=AccountType.PLATFORM_ESCROW,
            owner_id=None,
        )

        # Money comes in (debits external, credits escrow)
        LedgerEntryFactory(
            debit_account=external,
            credit_account=escrow,
            amount_cents=5000,
        )

        # External account should have negative balance
        assert external.get_balance() == -5000
        assert escrow.get_balance() == 5000


class TestLedgerEntry:
    """Tests for the LedgerEntry model."""

    def test_entry_created_with_uuid_primary_key(
        self, db, external_account, escrow_account
    ):
        """Entry should have a UUID as primary key."""
        entry = LedgerEntryFactory(
            debit_account=external_account,
            credit_account=escrow_account,
        )
        assert isinstance(entry.id, uuid.UUID)

    def test_entry_defaults(self, db, external_account, escrow_account):
        """Entry should have correct default values."""
        entry = LedgerEntry.objects.create(
            debit_account=external_account,
            credit_account=escrow_account,
            amount_cents=1000,
            entry_type=EntryType.TRANSFER,
            idempotency_key=f"test-{uuid.uuid4()}",
        )

        assert entry.currency == "usd"
        assert entry.metadata == {}
        assert entry.created_at is not None

    def test_entry_str_representation(self, db, external_account, escrow_account):
        """Entry string representation shows type and amount."""
        entry = LedgerEntryFactory(
            debit_account=external_account,
            credit_account=escrow_account,
            amount_cents=5000,
            entry_type=EntryType.PAYMENT_RECEIVED,
        )

        assert "5000" in str(entry)
        assert "cents" in str(entry)

    def test_entry_idempotency_key_unique(self, db, external_account, escrow_account):
        """Idempotency key must be unique across entries."""
        key = f"unique-key-{uuid.uuid4()}"
        LedgerEntryFactory(
            debit_account=external_account,
            credit_account=escrow_account,
            idempotency_key=key,
        )

        with pytest.raises(IntegrityError):
            LedgerEntryFactory(
                debit_account=external_account,
                credit_account=escrow_account,
                idempotency_key=key,
            )

    def test_entry_amount_must_be_positive(self, db, external_account, escrow_account):
        """Entry amount_cents must be positive (check constraint)."""
        with pytest.raises(IntegrityError):
            LedgerEntry.objects.create(
                debit_account=external_account,
                credit_account=escrow_account,
                amount_cents=0,
                entry_type=EntryType.TRANSFER,
                idempotency_key=f"zero-amount-{uuid.uuid4()}",
            )

    def test_entry_ordering_by_created_at_descending(
        self, db, external_account, escrow_account
    ):
        """Entries should be ordered by created_at descending (newest first)."""
        entry1 = LedgerEntryFactory(
            debit_account=external_account,
            credit_account=escrow_account,
            idempotency_key=f"entry1-{uuid.uuid4()}",
        )
        entry2 = LedgerEntryFactory(
            debit_account=external_account,
            credit_account=escrow_account,
            idempotency_key=f"entry2-{uuid.uuid4()}",
        )
        entry3 = LedgerEntryFactory(
            debit_account=external_account,
            credit_account=escrow_account,
            idempotency_key=f"entry3-{uuid.uuid4()}",
        )

        entries = list(LedgerEntry.objects.all())
        assert entries[0] == entry3
        assert entries[1] == entry2
        assert entries[2] == entry1

    def test_entry_protects_account_deletion(
        self, db, external_account, escrow_account
    ):
        """Cannot delete an account that has entries referencing it."""
        LedgerEntryFactory(
            debit_account=external_account,
            credit_account=escrow_account,
        )

        with pytest.raises(Exception):  # ProtectedError
            external_account.delete()

    def test_entry_with_reference(self, db, external_account, escrow_account):
        """Entry can store reference to business entity."""
        booking_id = uuid.uuid4()
        entry = LedgerEntryFactory(
            debit_account=external_account,
            credit_account=escrow_account,
            reference_type="booking",
            reference_id=booking_id,
        )

        assert entry.reference_type == "booking"
        assert entry.reference_id == booking_id

    def test_entry_with_metadata(self, db, external_account, escrow_account):
        """Entry can store arbitrary metadata."""
        metadata = {
            "stripe_payment_intent_id": "pi_123",
            "customer_email": "test@example.com",
        }
        entry = LedgerEntryFactory(
            debit_account=external_account,
            credit_account=escrow_account,
            metadata=metadata,
        )

        assert entry.metadata == metadata
        assert entry.metadata["stripe_payment_intent_id"] == "pi_123"
