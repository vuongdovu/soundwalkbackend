"""
Pytest fixtures for ledger tests.

This module provides fixtures for testing the ledger system, organized
into logical sections for clarity.

Sections:
    - Account Fixtures: Pre-configured ledger accounts
    - Entry Fixtures: Pre-configured ledger entries
    - Test Data Fixtures: UUIDs and other test data
"""

import uuid

import pytest

from payments.ledger.models import AccountType, EntryType
from payments.ledger.tests.factories import LedgerAccountFactory, LedgerEntryFactory


# ==========================================================================
# Account Fixtures
# ==========================================================================


@pytest.fixture
def external_account(db):
    """
    External Stripe account that can go negative.

    Represents money coming in from or going out to Stripe.
    This account is allowed to have a negative balance since
    it represents the external world.
    """
    return LedgerAccountFactory(
        type=AccountType.EXTERNAL_STRIPE,
        owner_id=None,
        allow_negative=True,
    )


@pytest.fixture
def escrow_account(db):
    """
    Platform escrow account.

    Holds money temporarily during transactions before release.
    Cannot go negative.
    """
    return LedgerAccountFactory(
        type=AccountType.PLATFORM_ESCROW,
        owner_id=None,
        allow_negative=False,
    )


@pytest.fixture
def revenue_account(db):
    """
    Platform revenue account.

    Accumulates platform fees and earnings.
    Cannot go negative.
    """
    return LedgerAccountFactory(
        type=AccountType.PLATFORM_REVENUE,
        owner_id=None,
        allow_negative=False,
    )


@pytest.fixture
def user_balance_account(db):
    """
    User balance account with a specific owner.

    Represents a user's available balance.
    Cannot go negative.
    """
    return LedgerAccountFactory(
        type=AccountType.USER_BALANCE,
        owner_id=uuid.uuid4(),
        allow_negative=False,
    )


@pytest.fixture
def inactive_account(db):
    """
    Inactive (deactivated) account.

    Used for testing that operations on inactive accounts are rejected.
    """
    return LedgerAccountFactory(
        type=AccountType.USER_BALANCE,
        owner_id=uuid.uuid4(),
        is_active=False,
    )


@pytest.fixture
def funded_escrow_account(db, external_account):
    """
    Escrow account with initial funding.

    Creates an escrow account with 10000 cents ($100) balance
    by recording a payment received entry.
    """
    escrow = LedgerAccountFactory(
        type=AccountType.PLATFORM_ESCROW,
        owner_id=None,
        allow_negative=False,
    )

    # Fund the account
    LedgerEntryFactory(
        debit_account=external_account,
        credit_account=escrow,
        amount_cents=10000,
        entry_type=EntryType.PAYMENT_RECEIVED,
        idempotency_key=f"fund-escrow-{uuid.uuid4()}",
    )

    return escrow


@pytest.fixture
def funded_user_account(db, external_account):
    """
    User balance account with initial funding.

    Creates a user balance account with 5000 cents ($50) balance.
    """
    user_account = LedgerAccountFactory(
        type=AccountType.USER_BALANCE,
        owner_id=uuid.uuid4(),
        allow_negative=False,
    )

    # Fund the account via escrow (realistic flow)
    escrow = LedgerAccountFactory(
        type=AccountType.PLATFORM_ESCROW,
        owner_id=None,
    )

    # Payment to escrow
    LedgerEntryFactory(
        debit_account=external_account,
        credit_account=escrow,
        amount_cents=5000,
        entry_type=EntryType.PAYMENT_RECEIVED,
        idempotency_key=f"fund-user-step1-{uuid.uuid4()}",
    )

    # Release to user
    LedgerEntryFactory(
        debit_account=escrow,
        credit_account=user_account,
        amount_cents=5000,
        entry_type=EntryType.PAYMENT_RELEASED,
        idempotency_key=f"fund-user-step2-{uuid.uuid4()}",
    )

    return user_account


# ==========================================================================
# Entry Fixtures
# ==========================================================================


@pytest.fixture
def basic_entry(db, external_account, escrow_account):
    """
    A basic ledger entry for testing.

    Creates a payment received entry moving money from
    external to escrow.
    """
    return LedgerEntryFactory(
        debit_account=external_account,
        credit_account=escrow_account,
        amount_cents=5000,
        entry_type=EntryType.PAYMENT_RECEIVED,
    )


# ==========================================================================
# Test Data Fixtures
# ==========================================================================


@pytest.fixture
def random_uuid():
    """Generate a random UUID for testing."""
    return uuid.uuid4()


@pytest.fixture
def unique_idempotency_key():
    """Generate a unique idempotency key for testing."""
    return f"test-{uuid.uuid4()}"
