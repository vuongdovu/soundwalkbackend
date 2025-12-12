"""
Integration tests for ledger operations.

This module tests complex scenarios that span multiple service calls
and verify atomic transaction behavior.
"""

import uuid

import pytest

from payments.ledger.exceptions import InsufficientBalance
from payments.ledger.models import AccountType, EntryType, LedgerEntry
from payments.ledger.services import LedgerService
from payments.ledger.tests.factories import LedgerAccountFactory, LedgerEntryFactory
from payments.ledger.types import RecordEntryParams


@pytest.mark.integration
class TestAtomicRollback:
    """Tests for atomic transaction rollback behavior."""

    def test_multi_entry_rollback_on_failure(self, db):
        """
        When recording multiple entries, if any fails, all should roll back.

        This tests the atomic transaction guarantee: either all entries
        succeed or none do.
        """
        external = LedgerAccountFactory(
            type=AccountType.EXTERNAL_STRIPE,
            owner_id=None,
            allow_negative=True,
        )
        escrow = LedgerAccountFactory(
            type=AccountType.PLATFORM_ESCROW,
            owner_id=None,
        )
        revenue = LedgerAccountFactory(
            type=AccountType.PLATFORM_REVENUE,
            owner_id=None,
        )
        user = LedgerAccountFactory(
            type=AccountType.USER_BALANCE,
        )

        # First fund escrow
        LedgerEntryFactory(
            debit_account=external,
            credit_account=escrow,
            amount_cents=5000,
        )

        initial_escrow_balance = escrow.get_balance()
        initial_entry_count = LedgerEntry.objects.count()

        # Try to record entries where the third will fail
        params_list = [
            # This should succeed
            RecordEntryParams(
                debit_account_id=escrow.id,
                credit_account_id=revenue.id,
                amount_cents=500,
                entry_type=EntryType.FEE_COLLECTED,
                idempotency_key=f"atomic1-{uuid.uuid4()}",
            ),
            # This should succeed (escrow has 4500 after first)
            RecordEntryParams(
                debit_account_id=escrow.id,
                credit_account_id=user.id,
                amount_cents=4000,
                entry_type=EntryType.PAYMENT_RELEASED,
                idempotency_key=f"atomic2-{uuid.uuid4()}",
            ),
            # This should fail (escrow only has 500 left, need 1000)
            RecordEntryParams(
                debit_account_id=escrow.id,
                credit_account_id=user.id,
                amount_cents=1000,
                entry_type=EntryType.PAYMENT_RELEASED,
                idempotency_key=f"atomic3-{uuid.uuid4()}",
            ),
        ]

        with pytest.raises(InsufficientBalance):
            LedgerService.record_entries(params_list)

        # Verify rollback - balances and entry count should be unchanged
        assert escrow.get_balance() == initial_escrow_balance
        assert revenue.get_balance() == 0
        assert user.get_balance() == 0
        assert LedgerEntry.objects.count() == initial_entry_count

    def test_partial_batch_with_idempotent_entries(self, db):
        """
        When some entries in a batch already exist (idempotency),
        new entries should still be recorded correctly.
        """
        external = LedgerAccountFactory(
            type=AccountType.EXTERNAL_STRIPE,
            owner_id=None,
            allow_negative=True,
        )
        escrow = LedgerAccountFactory(
            type=AccountType.PLATFORM_ESCROW,
            owner_id=None,
        )

        # Create first entry outside the batch
        existing_key = f"existing-{uuid.uuid4()}"
        existing_entry = LedgerEntryFactory(
            debit_account=external,
            credit_account=escrow,
            amount_cents=3000,
            idempotency_key=existing_key,
        )

        # Now submit a batch that includes the existing entry
        params_list = [
            # This already exists
            RecordEntryParams(
                debit_account_id=external.id,
                credit_account_id=escrow.id,
                amount_cents=3000,
                entry_type=EntryType.PAYMENT_RECEIVED,
                idempotency_key=existing_key,
            ),
            # This is new
            RecordEntryParams(
                debit_account_id=external.id,
                credit_account_id=escrow.id,
                amount_cents=2000,
                entry_type=EntryType.PAYMENT_RECEIVED,
                idempotency_key=f"new-{uuid.uuid4()}",
            ),
        ]

        entries = LedgerService.record_entries(params_list)

        assert len(entries) == 2
        assert entries[0].id == existing_entry.id  # Returned existing
        assert entries[1].amount_cents == 2000  # Created new

        # Balance should only reflect 3000 (existing) + 2000 (new) = 5000
        assert escrow.get_balance() == 5000


@pytest.mark.integration
class TestComplexWorkflows:
    """Tests for realistic payment workflows."""

    def test_full_payment_flow(self, db):
        """
        Test a complete payment flow:
        1. Payment received from Stripe to escrow
        2. Platform fee collected
        3. Remainder released to provider
        """
        external = LedgerService.get_or_create_account(
            AccountType.EXTERNAL_STRIPE,
            allow_negative=True,
        )
        escrow = LedgerService.get_or_create_account(AccountType.PLATFORM_ESCROW)
        revenue = LedgerService.get_or_create_account(AccountType.PLATFORM_REVENUE)

        provider_id = uuid.uuid4()
        provider = LedgerService.get_or_create_account(
            AccountType.USER_BALANCE,
            owner_id=provider_id,
        )

        booking_id = uuid.uuid4()
        payment_amount = 10000  # $100
        fee_amount = 1500  # $15 (15%)
        provider_amount = payment_amount - fee_amount  # $85

        # Step 1: Payment received
        LedgerService.record_entry(
            RecordEntryParams(
                debit_account_id=external.id,
                credit_account_id=escrow.id,
                amount_cents=payment_amount,
                entry_type=EntryType.PAYMENT_RECEIVED,
                reference_type="booking",
                reference_id=booking_id,
                idempotency_key=f"payment:{booking_id}",
                created_by="stripe_webhook",
            )
        )

        assert escrow.get_balance() == payment_amount

        # Step 2 & 3: Fee collection and release (atomic)
        LedgerService.record_entries(
            [
                RecordEntryParams(
                    debit_account_id=escrow.id,
                    credit_account_id=revenue.id,
                    amount_cents=fee_amount,
                    entry_type=EntryType.FEE_COLLECTED,
                    reference_type="booking",
                    reference_id=booking_id,
                    idempotency_key=f"fee:{booking_id}",
                    created_by="booking_service",
                ),
                RecordEntryParams(
                    debit_account_id=escrow.id,
                    credit_account_id=provider.id,
                    amount_cents=provider_amount,
                    entry_type=EntryType.PAYMENT_RELEASED,
                    reference_type="booking",
                    reference_id=booking_id,
                    idempotency_key=f"release:{booking_id}",
                    created_by="booking_service",
                ),
            ]
        )

        # Verify final balances
        assert external.get_balance() == -payment_amount
        assert escrow.get_balance() == 0
        assert revenue.get_balance() == fee_amount
        assert provider.get_balance() == provider_amount

        # Verify all entries linked to booking
        entries = LedgerService.get_entries_by_reference("booking", booking_id)
        assert len(entries) == 3

    def test_refund_flow(self, db):
        """
        Test a refund flow that reverses a payment.
        """
        external = LedgerService.get_or_create_account(
            AccountType.EXTERNAL_STRIPE,
            allow_negative=True,
        )
        escrow = LedgerService.get_or_create_account(AccountType.PLATFORM_ESCROW)
        revenue = LedgerService.get_or_create_account(AccountType.PLATFORM_REVENUE)

        provider_id = uuid.uuid4()
        provider = LedgerService.get_or_create_account(
            AccountType.USER_BALANCE,
            owner_id=provider_id,
        )

        booking_id = uuid.uuid4()

        # Initial payment flow
        LedgerService.record_entry(
            RecordEntryParams(
                debit_account_id=external.id,
                credit_account_id=escrow.id,
                amount_cents=10000,
                entry_type=EntryType.PAYMENT_RECEIVED,
                reference_type="booking",
                reference_id=booking_id,
                idempotency_key=f"payment:{booking_id}",
            )
        )

        LedgerService.record_entries(
            [
                RecordEntryParams(
                    debit_account_id=escrow.id,
                    credit_account_id=revenue.id,
                    amount_cents=1500,
                    entry_type=EntryType.FEE_COLLECTED,
                    reference_type="booking",
                    reference_id=booking_id,
                    idempotency_key=f"fee:{booking_id}",
                ),
                RecordEntryParams(
                    debit_account_id=escrow.id,
                    credit_account_id=provider.id,
                    amount_cents=8500,
                    entry_type=EntryType.PAYMENT_RELEASED,
                    reference_type="booking",
                    reference_id=booking_id,
                    idempotency_key=f"release:{booking_id}",
                ),
            ]
        )

        # Now process refund (reverse the transactions)
        refund_id = uuid.uuid4()
        LedgerService.record_entries(
            [
                # Take back from provider
                RecordEntryParams(
                    debit_account_id=provider.id,
                    credit_account_id=escrow.id,
                    amount_cents=8500,
                    entry_type=EntryType.REFUND,
                    reference_type="refund",
                    reference_id=refund_id,
                    idempotency_key=f"refund-provider:{refund_id}",
                ),
                # Return fee to escrow (platform absorbs the fee loss)
                RecordEntryParams(
                    debit_account_id=revenue.id,
                    credit_account_id=escrow.id,
                    amount_cents=1500,
                    entry_type=EntryType.REFUND,
                    reference_type="refund",
                    reference_id=refund_id,
                    idempotency_key=f"refund-fee:{refund_id}",
                ),
                # Send back to customer via Stripe
                RecordEntryParams(
                    debit_account_id=escrow.id,
                    credit_account_id=external.id,
                    amount_cents=10000,
                    entry_type=EntryType.REFUND,
                    reference_type="refund",
                    reference_id=refund_id,
                    idempotency_key=f"refund-stripe:{refund_id}",
                ),
            ]
        )

        # All accounts should be back to zero
        assert external.get_balance() == 0
        assert escrow.get_balance() == 0
        assert revenue.get_balance() == 0
        assert provider.get_balance() == 0

    def test_user_to_user_transfer(self, db):
        """
        Test direct transfer between two user accounts.
        """
        external = LedgerService.get_or_create_account(
            AccountType.EXTERNAL_STRIPE,
            allow_negative=True,
        )

        sender_id = uuid.uuid4()
        receiver_id = uuid.uuid4()

        sender = LedgerService.get_or_create_account(
            AccountType.USER_BALANCE,
            owner_id=sender_id,
        )
        receiver = LedgerService.get_or_create_account(
            AccountType.USER_BALANCE,
            owner_id=receiver_id,
        )

        # Fund sender account
        LedgerService.record_entry(
            RecordEntryParams(
                debit_account_id=external.id,
                credit_account_id=sender.id,
                amount_cents=5000,
                entry_type=EntryType.PAYMENT_RECEIVED,
                idempotency_key=f"fund-sender-{uuid.uuid4()}",
            )
        )

        # Transfer from sender to receiver
        LedgerService.transfer(
            from_account_id=sender.id,
            to_account_id=receiver.id,
            amount_cents=2000,
            idempotency_key=f"user-transfer-{uuid.uuid4()}",
            description="Gift from friend",
            created_by="transfer_service",
        )

        assert sender.get_balance() == 3000
        assert receiver.get_balance() == 2000


@pytest.mark.integration
class TestBalanceConsistency:
    """Tests to verify ledger balance consistency."""

    def test_total_balance_across_all_accounts_is_zero(self, db):
        """
        In a closed system, the sum of all account balances should be zero.

        External accounts represent money entering/leaving the system,
        so including them, the net should always be zero.
        """
        external = LedgerService.get_or_create_account(
            AccountType.EXTERNAL_STRIPE,
            allow_negative=True,
        )
        escrow = LedgerService.get_or_create_account(AccountType.PLATFORM_ESCROW)
        revenue = LedgerService.get_or_create_account(AccountType.PLATFORM_REVENUE)

        user1 = LedgerService.get_or_create_account(
            AccountType.USER_BALANCE,
            owner_id=uuid.uuid4(),
        )
        user2 = LedgerService.get_or_create_account(
            AccountType.USER_BALANCE,
            owner_id=uuid.uuid4(),
        )

        # Simulate various transactions
        LedgerService.record_entry(
            RecordEntryParams(
                debit_account_id=external.id,
                credit_account_id=escrow.id,
                amount_cents=20000,
                entry_type=EntryType.PAYMENT_RECEIVED,
                idempotency_key=f"consistency1-{uuid.uuid4()}",
            )
        )

        LedgerService.record_entries(
            [
                RecordEntryParams(
                    debit_account_id=escrow.id,
                    credit_account_id=revenue.id,
                    amount_cents=2000,
                    entry_type=EntryType.FEE_COLLECTED,
                    idempotency_key=f"consistency2-{uuid.uuid4()}",
                ),
                RecordEntryParams(
                    debit_account_id=escrow.id,
                    credit_account_id=user1.id,
                    amount_cents=10000,
                    entry_type=EntryType.PAYMENT_RELEASED,
                    idempotency_key=f"consistency3-{uuid.uuid4()}",
                ),
                RecordEntryParams(
                    debit_account_id=escrow.id,
                    credit_account_id=user2.id,
                    amount_cents=8000,
                    entry_type=EntryType.PAYMENT_RELEASED,
                    idempotency_key=f"consistency4-{uuid.uuid4()}",
                ),
            ]
        )

        LedgerService.transfer(
            from_account_id=user1.id,
            to_account_id=user2.id,
            amount_cents=3000,
            idempotency_key=f"consistency5-{uuid.uuid4()}",
        )

        # Calculate total balance
        total = (
            external.get_balance()
            + escrow.get_balance()
            + revenue.get_balance()
            + user1.get_balance()
            + user2.get_balance()
        )

        assert total == 0, f"Total balance should be 0, got {total}"

        # Verify individual balances make sense
        assert external.get_balance() == -20000  # Money came in
        assert escrow.get_balance() == 0  # All distributed
        assert revenue.get_balance() == 2000  # Fees collected
        assert user1.get_balance() == 7000  # 10000 - 3000 transferred
        assert user2.get_balance() == 11000  # 8000 + 3000 received
