"""
Tests for LedgerService.

This module tests the service layer for ledger operations,
including account management, entry recording, and balance queries.
"""

import uuid

import pytest

from payments.ledger.exceptions import (
    AccountNotFound,
    InactiveAccount,
    InsufficientBalance,
)
from payments.ledger.models import AccountType, EntryType, LedgerEntry
from payments.ledger.services import LedgerService
from payments.ledger.tests.factories import LedgerAccountFactory, LedgerEntryFactory
from payments.ledger.types import Money, RecordEntryParams


class TestGetOrCreateAccount:
    """Tests for LedgerService.get_or_create_account()."""

    def test_creates_new_account_when_none_exists(self, db):
        """Should create a new account if one doesn't exist."""
        account = LedgerService.get_or_create_account(
            account_type=AccountType.PLATFORM_ESCROW,
        )

        assert account.id is not None
        assert account.type == AccountType.PLATFORM_ESCROW
        assert account.currency == "usd"

    def test_returns_existing_account_when_exists(self, db):
        """Should return existing account instead of creating duplicate."""
        account1 = LedgerService.get_or_create_account(
            account_type=AccountType.PLATFORM_ESCROW,
        )
        account2 = LedgerService.get_or_create_account(
            account_type=AccountType.PLATFORM_ESCROW,
        )

        assert account1.id == account2.id

    def test_creates_account_with_owner_id(self, db):
        """Should create account associated with owner."""
        owner_id = uuid.uuid4()
        account = LedgerService.get_or_create_account(
            account_type=AccountType.USER_BALANCE,
            owner_id=owner_id,
        )

        assert account.owner_id == owner_id

    def test_different_currencies_create_different_accounts(self, db):
        """Same type/owner with different currency creates separate accounts."""
        usd = LedgerService.get_or_create_account(
            account_type=AccountType.PLATFORM_ESCROW,
            currency="usd",
        )
        eur = LedgerService.get_or_create_account(
            account_type=AccountType.PLATFORM_ESCROW,
            currency="eur",
        )

        assert usd.id != eur.id

    def test_allow_negative_flag_set_on_creation(self, db):
        """Should set allow_negative flag when creating new account."""
        account = LedgerService.get_or_create_account(
            account_type=AccountType.EXTERNAL_STRIPE,
            allow_negative=True,
        )

        assert account.allow_negative is True


class TestGetAccount:
    """Tests for LedgerService.get_account()."""

    def test_returns_account_by_id(self, db):
        """Should return account when found by ID."""
        created = LedgerAccountFactory()
        fetched = LedgerService.get_account(created.id)

        assert fetched.id == created.id

    def test_raises_account_not_found_for_invalid_id(self, db):
        """Should raise AccountNotFound for non-existent ID."""
        fake_id = uuid.uuid4()

        with pytest.raises(AccountNotFound) as exc_info:
            LedgerService.get_account(fake_id)

        assert str(fake_id) in str(exc_info.value)


class TestGetAccountByOwner:
    """Tests for LedgerService.get_account_by_owner()."""

    def test_returns_account_by_type_and_owner(self, db):
        """Should return account matching type and owner."""
        owner_id = uuid.uuid4()
        created = LedgerAccountFactory(
            type=AccountType.USER_BALANCE,
            owner_id=owner_id,
        )

        fetched = LedgerService.get_account_by_owner(
            account_type=AccountType.USER_BALANCE,
            owner_id=owner_id,
        )

        assert fetched.id == created.id

    def test_returns_none_when_not_found(self, db):
        """Should return None when no matching account exists."""
        result = LedgerService.get_account_by_owner(
            account_type=AccountType.USER_BALANCE,
            owner_id=uuid.uuid4(),
        )

        assert result is None


class TestRecordEntry:
    """Tests for LedgerService.record_entry()."""

    def test_records_single_entry_successfully(
        self, db, external_account, escrow_account
    ):
        """Should record a single entry and return it."""
        params = RecordEntryParams(
            debit_account_id=external_account.id,
            credit_account_id=escrow_account.id,
            amount_cents=5000,
            entry_type=EntryType.PAYMENT_RECEIVED,
            idempotency_key=f"test-{uuid.uuid4()}",
        )

        entry = LedgerService.record_entry(params)

        assert entry.amount_cents == 5000
        assert entry.debit_account_id == external_account.id
        assert entry.credit_account_id == escrow_account.id

    def test_idempotency_returns_existing_entry_for_same_key(
        self, db, external_account, escrow_account
    ):
        """Should return existing entry if idempotency key matches."""
        key = f"idempotent-{uuid.uuid4()}"
        params = RecordEntryParams(
            debit_account_id=external_account.id,
            credit_account_id=escrow_account.id,
            amount_cents=5000,
            entry_type=EntryType.PAYMENT_RECEIVED,
            idempotency_key=key,
        )

        entry1 = LedgerService.record_entry(params)
        entry2 = LedgerService.record_entry(params)

        assert entry1.id == entry2.id
        assert LedgerEntry.objects.filter(idempotency_key=key).count() == 1

    def test_raises_insufficient_balance_when_debit_exceeds_balance(
        self, db, escrow_account, revenue_account
    ):
        """Should raise InsufficientBalance when account lacks funds."""
        # escrow_account has 0 balance
        params = RecordEntryParams(
            debit_account_id=escrow_account.id,
            credit_account_id=revenue_account.id,
            amount_cents=5000,
            entry_type=EntryType.TRANSFER,
            idempotency_key=f"test-{uuid.uuid4()}",
        )

        with pytest.raises(InsufficientBalance) as exc_info:
            LedgerService.record_entry(params)

        assert exc_info.value.required == 5000
        assert exc_info.value.available == 0

    def test_allows_negative_balance_when_account_permits(
        self, db, external_account, escrow_account
    ):
        """Should allow debit when account.allow_negative is True."""
        # external_account has allow_negative=True
        params = RecordEntryParams(
            debit_account_id=external_account.id,
            credit_account_id=escrow_account.id,
            amount_cents=5000,
            entry_type=EntryType.PAYMENT_RECEIVED,
            idempotency_key=f"test-{uuid.uuid4()}",
        )

        entry = LedgerService.record_entry(params)

        assert entry.amount_cents == 5000
        assert external_account.get_balance() == -5000

    def test_raises_inactive_account_on_debit(
        self, db, inactive_account, escrow_account
    ):
        """Should raise InactiveAccount when debiting inactive account."""
        params = RecordEntryParams(
            debit_account_id=inactive_account.id,
            credit_account_id=escrow_account.id,
            amount_cents=1000,
            entry_type=EntryType.TRANSFER,
            idempotency_key=f"test-{uuid.uuid4()}",
        )

        with pytest.raises(InactiveAccount):
            LedgerService.record_entry(params)

    def test_raises_inactive_account_on_credit(
        self, db, external_account, inactive_account
    ):
        """Should raise InactiveAccount when crediting inactive account."""
        params = RecordEntryParams(
            debit_account_id=external_account.id,
            credit_account_id=inactive_account.id,
            amount_cents=1000,
            entry_type=EntryType.TRANSFER,
            idempotency_key=f"test-{uuid.uuid4()}",
        )

        with pytest.raises(InactiveAccount):
            LedgerService.record_entry(params)

    def test_raises_account_not_found_for_invalid_debit_account(
        self, db, escrow_account
    ):
        """Should raise AccountNotFound for non-existent debit account."""
        params = RecordEntryParams(
            debit_account_id=uuid.uuid4(),
            credit_account_id=escrow_account.id,
            amount_cents=1000,
            entry_type=EntryType.TRANSFER,
            idempotency_key=f"test-{uuid.uuid4()}",
        )

        with pytest.raises(AccountNotFound):
            LedgerService.record_entry(params)

    def test_raises_account_not_found_for_invalid_credit_account(
        self, db, external_account
    ):
        """Should raise AccountNotFound for non-existent credit account."""
        params = RecordEntryParams(
            debit_account_id=external_account.id,
            credit_account_id=uuid.uuid4(),
            amount_cents=1000,
            entry_type=EntryType.TRANSFER,
            idempotency_key=f"test-{uuid.uuid4()}",
        )

        with pytest.raises(AccountNotFound):
            LedgerService.record_entry(params)


class TestRecordEntries:
    """Tests for LedgerService.record_entries()."""

    def test_records_multiple_entries_atomically(
        self, db, external_account, escrow_account, revenue_account
    ):
        """Should record multiple entries in a single transaction."""
        params_list = [
            RecordEntryParams(
                debit_account_id=external_account.id,
                credit_account_id=escrow_account.id,
                amount_cents=10000,
                entry_type=EntryType.PAYMENT_RECEIVED,
                idempotency_key=f"batch1-{uuid.uuid4()}",
            ),
            RecordEntryParams(
                debit_account_id=escrow_account.id,
                credit_account_id=revenue_account.id,
                amount_cents=1000,
                entry_type=EntryType.FEE_COLLECTED,
                idempotency_key=f"batch2-{uuid.uuid4()}",
            ),
        ]

        entries = LedgerService.record_entries(params_list)

        assert len(entries) == 2
        assert escrow_account.get_balance() == 9000  # 10000 - 1000
        assert revenue_account.get_balance() == 1000

    def test_rolls_back_all_on_second_entry_failure(
        self, db, external_account, escrow_account, user_balance_account
    ):
        """If any entry fails validation, all entries should roll back."""
        # First entry would succeed, second would fail (insufficient balance)
        params_list = [
            RecordEntryParams(
                debit_account_id=external_account.id,
                credit_account_id=escrow_account.id,
                amount_cents=5000,
                entry_type=EntryType.PAYMENT_RECEIVED,
                idempotency_key=f"rollback1-{uuid.uuid4()}",
            ),
            RecordEntryParams(
                debit_account_id=user_balance_account.id,  # Has 0 balance
                credit_account_id=escrow_account.id,
                amount_cents=1000,
                entry_type=EntryType.TRANSFER,
                idempotency_key=f"rollback2-{uuid.uuid4()}",
            ),
        ]

        with pytest.raises(InsufficientBalance):
            LedgerService.record_entries(params_list)

        # First entry should have been rolled back
        assert escrow_account.get_balance() == 0
        assert LedgerEntry.objects.count() == 0

    def test_sequential_balance_validation_within_batch(
        self, db, external_account, escrow_account, user_balance_account
    ):
        """Entries within batch should see balance changes from previous entries."""
        # Fund escrow first
        LedgerEntryFactory(
            debit_account=external_account,
            credit_account=escrow_account,
            amount_cents=10000,
        )

        # Now try to debit escrow twice for 6000 each (total 12000 > 10000)
        params_list = [
            RecordEntryParams(
                debit_account_id=escrow_account.id,
                credit_account_id=user_balance_account.id,
                amount_cents=6000,
                entry_type=EntryType.PAYMENT_RELEASED,
                idempotency_key=f"seq1-{uuid.uuid4()}",
            ),
            RecordEntryParams(
                debit_account_id=escrow_account.id,
                credit_account_id=user_balance_account.id,
                amount_cents=6000,
                entry_type=EntryType.PAYMENT_RELEASED,
                idempotency_key=f"seq2-{uuid.uuid4()}",
            ),
        ]

        # Second entry should fail because first reduced balance to 4000
        with pytest.raises(InsufficientBalance) as exc_info:
            LedgerService.record_entries(params_list)

        assert exc_info.value.available == 4000
        assert exc_info.value.required == 6000

    def test_empty_entries_list_returns_empty(self, db):
        """Empty params list should return empty results."""
        entries = LedgerService.record_entries([])
        assert entries == []


class TestTransfer:
    """Tests for LedgerService.transfer()."""

    def test_transfer_creates_entry_with_transfer_type(self, db, funded_escrow_account):
        """Transfer convenience method should create TRANSFER entry type."""
        dest = LedgerAccountFactory(type=AccountType.USER_BALANCE)

        entry = LedgerService.transfer(
            from_account_id=funded_escrow_account.id,
            to_account_id=dest.id,
            amount_cents=5000,
            idempotency_key=f"transfer-{uuid.uuid4()}",
        )

        assert entry.entry_type == EntryType.TRANSFER
        assert entry.debit_account_id == funded_escrow_account.id
        assert entry.credit_account_id == dest.id

    def test_transfer_with_description_and_reference(self, db, funded_escrow_account):
        """Transfer should accept optional description and reference."""
        dest = LedgerAccountFactory(type=AccountType.USER_BALANCE)
        ref_id = uuid.uuid4()

        entry = LedgerService.transfer(
            from_account_id=funded_escrow_account.id,
            to_account_id=dest.id,
            amount_cents=2000,
            idempotency_key=f"transfer-ref-{uuid.uuid4()}",
            description="Payment for services",
            reference_type="booking",
            reference_id=ref_id,
            created_by="booking_service",
        )

        assert entry.description == "Payment for services"
        assert entry.reference_type == "booking"
        assert entry.reference_id == ref_id
        assert entry.created_by == "booking_service"


class TestGetBalance:
    """Tests for LedgerService.get_balance()."""

    def test_returns_money_object_with_balance(self, db, funded_escrow_account):
        """Should return Money object with current balance."""
        balance = LedgerService.get_balance(funded_escrow_account.id)

        assert isinstance(balance, Money)
        assert balance.cents == 10000  # Funded with 10000 cents
        assert balance.currency == "usd"

    def test_raises_account_not_found_for_invalid_id(self, db):
        """Should raise AccountNotFound for non-existent account."""
        with pytest.raises(AccountNotFound):
            LedgerService.get_balance(uuid.uuid4())


class TestGetEntriesForAccount:
    """Tests for LedgerService.get_entries_for_account()."""

    def test_returns_entries_paginated(self, db, external_account, escrow_account):
        """Should return paginated list of entries."""
        # Create 5 entries
        for i in range(5):
            LedgerEntryFactory(
                debit_account=external_account,
                credit_account=escrow_account,
                idempotency_key=f"paginate-{i}-{uuid.uuid4()}",
            )

        entries = LedgerService.get_entries_for_account(
            account_id=escrow_account.id,
            limit=3,
            offset=0,
        )

        assert len(entries) == 3

    def test_returns_both_debit_and_credit_entries(
        self, db, external_account, escrow_account
    ):
        """Should return entries where account is debited OR credited."""
        # Entry where escrow is credited
        LedgerEntryFactory(
            debit_account=external_account,
            credit_account=escrow_account,
            idempotency_key=f"credit-{uuid.uuid4()}",
        )

        dest = LedgerAccountFactory(type=AccountType.USER_BALANCE)

        # Fund escrow first
        LedgerEntryFactory(
            debit_account=external_account,
            credit_account=escrow_account,
            amount_cents=5000,
            idempotency_key=f"fund-{uuid.uuid4()}",
        )

        # Entry where escrow is debited
        LedgerEntryFactory(
            debit_account=escrow_account,
            credit_account=dest,
            amount_cents=1000,
            idempotency_key=f"debit-{uuid.uuid4()}",
        )

        entries = LedgerService.get_entries_for_account(escrow_account.id)

        assert len(entries) == 3  # 2 credits + 1 debit

    def test_entries_ordered_by_created_at_descending(
        self, db, external_account, escrow_account
    ):
        """Entries should be returned newest first."""
        entry1 = LedgerEntryFactory(
            debit_account=external_account,
            credit_account=escrow_account,
            idempotency_key=f"order1-{uuid.uuid4()}",
        )
        entry2 = LedgerEntryFactory(
            debit_account=external_account,
            credit_account=escrow_account,
            idempotency_key=f"order2-{uuid.uuid4()}",
        )

        entries = LedgerService.get_entries_for_account(escrow_account.id)

        assert entries[0].id == entry2.id
        assert entries[1].id == entry1.id


class TestGetEntriesByReference:
    """Tests for LedgerService.get_entries_by_reference()."""

    def test_returns_entries_matching_reference(
        self, db, external_account, escrow_account
    ):
        """Should return all entries with matching reference."""
        booking_id = uuid.uuid4()

        LedgerEntryFactory(
            debit_account=external_account,
            credit_account=escrow_account,
            reference_type="booking",
            reference_id=booking_id,
            idempotency_key=f"ref1-{uuid.uuid4()}",
        )
        LedgerEntryFactory(
            debit_account=external_account,
            credit_account=escrow_account,
            reference_type="booking",
            reference_id=booking_id,
            idempotency_key=f"ref2-{uuid.uuid4()}",
        )
        # Different booking
        LedgerEntryFactory(
            debit_account=external_account,
            credit_account=escrow_account,
            reference_type="booking",
            reference_id=uuid.uuid4(),
            idempotency_key=f"ref3-{uuid.uuid4()}",
        )

        entries = LedgerService.get_entries_by_reference("booking", booking_id)

        assert len(entries) == 2

    def test_returns_empty_for_no_match(self, db):
        """Should return empty list when no entries match."""
        entries = LedgerService.get_entries_by_reference("booking", uuid.uuid4())
        assert entries == []


class TestAccountLifecycle:
    """Tests for account activation/deactivation."""

    def test_deactivate_account(self, db):
        """Should mark account as inactive."""
        account = LedgerAccountFactory(is_active=True)

        result = LedgerService.deactivate_account(account.id)

        assert result.is_active is False
        account.refresh_from_db()
        assert account.is_active is False

    def test_reactivate_account(self, db):
        """Should mark account as active."""
        account = LedgerAccountFactory(is_active=False)

        result = LedgerService.reactivate_account(account.id)

        assert result.is_active is True
        account.refresh_from_db()
        assert account.is_active is True

    def test_deactivate_nonexistent_raises_not_found(self, db):
        """Should raise AccountNotFound for non-existent account."""
        with pytest.raises(AccountNotFound):
            LedgerService.deactivate_account(uuid.uuid4())
