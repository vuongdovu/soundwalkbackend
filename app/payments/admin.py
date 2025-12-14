"""
Payment admin configuration.

This file imports admin configurations from the ledger submodule
and registers payment domain models with the Django admin.
"""

from django.contrib import admin

from payments.ledger.admin import LedgerAccountAdmin, LedgerEntryAdmin
from payments.models import (
    ConnectedAccount,
    FundHold,
    PaymentOrder,
    Payout,
    Refund,
    WebhookEvent,
)

__all__ = [
    "LedgerAccountAdmin",
    "LedgerEntryAdmin",
    "ConnectedAccountAdmin",
    "PaymentOrderAdmin",
    "FundHoldAdmin",
    "PayoutAdmin",
    "RefundAdmin",
    "WebhookEventAdmin",
]


@admin.register(ConnectedAccount)
class ConnectedAccountAdmin(admin.ModelAdmin):
    """
    Admin configuration for ConnectedAccount.

    Provides visibility into Stripe Connect account status.
    """

    list_display = [
        "id",
        "profile",
        "stripe_account_id",
        "onboarding_status",
        "payouts_enabled",
        "charges_enabled",
        "created_at",
    ]
    list_filter = ["onboarding_status", "payouts_enabled", "charges_enabled"]
    search_fields = ["id", "stripe_account_id", "profile__user__email"]
    readonly_fields = ["id", "created_at", "updated_at", "version"]
    ordering = ["-created_at"]

    fieldsets = (
        (
            None,
            {
                "fields": ("id", "profile", "stripe_account_id"),
            },
        ),
        (
            "Status",
            {
                "fields": ("onboarding_status", "payouts_enabled", "charges_enabled"),
            },
        ),
        (
            "Metadata",
            {
                "fields": ("metadata", "version"),
                "classes": ("collapse",),
            },
        ),
        (
            "Timestamps",
            {
                "fields": ("created_at", "updated_at"),
            },
        ),
    )


@admin.register(PaymentOrder)
class PaymentOrderAdmin(admin.ModelAdmin):
    """
    Admin configuration for PaymentOrder.

    Provides visibility into payment orders and their states.
    State changes should be made through the service layer, not admin.
    """

    list_display = [
        "id",
        "payer",
        "amount_display",
        "state",
        "strategy_type",
        "reference_type",
        "created_at",
    ]
    list_filter = ["state", "strategy_type", "currency", "created_at"]
    search_fields = [
        "id",
        "stripe_payment_intent_id",
        "payer__email",
        "reference_id",
    ]
    readonly_fields = [
        "id",
        "created_at",
        "updated_at",
        "version",
        "captured_at",
        "held_at",
        "released_at",
        "settled_at",
        "failed_at",
        "cancelled_at",
    ]
    date_hierarchy = "created_at"
    ordering = ["-created_at"]

    fieldsets = (
        (
            None,
            {
                "fields": ("id", "payer", "state"),
            },
        ),
        (
            "Amount",
            {
                "fields": ("amount_cents", "currency"),
            },
        ),
        (
            "Payment Details",
            {
                "fields": (
                    "strategy_type",
                    "stripe_payment_intent_id",
                    "reference_type",
                    "reference_id",
                ),
            },
        ),
        (
            "State Timestamps",
            {
                "fields": (
                    "captured_at",
                    "held_at",
                    "released_at",
                    "settled_at",
                    "failed_at",
                    "cancelled_at",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Failure Info",
            {
                "fields": ("failure_reason",),
                "classes": ("collapse",),
            },
        ),
        (
            "Metadata",
            {
                "fields": ("metadata", "version"),
                "classes": ("collapse",),
            },
        ),
        (
            "Timestamps",
            {
                "fields": ("created_at", "updated_at"),
            },
        ),
    )

    def amount_display(self, obj: PaymentOrder) -> str:
        """Display the amount formatted as currency."""
        return f"${obj.amount_cents / 100:.2f} {obj.currency.upper()}"

    amount_display.short_description = "Amount"

    def has_delete_permission(self, request, obj=None) -> bool:
        """Disable delete for payment orders (audit trail)."""
        return False


class FundHoldInline(admin.TabularInline):
    """Inline display of fund holds for a payment order."""

    model = FundHold
    extra = 0
    readonly_fields = [
        "id",
        "amount_cents",
        "currency",
        "expires_at",
        "released",
        "released_at",
        "released_to_payout",
    ]
    can_delete = False

    def has_add_permission(self, request, obj=None) -> bool:
        return False


@admin.register(FundHold)
class FundHoldAdmin(admin.ModelAdmin):
    """
    Admin configuration for FundHold.

    Fund holds are created by the escrow service and should not be
    manually modified through admin.
    """

    list_display = [
        "id",
        "payment_order",
        "amount_display",
        "expires_at",
        "released",
        "released_at",
        "created_at",
    ]
    list_filter = ["released", "currency", "created_at"]
    search_fields = ["id", "payment_order__id"]
    readonly_fields = [
        "id",
        "created_at",
        "updated_at",
        "version",
        "released_at",
    ]
    ordering = ["-created_at"]

    fieldsets = (
        (
            None,
            {
                "fields": ("id", "payment_order"),
            },
        ),
        (
            "Amount",
            {
                "fields": ("amount_cents", "currency"),
            },
        ),
        (
            "Hold Status",
            {
                "fields": (
                    "expires_at",
                    "released",
                    "released_at",
                    "released_to_payout",
                ),
            },
        ),
        (
            "Metadata",
            {
                "fields": ("metadata", "version"),
                "classes": ("collapse",),
            },
        ),
        (
            "Timestamps",
            {
                "fields": ("created_at", "updated_at"),
            },
        ),
    )

    def amount_display(self, obj: FundHold) -> str:
        """Display the amount formatted as currency."""
        return f"${obj.amount_cents / 100:.2f} {obj.currency.upper()}"

    amount_display.short_description = "Amount"

    def has_delete_permission(self, request, obj=None) -> bool:
        """Disable delete for fund holds (audit trail)."""
        return False

    def has_add_permission(self, request) -> bool:
        """Disable adding fund holds through admin."""
        return False


@admin.register(Payout)
class PayoutAdmin(admin.ModelAdmin):
    """
    Admin configuration for Payout.

    Provides visibility into payout status and history.
    """

    list_display = [
        "id",
        "payment_order",
        "connected_account",
        "amount_display",
        "state",
        "scheduled_for",
        "paid_at",
        "created_at",
    ]
    list_filter = ["state", "currency", "created_at"]
    search_fields = [
        "id",
        "stripe_transfer_id",
        "payment_order__id",
        "connected_account__stripe_account_id",
    ]
    readonly_fields = [
        "id",
        "created_at",
        "updated_at",
        "version",
        "paid_at",
        "failed_at",
    ]
    date_hierarchy = "created_at"
    ordering = ["-created_at"]

    fieldsets = (
        (
            None,
            {
                "fields": ("id", "payment_order", "connected_account", "state"),
            },
        ),
        (
            "Amount",
            {
                "fields": ("amount_cents", "currency"),
            },
        ),
        (
            "Stripe Details",
            {
                "fields": ("stripe_transfer_id", "scheduled_for"),
            },
        ),
        (
            "Status Timestamps",
            {
                "fields": ("paid_at", "failed_at"),
            },
        ),
        (
            "Failure Info",
            {
                "fields": ("failure_reason",),
                "classes": ("collapse",),
            },
        ),
        (
            "Metadata",
            {
                "fields": ("metadata", "version"),
                "classes": ("collapse",),
            },
        ),
        (
            "Timestamps",
            {
                "fields": ("created_at", "updated_at"),
            },
        ),
    )

    def amount_display(self, obj: Payout) -> str:
        """Display the amount formatted as currency."""
        return f"${obj.amount_cents / 100:.2f} {obj.currency.upper()}"

    amount_display.short_description = "Amount"

    def has_delete_permission(self, request, obj=None) -> bool:
        """Disable delete for payouts (audit trail)."""
        return False


@admin.register(Refund)
class RefundAdmin(admin.ModelAdmin):
    """
    Admin configuration for Refund.

    Provides visibility into refund status and history.
    """

    list_display = [
        "id",
        "payment_order",
        "amount_display",
        "state",
        "reason",
        "completed_at",
        "created_at",
    ]
    list_filter = ["state", "currency", "created_at"]
    search_fields = [
        "id",
        "stripe_refund_id",
        "payment_order__id",
        "reason",
    ]
    readonly_fields = [
        "id",
        "created_at",
        "updated_at",
        "version",
        "completed_at",
        "failed_at",
    ]
    date_hierarchy = "created_at"
    ordering = ["-created_at"]

    fieldsets = (
        (
            None,
            {
                "fields": ("id", "payment_order", "state"),
            },
        ),
        (
            "Amount",
            {
                "fields": ("amount_cents", "currency"),
            },
        ),
        (
            "Refund Details",
            {
                "fields": ("reason", "stripe_refund_id"),
            },
        ),
        (
            "Status Timestamps",
            {
                "fields": ("completed_at", "failed_at"),
            },
        ),
        (
            "Failure Info",
            {
                "fields": ("failure_reason",),
                "classes": ("collapse",),
            },
        ),
        (
            "Metadata",
            {
                "fields": ("metadata", "version"),
                "classes": ("collapse",),
            },
        ),
        (
            "Timestamps",
            {
                "fields": ("created_at", "updated_at"),
            },
        ),
    )

    def amount_display(self, obj: Refund) -> str:
        """Display the amount formatted as currency."""
        return f"${obj.amount_cents / 100:.2f} {obj.currency.upper()}"

    amount_display.short_description = "Amount"

    def has_delete_permission(self, request, obj=None) -> bool:
        """Disable delete for refunds (audit trail)."""
        return False


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    """
    Admin configuration for WebhookEvent.

    Provides visibility into webhook processing status.
    Webhook events are immutable once received.
    """

    list_display = [
        "id",
        "stripe_event_id",
        "event_type",
        "status",
        "retry_count",
        "processed_at",
        "created_at",
    ]
    list_filter = ["status", "event_type", "created_at"]
    search_fields = ["id", "stripe_event_id", "event_type"]
    readonly_fields = [
        "id",
        "created_at",
        "updated_at",
        "stripe_event_id",
        "event_type",
        "payload",
        "processed_at",
    ]
    date_hierarchy = "created_at"
    ordering = ["-created_at"]

    fieldsets = (
        (
            None,
            {
                "fields": ("id", "stripe_event_id", "event_type", "status"),
            },
        ),
        (
            "Processing",
            {
                "fields": ("processed_at", "retry_count"),
            },
        ),
        (
            "Error Info",
            {
                "fields": ("error_message",),
                "classes": ("collapse",),
            },
        ),
        (
            "Payload",
            {
                "fields": ("payload",),
                "classes": ("collapse",),
            },
        ),
        (
            "Timestamps",
            {
                "fields": ("created_at", "updated_at"),
            },
        ),
    )

    def has_delete_permission(self, request, obj=None) -> bool:
        """Disable delete for webhook events (audit trail)."""
        return False

    def has_add_permission(self, request) -> bool:
        """Disable adding webhook events through admin."""
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        """
        Only allow changing status and error_message for retry.

        Payload and event details are immutable.
        """
        return True
