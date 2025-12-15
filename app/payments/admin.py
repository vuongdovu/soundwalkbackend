"""
Payment admin configuration.

This file imports admin configurations from the ledger submodule
and registers payment domain models with the Django admin.
"""

from django.contrib import admin

from django.utils import timezone

from payments.ledger.admin import LedgerAccountAdmin, LedgerEntryAdmin
from payments.models import (
    ConnectedAccount,
    DiscrepancyResolution,
    FundHold,
    PaymentOrder,
    Payout,
    ReconciliationDiscrepancy,
    ReconciliationRun,
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
    "ReconciliationRunAdmin",
    "ReconciliationDiscrepancyAdmin",
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


# =============================================================================
# Reconciliation Admin
# =============================================================================


class ReconciliationDiscrepancyInline(admin.TabularInline):
    """Inline display of discrepancies for a reconciliation run."""

    model = ReconciliationDiscrepancy
    extra = 0
    readonly_fields = [
        "id",
        "entity_type",
        "entity_id",
        "stripe_id",
        "discrepancy_type",
        "local_state",
        "stripe_state",
        "resolution",
        "reviewed",
    ]
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None) -> bool:
        return False


@admin.register(ReconciliationRun)
class ReconciliationRunAdmin(admin.ModelAdmin):
    """
    Admin configuration for ReconciliationRun.

    Provides visibility into reconciliation run history and results.
    Runs are created by the reconciliation service and should not be
    manually modified.
    """

    list_display = [
        "id",
        "started_at",
        "status",
        "duration_display",
        "payment_orders_checked",
        "payouts_checked",
        "discrepancies_found",
        "auto_healed",
        "flagged_for_review",
        "failed_to_heal",
    ]
    list_filter = ["status", "started_at"]
    search_fields = ["id"]
    readonly_fields = [
        "id",
        "created_at",
        "updated_at",
        "started_at",
        "completed_at",
        "duration_display",
        "lookback_hours",
        "stuck_threshold_hours",
        "payment_orders_checked",
        "payouts_checked",
        "discrepancies_found",
        "auto_healed",
        "flagged_for_review",
        "failed_to_heal",
        "status",
        "error_message",
    ]
    date_hierarchy = "started_at"
    ordering = ["-started_at"]
    inlines = [ReconciliationDiscrepancyInline]

    fieldsets = (
        (
            None,
            {
                "fields": ("id", "status", "duration_display"),
            },
        ),
        (
            "Configuration",
            {
                "fields": ("lookback_hours", "stuck_threshold_hours"),
            },
        ),
        (
            "Results Summary",
            {
                "fields": (
                    "payment_orders_checked",
                    "payouts_checked",
                    "discrepancies_found",
                    "auto_healed",
                    "flagged_for_review",
                    "failed_to_heal",
                ),
            },
        ),
        (
            "Timing",
            {
                "fields": ("started_at", "completed_at"),
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
            "Timestamps",
            {
                "fields": ("created_at", "updated_at"),
            },
        ),
    )

    def duration_display(self, obj: ReconciliationRun) -> str:
        """Display the run duration in human-readable format."""
        if obj.duration_seconds is not None:
            return f"{obj.duration_seconds:.1f}s"
        return "Running..."

    duration_display.short_description = "Duration"

    def has_delete_permission(self, request, obj=None) -> bool:
        """Disable delete for reconciliation runs (audit trail)."""
        return False

    def has_add_permission(self, request) -> bool:
        """Disable adding reconciliation runs through admin."""
        return False


@admin.register(ReconciliationDiscrepancy)
class ReconciliationDiscrepancyAdmin(admin.ModelAdmin):
    """
    Admin configuration for ReconciliationDiscrepancy.

    Provides a review queue for operators to investigate and resolve
    flagged discrepancies. Supports bulk marking as reviewed.
    """

    list_display = [
        "id",
        "run_link",
        "entity_type",
        "entity_id",
        "discrepancy_type",
        "local_state",
        "stripe_state",
        "resolution",
        "reviewed",
        "created_at",
    ]
    list_filter = [
        "resolution",
        "reviewed",
        "entity_type",
        "discrepancy_type",
        "created_at",
    ]
    search_fields = [
        "id",
        "entity_id",
        "stripe_id",
        "discrepancy_type",
    ]
    readonly_fields = [
        "id",
        "created_at",
        "updated_at",
        "run",
        "entity_type",
        "entity_id",
        "stripe_id",
        "discrepancy_type",
        "local_state",
        "stripe_state",
        "details",
        "resolution",
        "action_taken",
        "error_message",
        "ledger_entry_id",
    ]
    date_hierarchy = "created_at"
    ordering = ["-created_at"]
    actions = ["mark_reviewed"]

    fieldsets = (
        (
            None,
            {
                "fields": ("id", "run", "resolution"),
            },
        ),
        (
            "Entity",
            {
                "fields": ("entity_type", "entity_id", "stripe_id"),
            },
        ),
        (
            "Discrepancy Details",
            {
                "fields": (
                    "discrepancy_type",
                    "local_state",
                    "stripe_state",
                    "details",
                ),
            },
        ),
        (
            "Resolution",
            {
                "fields": ("action_taken", "error_message", "ledger_entry_id"),
            },
        ),
        (
            "Review",
            {
                "fields": ("reviewed", "reviewed_at", "reviewed_by", "review_notes"),
            },
        ),
        (
            "Timestamps",
            {
                "fields": ("created_at", "updated_at"),
            },
        ),
    )

    def run_link(self, obj: ReconciliationDiscrepancy) -> str:
        """Display a link to the reconciliation run."""
        if obj.run:
            return str(obj.run.id)[:8]
        return "-"

    run_link.short_description = "Run"

    @admin.action(description="Mark selected discrepancies as reviewed")
    def mark_reviewed(self, request, queryset):
        """Bulk action to mark discrepancies as reviewed."""
        count = queryset.filter(
            resolution=DiscrepancyResolution.FLAGGED_FOR_REVIEW,
            reviewed=False,
        ).update(
            reviewed=True,
            reviewed_at=timezone.now(),
            reviewed_by=request.user,
        )
        self.message_user(request, f"Marked {count} discrepancies as reviewed.")

    def has_delete_permission(self, request, obj=None) -> bool:
        """Disable delete for discrepancies (audit trail)."""
        return False

    def has_add_permission(self, request) -> bool:
        """Disable adding discrepancies through admin."""
        return False
