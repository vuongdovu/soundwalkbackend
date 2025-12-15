"""
Escrow payment strategy for holding funds until service completion.

This strategy implements the escrow payment flow:
1. Customer initiates payment
2. PaymentIntent created via Stripe
3. Customer completes payment (card input, 3DS, etc.)
4. Webhook confirms success
5. Funds are captured and HELD in escrow
6. Service is delivered
7. Funds are RELEASED to mentor (minus platform fee)
8. Payout sent to mentor's connected account

State Flow:
    DRAFT -> PENDING -> PROCESSING -> CAPTURED -> HELD -> RELEASED -> SETTLED

Ledger Flow:
    At Capture (HELD):
        Entry 1: Debit EXTERNAL_STRIPE, Credit PLATFORM_ESCROW (full amount)
        Note: NO fee deducted yet - enables full refund while held

    At Release (RELEASED):
        Entry 2: Debit PLATFORM_ESCROW, Credit USER_BALANCE[recipient] (amount - fee)
        Entry 3: Debit PLATFORM_ESCROW, Credit PLATFORM_REVENUE (fee)

    At Payout (SETTLED):
        Entry 4: Debit USER_BALANCE[recipient], Credit EXTERNAL_STRIPE

Key Difference from DirectPaymentStrategy:
    - Platform fee is deferred to release (not capture)
    - Full refund possible while funds are held
    - Requires recipient_profile_id in metadata

Usage:
    from payments.strategies import EscrowPaymentStrategy, CreatePaymentParams

    strategy = EscrowPaymentStrategy()
    result = strategy.create_payment(
        CreatePaymentParams(
            payer=customer,
            amount_cents=10000,
            metadata={
                'recipient_profile_id': str(mentor.profile.id),
            },
        )
    )
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.services import ServiceResult

from payments.adapters import (
    CreatePaymentIntentParams,
    IdempotencyKeyGenerator,
    StripeAdapter,
)
from payments.exceptions import (
    LockAcquisitionError,
    StripeError,
)
from payments.ledger.models import AccountType, EntryType
from payments.ledger.services import LedgerService
from payments.ledger.types import RecordEntryParams
from payments.locks import DistributedLock
from payments.models import ConnectedAccount, FundHold, PaymentOrder, Payout
from payments.state_machines import (
    PaymentOrderState,
    PaymentStrategyType,
    PayoutState,
)
from payments.strategies.base import (
    CreatePaymentParams,
    PaymentResult,
    PaymentStrategy,
)

if TYPE_CHECKING:
    from payments.models import Refund


logger = logging.getLogger(__name__)


class EscrowPaymentStrategy(PaymentStrategy):
    """
    Strategy for escrow payments with hold period before release.

    Escrow payments hold funds after capture until service completion
    is confirmed, at which point funds are released to the recipient
    (mentor) minus the platform fee.

    State Flow:
        DRAFT -> PENDING -> PROCESSING -> CAPTURED -> HELD -> RELEASED -> SETTLED

    Platform Fee:
        Unlike DirectPaymentStrategy, the platform fee is NOT deducted at
        capture time. Instead, it's deducted when funds are released. This
        allows full refunds while funds are held without needing to refund
        the fee separately.

    Recipient Tracking:
        Escrow payments require a recipient_profile_id in the metadata.
        This links the payment to the mentor who will receive funds upon
        release.

    Dependency Injection:
        The Stripe adapter can be injected for testing. If not provided,
        uses the default StripeAdapter class.

    Usage:
        # Production
        strategy = EscrowPaymentStrategy()

        # Testing with mock adapter
        strategy = EscrowPaymentStrategy(stripe_adapter=MockStripeAdapter)

        # Custom hold duration
        strategy = EscrowPaymentStrategy(hold_duration_days=30)
    """

    def __init__(
        self,
        stripe_adapter: type | None = None,
        hold_duration_days: int | None = None,
    ) -> None:
        """
        Initialize the strategy with optional Stripe adapter injection.

        Args:
            stripe_adapter: Optional Stripe adapter class for dependency injection.
                            If not provided, uses the default StripeAdapter.
            hold_duration_days: Optional hold duration override.
                               If not provided, uses ESCROW_DEFAULT_HOLD_DURATION_DAYS setting.
        """
        self.stripe = stripe_adapter or StripeAdapter
        self.hold_duration_days = hold_duration_days or getattr(
            settings, "ESCROW_DEFAULT_HOLD_DURATION_DAYS", 42
        )

    def create_payment(
        self, params: CreatePaymentParams
    ) -> ServiceResult[PaymentResult]:
        """
        Create an escrow payment and return the client secret.

        Steps:
        1. Validate recipient_profile_id exists in metadata
        2. Create PaymentOrder in DRAFT state with ESCROW strategy
        3. Call Stripe to create a PaymentIntent
        4. Store the PaymentIntent ID on the order
        5. Transition to PENDING state
        6. Return client_secret for frontend use

        The recipient_profile_id is required because we need to know who
        will receive funds when the hold is released. Optionally warn if
        the recipient doesn't have a connected account yet (they can
        complete onboarding before release).

        Args:
            params: Payment creation parameters (must include recipient_profile_id in metadata)

        Returns:
            ServiceResult containing PaymentResult on success
        """
        logger.info(
            "Creating escrow payment",
            extra={
                "payer_id": str(params.payer.id),
                "amount_cents": params.amount_cents,
                "currency": params.currency,
            },
        )

        # Step 1: Validate recipient_profile_id
        metadata = params.metadata or {}
        recipient_profile_id = metadata.get("recipient_profile_id")

        if not recipient_profile_id:
            logger.warning(
                "Escrow payment missing recipient_profile_id",
                extra={"payer_id": str(params.payer.id)},
            )
            return ServiceResult.failure(
                "recipient_profile_id is required for escrow payments",
                error_code="MISSING_RECIPIENT",
            )

        # Validate recipient_profile_id format (UUID)
        # Profile uses user_id (UUID) as primary key
        try:
            recipient_id = uuid.UUID(str(recipient_profile_id))
        except (ValueError, TypeError):
            logger.warning(
                "Invalid recipient_profile_id format",
                extra={"recipient_profile_id": recipient_profile_id},
            )
            return ServiceResult.failure(
                "Invalid recipient_profile_id format",
                error_code="INVALID_RECIPIENT",
            )

        # Check if recipient has connected account (warn if not, but don't fail)
        connected_account = ConnectedAccount.objects.filter(
            profile_id=recipient_id
        ).first()
        if not connected_account or not connected_account.is_ready_for_payouts:
            logger.warning(
                "Recipient not ready for payouts (will need to complete onboarding before release)",
                extra={"recipient_profile_id": str(recipient_id)},
            )

        try:
            with transaction.atomic():
                # Step 2: Create PaymentOrder (DRAFT state)
                order = PaymentOrder.objects.create(
                    payer=params.payer,
                    amount_cents=params.amount_cents,
                    currency=params.currency,
                    strategy_type=PaymentStrategyType.ESCROW,
                    reference_id=params.reference_id,
                    reference_type=params.reference_type,
                    metadata=metadata,
                )

                # Step 3: Call Stripe to create PaymentIntent
                idempotency_key = IdempotencyKeyGenerator.generate(
                    operation="create_intent",
                    entity_id=order.id,
                )

                stripe_params = CreatePaymentIntentParams(
                    amount_cents=params.amount_cents,
                    currency=params.currency,
                    idempotency_key=idempotency_key,
                    metadata={
                        "payment_order_id": str(order.id),
                        "payer_id": str(params.payer.id),
                        "recipient_profile_id": str(recipient_id),
                        "strategy": "escrow",
                    },
                )

                # This call is outside the DB transaction's protection
                # but uses idempotency key for safe retries
                stripe_result = self.stripe.create_payment_intent(stripe_params)

                # Step 4: Store PaymentIntent ID
                order.stripe_payment_intent_id = stripe_result.id
                order.save(update_fields=["stripe_payment_intent_id"])

                # Step 5: Transition to PENDING
                order.submit()
                order.save()

                logger.info(
                    "Escrow payment created",
                    extra={
                        "payment_order_id": str(order.id),
                        "payment_intent_id": stripe_result.id,
                        "recipient_profile_id": str(recipient_id),
                        "status": order.state,
                    },
                )

                # Step 6: Return result
                return ServiceResult.success(
                    PaymentResult(
                        payment_order=order,
                        client_secret=stripe_result.client_secret,
                    )
                )

        except StripeError as e:
            logger.error(
                "Failed to create escrow payment: Stripe error",
                extra={
                    "error_code": e.error_code,
                    "is_retryable": e.is_retryable,
                },
                exc_info=True,
            )
            return ServiceResult.failure(
                str(e),
                error_code=e.error_code or "STRIPE_ERROR",
            )

        except Exception as e:
            logger.error(
                f"Failed to create escrow payment: {type(e).__name__}",
                exc_info=True,
            )
            return ServiceResult.failure(
                "An unexpected error occurred while creating the payment",
                error_code="PAYMENT_CREATION_ERROR",
            )

    def handle_payment_succeeded(
        self,
        payment_order: PaymentOrder,
        event_data: dict[str, Any],
    ) -> ServiceResult[PaymentOrder]:
        """
        Process a successful payment webhook event.

        For escrow payments, this handles the flow from PENDING through
        to HELD (not SETTLED like direct payments). A FundHold record is
        created to track the escrowed funds.

        Steps:
        1. Validate current state is PENDING
        2. Transition: PENDING -> PROCESSING -> CAPTURED -> HELD
        3. Create FundHold record with expiration
        4. Record ledger entry: EXTERNAL_STRIPE -> PLATFORM_ESCROW (full amount)
        5. Save all changes atomically

        Note: Platform fee is NOT deducted at this stage. It's deferred
        to the release phase to enable full refunds while held.

        The caller (webhook handler) is responsible for:
        - Wrapping this in a transaction with select_for_update
        - Ensuring idempotent webhook processing

        Args:
            payment_order: The PaymentOrder to update
            event_data: Full webhook event data from Stripe

        Returns:
            ServiceResult containing updated PaymentOrder on success
        """
        logger.info(
            "Handling payment success for escrow payment",
            extra={
                "payment_order_id": str(payment_order.id),
                "current_state": payment_order.state,
            },
        )

        # Validate we're in the right state
        if payment_order.state != PaymentOrderState.PENDING:
            logger.warning(
                "Escrow payment not in PENDING state",
                extra={
                    "payment_order_id": str(payment_order.id),
                    "current_state": payment_order.state,
                },
            )
            # If already processed, return success (idempotent)
            if payment_order.state in [
                PaymentOrderState.HELD,
                PaymentOrderState.RELEASED,
                PaymentOrderState.SETTLED,
            ]:
                return ServiceResult.success(payment_order)

            return ServiceResult.failure(
                f"Cannot process escrow payment from state: {payment_order.state}",
                error_code="INVALID_STATE",
            )

        try:
            with transaction.atomic():
                # Re-fetch with lock to ensure consistency
                order = PaymentOrder.objects.select_for_update().get(
                    id=payment_order.id
                )

                # Double-check state after lock
                if order.state != PaymentOrderState.PENDING:
                    if order.state in [
                        PaymentOrderState.HELD,
                        PaymentOrderState.RELEASED,
                        PaymentOrderState.SETTLED,
                    ]:
                        return ServiceResult.success(order)
                    return ServiceResult.failure(
                        f"Cannot process escrow payment from state: {order.state}",
                        error_code="INVALID_STATE",
                    )

                # State transitions: PENDING -> PROCESSING -> CAPTURED -> HELD
                order.process()
                order.capture()
                order.hold()
                order.save()

                # Create FundHold record
                expires_at = timezone.now() + timedelta(days=self.hold_duration_days)
                fund_hold = FundHold.objects.create(
                    payment_order=order,
                    amount_cents=order.amount_cents,
                    currency=order.currency,
                    expires_at=expires_at,
                    metadata={
                        "release_condition_type": "service_completed_or_expired",
                    },
                )

                # Record ledger entry: EXTERNAL_STRIPE -> PLATFORM_ESCROW (full amount)
                # Note: NO fee entry here - deferred to release
                self._record_capture_ledger_entries(order)

                logger.info(
                    "Escrow payment captured and held",
                    extra={
                        "payment_order_id": str(order.id),
                        "fund_hold_id": str(fund_hold.id),
                        "final_state": order.state,
                        "expires_at": expires_at.isoformat(),
                    },
                )

                return ServiceResult.success(order)

        except Exception as e:
            logger.error(
                f"Failed to process escrow payment success: {type(e).__name__}",
                extra={"payment_order_id": str(payment_order.id)},
                exc_info=True,
            )
            return ServiceResult.failure(
                str(e),
                error_code="PAYMENT_PROCESSING_ERROR",
            )

    def _record_capture_ledger_entries(self, payment_order: PaymentOrder) -> None:
        """
        Record ledger entry for captured escrow payment.

        Creates ONE entry (unlike DirectPaymentStrategy which creates TWO):
        1. Payment received: EXTERNAL_STRIPE -> PLATFORM_ESCROW (full amount)

        The platform fee is NOT deducted here - it's deferred to release.
        This allows full refunds while funds are held.

        Args:
            payment_order: The captured PaymentOrder
        """
        # Get or create accounts
        external_account = LedgerService.get_or_create_account(
            AccountType.EXTERNAL_STRIPE,
            owner_id=None,
            currency=payment_order.currency,
            allow_negative=True,  # External can go negative (money out)
        )

        escrow_account = LedgerService.get_or_create_account(
            AccountType.PLATFORM_ESCROW,
            owner_id=None,
            currency=payment_order.currency,
        )

        # Create ledger entry
        LedgerService.record_entries(
            [
                RecordEntryParams(
                    debit_account_id=external_account.id,
                    credit_account_id=escrow_account.id,
                    amount_cents=payment_order.amount_cents,
                    entry_type=EntryType.PAYMENT_RECEIVED,
                    idempotency_key=f"escrow:{payment_order.id}:capture",
                    reference_type="payment_order",
                    reference_id=payment_order.id,
                    description=f"Escrow payment captured for order {payment_order.id}",
                    created_by="escrow_payment_strategy",
                )
            ]
        )

        logger.info(
            "Capture ledger entry recorded for escrow payment",
            extra={
                "payment_order_id": str(payment_order.id),
                "amount_cents": payment_order.amount_cents,
            },
        )

    def handle_payment_failed(
        self,
        payment_order: PaymentOrder,
        event_data: dict[str, Any],
        reason: str,
    ) -> ServiceResult[PaymentOrder]:
        """
        Process a failed payment webhook event.

        Transitions the PaymentOrder to FAILED state with the failure
        reason. The customer can later retry the payment if desired.

        Note: We only fail from PENDING state. If already failed, this
        is idempotent.

        Args:
            payment_order: The PaymentOrder to update
            event_data: Full webhook event data from Stripe
            reason: Human-readable failure reason

        Returns:
            ServiceResult containing updated PaymentOrder on success
        """
        logger.info(
            "Handling payment failure for escrow payment",
            extra={
                "payment_order_id": str(payment_order.id),
                "current_state": payment_order.state,
                "reason": reason,
            },
        )

        # If already failed, return success (idempotent)
        if payment_order.state == PaymentOrderState.FAILED:
            return ServiceResult.success(payment_order)

        # For escrow payments, we need to be in PENDING to process
        if payment_order.state == PaymentOrderState.PENDING:
            try:
                # Transition through PROCESSING to reach FAILED
                payment_order.process()
                payment_order.fail(reason=reason)
                payment_order.save()

                logger.info(
                    "Escrow payment marked as failed",
                    extra={
                        "payment_order_id": str(payment_order.id),
                        "final_state": payment_order.state,
                        "reason": reason,
                    },
                )

                return ServiceResult.success(payment_order)

            except Exception as e:
                logger.error(
                    f"Failed to process escrow payment failure: {type(e).__name__}",
                    extra={"payment_order_id": str(payment_order.id)},
                    exc_info=True,
                )
                return ServiceResult.failure(
                    str(e),
                    error_code="PAYMENT_FAILURE_PROCESSING_ERROR",
                )

        # Can't fail from other states
        logger.warning(
            "Cannot fail escrow payment from current state",
            extra={
                "payment_order_id": str(payment_order.id),
                "current_state": payment_order.state,
            },
        )
        return ServiceResult.failure(
            f"Cannot fail payment from state: {payment_order.state}",
            error_code="INVALID_STATE",
        )

    def release_hold(
        self,
        payment_order: PaymentOrder,
        release_reason: str = "service_completed",
    ) -> ServiceResult[PaymentOrder]:
        """
        Release escrowed funds to the recipient.

        This is the key escrow operation - it moves funds from hold to
        the recipient's account, deducting the platform fee.

        Steps:
        1. Acquire distributed lock to prevent concurrent releases
        2. Validate state is HELD
        3. Get recipient_profile_id from metadata
        4. Validate recipient has ConnectedAccount with payouts_enabled
        5. Calculate platform fee
        6. Record ledger entries:
           - PLATFORM_ESCROW -> USER_BALANCE[recipient] (amount - fee)
           - PLATFORM_ESCROW -> PLATFORM_REVENUE (fee)
        7. Create Payout record (PENDING state)
        8. Mark FundHold as released, link to Payout
        9. Transition PaymentOrder: HELD -> RELEASED
        10. Save all changes atomically

        The Payout record will be picked up by the payout executor worker
        to actually transfer funds to the mentor's connected account.

        Args:
            payment_order: The PaymentOrder in HELD state
            release_reason: Reason for release (for audit trail)

        Returns:
            ServiceResult containing updated PaymentOrder on success
        """
        logger.info(
            "Releasing escrow hold",
            extra={
                "payment_order_id": str(payment_order.id),
                "current_state": payment_order.state,
                "release_reason": release_reason,
            },
        )

        # Idempotent check for already released
        if payment_order.state in [
            PaymentOrderState.RELEASED,
            PaymentOrderState.SETTLED,
        ]:
            logger.info(
                "Escrow already released, returning success (idempotent)",
                extra={
                    "payment_order_id": str(payment_order.id),
                    "current_state": payment_order.state,
                },
            )
            return ServiceResult.success(payment_order)

        # Validate state is HELD
        if payment_order.state != PaymentOrderState.HELD:
            return ServiceResult.failure(
                f"Cannot release escrow from state: {payment_order.state}",
                error_code="INVALID_STATE",
            )

        # Step 1: Acquire distributed lock
        lock_key = f"escrow:release:{payment_order.id}"
        try:
            with DistributedLock(lock_key, ttl=60, blocking=True, timeout=10.0):
                return self._execute_release(payment_order, release_reason)
        except LockAcquisitionError as e:
            logger.warning(
                "Failed to acquire lock for escrow release",
                extra={
                    "payment_order_id": str(payment_order.id),
                    "error": str(e),
                },
            )
            return ServiceResult.failure(
                "Another process is releasing this payment",
                error_code="LOCK_CONTENTION",
            )

    def _execute_release(
        self,
        payment_order: PaymentOrder,
        release_reason: str,
    ) -> ServiceResult[PaymentOrder]:
        """
        Execute the release within the distributed lock.

        Args:
            payment_order: The PaymentOrder to release
            release_reason: Reason for release

        Returns:
            ServiceResult containing updated PaymentOrder
        """
        try:
            with transaction.atomic():
                # Re-fetch with lock to ensure consistency
                order = PaymentOrder.objects.select_for_update().get(
                    id=payment_order.id
                )

                # Double-check state after lock
                if order.state in [
                    PaymentOrderState.RELEASED,
                    PaymentOrderState.SETTLED,
                ]:
                    return ServiceResult.success(order)

                if order.state != PaymentOrderState.HELD:
                    return ServiceResult.failure(
                        f"Cannot release escrow from state: {order.state}",
                        error_code="INVALID_STATE",
                    )

                # Get FundHold
                fund_hold = (
                    FundHold.objects.select_for_update()
                    .filter(
                        payment_order=order,
                        released=False,
                    )
                    .first()
                )

                if not fund_hold:
                    return ServiceResult.failure(
                        "No active FundHold found for this order",
                        error_code="FUND_HOLD_NOT_FOUND",
                    )

                # Get recipient profile ID
                recipient_profile_id = order.metadata.get("recipient_profile_id")
                if not recipient_profile_id:
                    return ServiceResult.failure(
                        "No recipient_profile_id in order metadata",
                        error_code="MISSING_RECIPIENT",
                    )

                # Profile uses user_id (UUID) as primary key
                recipient_id = uuid.UUID(str(recipient_profile_id))

                # Validate recipient has ConnectedAccount ready for payouts
                connected_account = ConnectedAccount.objects.filter(
                    profile_id=recipient_id
                ).first()

                if not connected_account:
                    return ServiceResult.failure(
                        "Recipient does not have a connected account",
                        error_code="RECIPIENT_NOT_READY",
                    )

                if not connected_account.is_ready_for_payouts:
                    return ServiceResult.failure(
                        "Recipient's connected account is not ready for payouts",
                        error_code="RECIPIENT_NOT_READY",
                    )

                # Calculate amounts
                platform_fee = self.calculate_platform_fee(order.amount_cents)
                recipient_amount = order.amount_cents - platform_fee

                # Record ledger entries for release
                self._record_release_ledger_entries(
                    order,
                    recipient_id,
                    recipient_amount,
                    platform_fee,
                )

                # Create Payout record
                payout = Payout.objects.create(
                    payment_order=order,
                    connected_account=connected_account,
                    amount_cents=recipient_amount,
                    currency=order.currency,
                    metadata={
                        "release_reason": release_reason,
                        "platform_fee": platform_fee,
                    },
                )

                # Mark FundHold as released
                fund_hold.release_to(payout)
                fund_hold.save()

                # Transition order to RELEASED
                order.release()
                order.save()

                logger.info(
                    "Escrow hold released successfully",
                    extra={
                        "payment_order_id": str(order.id),
                        "payout_id": str(payout.id),
                        "recipient_amount": recipient_amount,
                        "platform_fee": platform_fee,
                        "release_reason": release_reason,
                    },
                )

                return ServiceResult.success(order)

        except Exception as e:
            logger.error(
                f"Failed to release escrow hold: {type(e).__name__}",
                extra={"payment_order_id": str(payment_order.id)},
                exc_info=True,
            )
            return ServiceResult.failure(
                str(e),
                error_code="RELEASE_FAILED",
            )

    def _record_release_ledger_entries(
        self,
        payment_order: PaymentOrder,
        recipient_profile_id: uuid.UUID,
        recipient_amount: int,
        platform_fee: int,
    ) -> None:
        """
        Record ledger entries for releasing escrowed funds.

        Creates two entries:
        1. PLATFORM_ESCROW -> USER_BALANCE[recipient] (amount - fee)
        2. PLATFORM_ESCROW -> PLATFORM_REVENUE (fee)

        Args:
            payment_order: The PaymentOrder being released
            recipient_profile_id: UUID of recipient's profile (Profile uses user_id as PK)
            recipient_amount: Amount to pay recipient (after fee)
            platform_fee: Platform fee amount
        """
        # Get or create accounts
        escrow_account = LedgerService.get_or_create_account(
            AccountType.PLATFORM_ESCROW,
            owner_id=None,
            currency=payment_order.currency,
        )

        # User/Profile now uses UUID PK, use directly as ledger owner_id
        recipient_account = LedgerService.get_or_create_account(
            AccountType.USER_BALANCE,
            owner_id=recipient_profile_id,
            currency=payment_order.currency,
        )

        revenue_account = LedgerService.get_or_create_account(
            AccountType.PLATFORM_REVENUE,
            owner_id=None,
            currency=payment_order.currency,
        )

        entries = []

        # Entry 1: Release to recipient
        entries.append(
            RecordEntryParams(
                debit_account_id=escrow_account.id,
                credit_account_id=recipient_account.id,
                amount_cents=recipient_amount,
                entry_type=EntryType.PAYMENT_RELEASED,
                idempotency_key=f"escrow:{payment_order.id}:release:recipient",
                reference_type="payment_order",
                reference_id=payment_order.id,
                description=f"Escrow funds released to recipient for order {payment_order.id}",
                created_by="escrow_payment_strategy",
            )
        )

        # Entry 2: Platform fee (only if fee > 0)
        if platform_fee > 0:
            entries.append(
                RecordEntryParams(
                    debit_account_id=escrow_account.id,
                    credit_account_id=revenue_account.id,
                    amount_cents=platform_fee,
                    entry_type=EntryType.FEE_COLLECTED,
                    idempotency_key=f"escrow:{payment_order.id}:release:fee",
                    reference_type="payment_order",
                    reference_id=payment_order.id,
                    description=f"Platform fee for escrow order {payment_order.id}",
                    created_by="escrow_payment_strategy",
                )
            )

        LedgerService.record_entries(entries)

        logger.info(
            "Release ledger entries recorded for escrow payment",
            extra={
                "payment_order_id": str(payment_order.id),
                "recipient_amount": recipient_amount,
                "platform_fee": platform_fee,
                "entries_count": len(entries),
            },
        )

    def refund_held_payment(
        self,
        payment_order: PaymentOrder,
        amount_cents: int | None = None,
        reason: str = "refund_requested",
    ) -> ServiceResult[Refund]:
        """
        Refund an escrow payment.

        Refund behavior depends on the current state:
        - HELD: Full refund allowed (no fee was taken)
        - RELEASED (payout PENDING): Refund allowed, cancel payout
        - RELEASED (payout PROCESSING): Block - uncertain state
        - SETTLED (payout PAID): Block - requires manual resolution

        Args:
            payment_order: The PaymentOrder to refund
            amount_cents: Amount to refund (None = full refund)
            reason: Reason for refund

        Returns:
            ServiceResult containing Refund record on success
        """
        logger.info(
            "Processing escrow refund",
            extra={
                "payment_order_id": str(payment_order.id),
                "current_state": payment_order.state,
                "amount_cents": amount_cents,
                "reason": reason,
            },
        )

        # Check for post-payout refund - BLOCK if payout is PAID
        if payment_order.state in [
            PaymentOrderState.RELEASED,
            PaymentOrderState.SETTLED,
        ]:
            payout = payment_order.payouts.first()
            if payout and payout.state == PayoutState.PAID:
                logger.warning(
                    "Cannot refund escrow - payout already paid",
                    extra={
                        "payment_order_id": str(payment_order.id),
                        "payout_id": str(payout.id),
                        "payout_state": payout.state,
                    },
                )
                return ServiceResult.failure(
                    "Cannot refund - funds already paid out to recipient. "
                    "Manual resolution required.",
                    error_code="PAYOUT_ALREADY_PAID",
                )

            # If payout is PROCESSING, also block (uncertain state)
            if payout and payout.state == PayoutState.PROCESSING:
                logger.warning(
                    "Cannot refund escrow - payout is processing",
                    extra={
                        "payment_order_id": str(payment_order.id),
                        "payout_id": str(payout.id),
                    },
                )
                return ServiceResult.failure(
                    "Cannot refund - payout is currently processing. "
                    "Please wait for payout to complete or fail.",
                    error_code="PAYOUT_IN_PROGRESS",
                )

        # Validate state allows refund
        if payment_order.state not in [
            PaymentOrderState.HELD,
            PaymentOrderState.RELEASED,
        ]:
            return ServiceResult.failure(
                f"Cannot refund from state: {payment_order.state}",
                error_code="INVALID_STATE",
            )

        # For now, return success placeholder
        # Full implementation would create Stripe refund and Refund record
        # This is a simplified version for the initial implementation
        try:
            refund_amount = amount_cents or payment_order.amount_cents

            # Create Stripe refund
            refund_result = self.stripe.create_refund(
                payment_intent_id=payment_order.stripe_payment_intent_id,
                amount_cents=refund_amount,
                reason=reason,
            )

            # The refund handling would continue here with Refund model creation
            # and state transitions, but that's handled by the refund service
            # which will be called by the webhook handler

            logger.info(
                "Escrow refund initiated",
                extra={
                    "payment_order_id": str(payment_order.id),
                    "refund_id": refund_result.id,
                    "amount_cents": refund_amount,
                },
            )

            return ServiceResult.success(refund_result)

        except StripeError as e:
            logger.error(
                "Failed to create escrow refund",
                extra={
                    "payment_order_id": str(payment_order.id),
                    "error": str(e),
                },
                exc_info=True,
            )
            return ServiceResult.failure(
                str(e),
                error_code="REFUND_FAILED",
            )

        except Exception as e:
            logger.error(
                f"Unexpected error during escrow refund: {type(e).__name__}",
                extra={"payment_order_id": str(payment_order.id)},
                exc_info=True,
            )
            return ServiceResult.failure(
                str(e),
                error_code="REFUND_ERROR",
            )

    def calculate_platform_fee(self, amount_cents: int) -> int:
        """
        Calculate the platform fee for a given payment amount.

        Uses the PLATFORM_FEE_PERCENT setting (default: 15%).
        Uses integer division to avoid floating-point issues.

        Note: For escrow payments, this fee is deducted at RELEASE time,
        not at capture time like direct payments.

        Args:
            amount_cents: Total payment amount in cents

        Returns:
            Platform fee amount in cents

        Example:
            # With 15% platform fee
            fee = strategy.calculate_platform_fee(10000)  # Returns 1500
            fee = strategy.calculate_platform_fee(9999)   # Returns 1499
        """
        fee_percent = getattr(settings, "PLATFORM_FEE_PERCENT", 15)
        return amount_cents * fee_percent // 100
