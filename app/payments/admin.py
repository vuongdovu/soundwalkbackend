"""
Django admin configuration for payments app.

Registers:
    - Subscription: User subscriptions
    - Transaction: Payment history
    - WebhookEvent: Stripe webhook events

Usage:
    Access via /admin/payments/
"""


# TODO: Uncomment when models are implemented
# from .models import Subscription, Transaction, WebhookEvent


# TODO: Implement admin classes
# @admin.register(Subscription)
# class SubscriptionAdmin(admin.ModelAdmin):
#     """Admin for Subscription model."""
#
#     list_display = [
#         "user",
#         "plan_name",
#         "status",
#         "current_period_end",
#         "cancel_at_period_end",
#     ]
#     list_filter = ["status", "plan_name", "cancel_at_period_end"]
#     search_fields = ["user__email", "stripe_customer_id", "stripe_subscription_id"]
#     readonly_fields = [
#         "stripe_customer_id",
#         "stripe_subscription_id",
#         "created_at",
#         "updated_at",
#     ]
#     raw_id_fields = ["user"]
#
#     fieldsets = (
#         (None, {
#             "fields": ("user", "plan_name", "status"),
#         }),
#         ("Stripe IDs", {
#             "fields": (
#                 "stripe_customer_id",
#                 "stripe_subscription_id",
#                 "stripe_price_id",
#             ),
#         }),
#         ("Period", {
#             "fields": (
#                 "current_period_start",
#                 "current_period_end",
#                 "cancel_at_period_end",
#                 "canceled_at",
#             ),
#         }),
#         ("Trial", {
#             "fields": ("trial_start", "trial_end"),
#             "classes": ("collapse",),
#         }),
#         ("Metadata", {
#             "fields": ("metadata",),
#             "classes": ("collapse",),
#         }),
#         ("Timestamps", {
#             "fields": ("created_at", "updated_at"),
#             "classes": ("collapse",),
#         }),
#     )


# @admin.register(Transaction)
# class TransactionAdmin(admin.ModelAdmin):
#     """Admin for Transaction model."""
#
#     list_display = [
#         "id",
#         "user",
#         "transaction_type",
#         "status",
#         "amount_display",
#         "created_at",
#     ]
#     list_filter = ["transaction_type", "status", "currency"]
#     search_fields = [
#         "user__email",
#         "stripe_payment_intent_id",
#         "stripe_invoice_id",
#         "description",
#     ]
#     readonly_fields = [
#         "stripe_payment_intent_id",
#         "stripe_invoice_id",
#         "stripe_charge_id",
#         "created_at",
#         "updated_at",
#     ]
#     raw_id_fields = ["user", "subscription"]
#     date_hierarchy = "created_at"
#
#     fieldsets = (
#         (None, {
#             "fields": ("user", "subscription", "transaction_type", "status"),
#         }),
#         ("Amount", {
#             "fields": ("amount_cents", "currency", "description"),
#         }),
#         ("Stripe IDs", {
#             "fields": (
#                 "stripe_payment_intent_id",
#                 "stripe_invoice_id",
#                 "stripe_charge_id",
#             ),
#         }),
#         ("Failure Details", {
#             "fields": ("failure_code", "failure_message"),
#             "classes": ("collapse",),
#         }),
#         ("Metadata", {
#             "fields": ("metadata",),
#             "classes": ("collapse",),
#         }),
#         ("Timestamps", {
#             "fields": ("created_at", "updated_at"),
#             "classes": ("collapse",),
#         }),
#     )


# @admin.register(WebhookEvent)
# class WebhookEventAdmin(admin.ModelAdmin):
#     """Admin for WebhookEvent model."""
#
#     list_display = [
#         "stripe_event_id",
#         "event_type",
#         "processing_status",
#         "retry_count",
#         "created_at",
#         "processed_at",
#     ]
#     list_filter = ["processing_status", "event_type"]
#     search_fields = ["stripe_event_id", "event_type"]
#     readonly_fields = [
#         "stripe_event_id",
#         "event_type",
#         "payload",
#         "created_at",
#         "updated_at",
#         "processed_at",
#     ]
#     date_hierarchy = "created_at"
#
#     fieldsets = (
#         (None, {
#             "fields": ("stripe_event_id", "event_type", "processing_status"),
#         }),
#         ("Processing", {
#             "fields": ("processed_at", "retry_count", "error_message"),
#         }),
#         ("Payload", {
#             "fields": ("payload",),
#             "classes": ("collapse",),
#         }),
#         ("Timestamps", {
#             "fields": ("created_at", "updated_at"),
#             "classes": ("collapse",),
#         }),
#     )
#
#     def has_add_permission(self, request):
#         """Disable manual creation."""
#         return False
#
#     def has_change_permission(self, request, obj=None):
#         """Disable editing."""
#         return False
