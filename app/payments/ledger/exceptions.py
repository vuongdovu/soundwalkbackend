"""
Ledger-specific exceptions for financial operations.

This module provides a hierarchy of exceptions for ledger operations,
inheriting from the core exception base class for API consistency.

Exception Hierarchy:
    LedgerError (base)
    ├── AccountNotFound - Account lookup failures
    ├── InsufficientBalance - Balance validation failures
    └── InactiveAccount - Operations on inactive accounts

Usage:
    from payments.ledger.exceptions import InsufficientBalance, AccountNotFound

    # Check balance before transfer
    if balance < amount:
        raise InsufficientBalance(account.id, required=amount, available=balance)

    # Account not found
    raise AccountNotFound(f"Account {account_id} not found")
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.exceptions import BaseApplicationError

if TYPE_CHECKING:
    import uuid
    from typing import Any


class LedgerError(BaseApplicationError):
    """
    Base exception for all ledger operations.

    All ledger-specific exceptions inherit from this class,
    which itself inherits from BaseApplicationError for
    consistent API error responses.

    Example:
        try:
            ledger.record_entry(params)
        except LedgerError as e:
            logger.error(f"Ledger operation failed: {e}")
            return Response(e.to_dict(), status=400)
    """

    default_error_code: str = "LEDGER_ERROR"


class AccountNotFound(LedgerError):
    """
    Raised when a ledger account cannot be found.

    Use for:
    - Account lookup by ID fails
    - Account lookup by owner/type fails
    - Referenced account in entry doesn't exist

    Example:
        account = LedgerAccount.objects.filter(id=account_id).first()
        if not account:
            raise AccountNotFound(
                f"Account {account_id} not found",
                details={"account_id": str(account_id)}
            )
    """

    default_error_code: str = "ACCOUNT_NOT_FOUND"


class InsufficientBalance(LedgerError):
    """
    Raised when an account has insufficient funds for an operation.

    Stores the account ID, required amount, and available balance
    for detailed error reporting.

    Attributes:
        account_id: The UUID of the account with insufficient funds
        required: The amount (in cents) that was required
        available: The amount (in cents) that was available

    Example:
        if current_balance < amount_cents:
            raise InsufficientBalance(
                account.id,
                required=amount_cents,
                available=current_balance
            )
    """

    default_error_code: str = "INSUFFICIENT_BALANCE"

    def __init__(
        self,
        account_id: uuid.UUID,
        required: int,
        available: int,
        error_code: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        """
        Initialize with account details and amounts.

        Args:
            account_id: UUID of the account with insufficient funds
            required: Amount required in cents
            available: Amount available in cents
            error_code: Optional custom error code
            details: Optional additional error context
        """
        self.account_id = account_id
        self.required = required
        self.available = available

        message = (
            f"Account {account_id} has insufficient balance: "
            f"required {required} cents, available {available} cents"
        )

        # Build details dict
        full_details = {
            "account_id": str(account_id),
            "required_cents": required,
            "available_cents": available,
        }
        if details:
            full_details.update(details)

        super().__init__(
            message=message,
            error_code=error_code,
            details=full_details,
        )


class InactiveAccount(LedgerError):
    """
    Raised when attempting to use an inactive account.

    Accounts can be deactivated (soft-deleted) but their history
    is preserved. Operations on inactive accounts are rejected.

    Example:
        if not account.is_active:
            raise InactiveAccount(
                f"Account {account.id} is inactive",
                details={"account_id": str(account.id)}
            )
    """

    default_error_code: str = "INACTIVE_ACCOUNT"
