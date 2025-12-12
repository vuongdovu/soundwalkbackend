"""
Django admin configuration for ledger models.

This module configures the admin interface for LedgerAccount and LedgerEntry,
enforcing immutability for ledger entries while providing useful visibility
into account states and transaction history.

Key features:
- LedgerEntry is immutable (no edit/delete permissions)
- Balance displayed on LedgerAccount list view
- Useful filters and search capabilities
"""

from django.contrib import admin

from .models import LedgerAccount, LedgerEntry


@admin.register(LedgerAccount)
class LedgerAccountAdmin(admin.ModelAdmin):
    """
    Admin configuration for LedgerAccount.

    Provides visibility into account types, owners, balances, and status.
    Balance is computed dynamically from related entries.
    """

    list_display = [
        "id",
        "type",
        "owner_id",
        "currency",
        "balance_display",
        "is_active",
        "allow_negative",
        "created_at",
    ]
    list_filter = ["type", "currency", "is_active", "allow_negative"]
    search_fields = ["id", "owner_id"]
    readonly_fields = ["id", "created_at", "balance_display"]
    ordering = ["-created_at"]

    fieldsets = (
        (
            None,
            {
                "fields": ("id", "type", "owner_id", "currency"),
            },
        ),
        (
            "Configuration",
            {
                "fields": ("allow_negative", "is_active"),
            },
        ),
        (
            "Balance",
            {
                "fields": ("balance_display",),
            },
        ),
        (
            "Timestamps",
            {
                "fields": ("created_at",),
            },
        ),
    )

    def balance_display(self, obj: LedgerAccount) -> str:
        """
        Display the account balance formatted as currency.

        This performs a database query to calculate the balance
        from related entries.
        """
        cents = obj.get_balance()
        return f"${cents / 100:.2f}"

    balance_display.short_description = "Balance"


@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    """
    Admin configuration for LedgerEntry.

    Ledger entries are immutable - they cannot be edited or deleted
    through the admin interface. Corrections should be made via
    new adjustment entries.
    """

    list_display = [
        "id",
        "created_at",
        "entry_type",
        "amount_display",
        "debit_account",
        "credit_account",
        "reference_type",
        "created_by",
    ]
    list_filter = ["entry_type", "reference_type", "created_at"]
    search_fields = [
        "id",
        "idempotency_key",
        "reference_id",
        "description",
        "created_by",
    ]
    readonly_fields = [
        "id",
        "created_at",
        "debit_account",
        "credit_account",
        "amount_cents",
        "currency",
        "entry_type",
        "reference_id",
        "reference_type",
        "description",
        "metadata",
        "created_by",
        "idempotency_key",
    ]
    date_hierarchy = "created_at"
    ordering = ["-created_at"]

    fieldsets = (
        (
            "Entry Details",
            {
                "fields": (
                    "id",
                    "entry_type",
                    "amount_cents",
                    "currency",
                    "created_at",
                ),
            },
        ),
        (
            "Accounts",
            {
                "fields": ("debit_account", "credit_account"),
            },
        ),
        (
            "Reference",
            {
                "fields": (
                    "reference_type",
                    "reference_id",
                    "idempotency_key",
                ),
            },
        ),
        (
            "Additional Info",
            {
                "fields": ("description", "metadata", "created_by"),
            },
        ),
    )

    def amount_display(self, obj: LedgerEntry) -> str:
        """Display the amount formatted as currency."""
        return f"${obj.amount_cents / 100:.2f}"

    amount_display.short_description = "Amount"

    def has_delete_permission(self, request, obj=None) -> bool:
        """
        Ledger entries are immutable - disable delete.

        Corrections should be made via new adjustment entries,
        not by deleting existing entries.
        """
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        """
        Ledger entries are immutable - disable edit.

        Once recorded, entries cannot be modified. Create
        adjustment entries for corrections.
        """
        return False

    def has_add_permission(self, request) -> bool:
        """
        Disable adding entries through admin.

        Entries should only be created through the LedgerService
        to ensure proper validation and transaction handling.
        """
        return False
