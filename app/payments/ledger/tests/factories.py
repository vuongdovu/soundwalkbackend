"""
Factory Boy factories for ledger test data.

This module provides factories for creating test instances of ledger models.
Factories generate realistic test data while allowing easy customization.

Usage:
    from payments.ledger.tests.factories import LedgerAccountFactory, LedgerEntryFactory

    # Create a basic account
    account = LedgerAccountFactory()

    # Create account with specific type
    escrow = LedgerAccountFactory(type=AccountType.PLATFORM_ESCROW)

    # Create an entry between two accounts
    entry = LedgerEntryFactory(
        debit_account=source_account,
        credit_account=dest_account,
        amount_cents=5000,
    )
"""

import uuid

import factory

from payments.ledger.models import AccountType, EntryType, LedgerAccount, LedgerEntry


class LedgerAccountFactory(factory.django.DjangoModelFactory):
    """
    Factory for creating LedgerAccount instances.

    Default creates a USER_BALANCE account with a unique owner_id.

    Example:
        # Default user balance account
        account = LedgerAccountFactory()

        # Platform escrow (no owner)
        escrow = LedgerAccountFactory(
            type=AccountType.PLATFORM_ESCROW,
            owner_id=None,
        )

        # External account that can go negative
        external = LedgerAccountFactory(
            type=AccountType.EXTERNAL_STRIPE,
            owner_id=None,
            allow_negative=True,
        )
    """

    class Meta:
        model = LedgerAccount
        skip_postgeneration_save = True

    type = AccountType.USER_BALANCE
    owner_id = factory.LazyFunction(uuid.uuid4)
    currency = "usd"
    allow_negative = False
    is_active = True


class LedgerEntryFactory(factory.django.DjangoModelFactory):
    """
    Factory for creating LedgerEntry instances.

    Requires debit_account and credit_account to be provided or
    will create new accounts. Generates unique idempotency keys.

    Example:
        # Create entry with new accounts
        entry = LedgerEntryFactory(amount_cents=5000)

        # Create entry with existing accounts
        entry = LedgerEntryFactory(
            debit_account=source,
            credit_account=dest,
            amount_cents=10000,
            entry_type=EntryType.PAYMENT_RECEIVED,
        )

        # Create entry with reference
        entry = LedgerEntryFactory(
            reference_type='booking',
            reference_id=booking.id,
        )
    """

    class Meta:
        model = LedgerEntry
        skip_postgeneration_save = True

    debit_account = factory.SubFactory(
        LedgerAccountFactory,
        type=AccountType.EXTERNAL_STRIPE,
        owner_id=None,
        allow_negative=True,
    )
    credit_account = factory.SubFactory(
        LedgerAccountFactory,
        type=AccountType.PLATFORM_ESCROW,
        owner_id=None,
    )
    amount_cents = 1000
    currency = "usd"
    entry_type = EntryType.TRANSFER
    idempotency_key = factory.Sequence(lambda n: f"test-entry-{n}-{uuid.uuid4()}")

    reference_id = None
    reference_type = None
    description = factory.Faker("sentence")
    metadata = factory.LazyFunction(dict)
    created_by = "test_factory"
