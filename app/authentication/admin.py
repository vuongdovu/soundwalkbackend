"""
Django admin configuration for authentication models.

This module registers User, Profile, and EmailVerificationToken
with the Django admin site for management.

Related files:
    - models.py: Model definitions
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from authentication.models import User, Profile, EmailVerificationToken


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """
    Admin configuration for User model.

    Customized for email-based authentication (no username).
    """

    # List display
    list_display = (
        "email",
        "first_name",
        "last_name",
        "oauth_provider",
        "email_verified",
        "is_active",
        "is_staff",
        "date_joined",
    )
    list_filter = (
        "is_active",
        "is_staff",
        "is_superuser",
        "email_verified",
        "oauth_provider",
        "date_joined",
    )
    search_fields = ("email", "first_name", "last_name")
    ordering = ("-date_joined",)

    # Field configuration
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (
            "Personal info",
            {"fields": ("first_name", "last_name")},
        ),
        (
            "Authentication",
            {"fields": ("oauth_provider", "email_verified")},
        ),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                ),
            },
        ),
        (
            "Important dates",
            {"fields": ("date_joined", "last_login")},
        ),
    )

    # Fields for creating a new user
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "email",
                    "first_name",
                    "last_name",
                    "password1",
                    "password2",
                ),
            },
        ),
    )

    # Read-only fields
    readonly_fields = ("date_joined", "last_login")


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    """
    Admin configuration for Profile model.
    """

    list_display = (
        "user",
        "display_name",
        "timezone",
        "created_at",
        "updated_at",
    )
    list_filter = ("timezone", "created_at")
    search_fields = ("user__email", "display_name")
    ordering = ("-created_at",)

    # Show user info in detail view
    raw_id_fields = ("user",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(EmailVerificationToken)
class EmailVerificationTokenAdmin(admin.ModelAdmin):
    """
    Admin configuration for EmailVerificationToken model.
    """

    list_display = (
        "user",
        "token_type",
        "is_valid_display",
        "created_at",
        "expires_at",
        "used_at",
    )
    list_filter = ("token_type", "created_at", "expires_at")
    search_fields = ("user__email", "token")
    ordering = ("-created_at",)

    raw_id_fields = ("user",)
    readonly_fields = ("token", "created_at")

    def is_valid_display(self, obj):
        """Display whether token is still valid."""
        return obj.is_valid

    is_valid_display.boolean = True
    is_valid_display.short_description = "Valid"
