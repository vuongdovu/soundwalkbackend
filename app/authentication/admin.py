"""
Django admin configuration for authentication models.

This module registers User, Profile, LinkedAccount, and EmailVerificationToken
with the Django admin site for management.

Related files:
    - models.py: Model definitions
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from authentication.models import User, Profile, LinkedAccount, EmailVerificationToken


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """
    Admin configuration for User model (slim version).

    Customized for email-based authentication. Profile data (name, etc.)
    is managed via ProfileAdmin.
    """

    # List display
    list_display = (
        "email",
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
        "date_joined",
    )
    search_fields = ("email",)
    ordering = ("-date_joined",)

    # Field configuration
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (
            "Status",
            {"fields": ("email_verified", "is_active", "is_staff", "is_superuser")},
        ),
        (
            "Permissions",
            {"fields": ("groups", "user_permissions")},
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
                "fields": ("email", "password1", "password2"),
            },
        ),
    )

    # Read-only fields
    readonly_fields = ("date_joined", "last_login")


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    """
    Admin configuration for Profile model.

    Profile stores user identity data (name, username, avatar) and preferences.
    """

    list_display = (
        "user",
        "username",
        "first_name",
        "last_name",
        "timezone",
        "created_at",
    )
    list_filter = ("timezone", "created_at")
    search_fields = ("user__email", "username", "first_name", "last_name")
    ordering = ("-created_at",)

    # Show user info in detail view
    raw_id_fields = ("user",)
    readonly_fields = ("created_at", "updated_at")

    fieldsets = (
        (
            "User",
            {"fields": ("user",)},
        ),
        (
            "Identity",
            {"fields": ("username", "first_name", "last_name", "profile_picture")},
        ),
        (
            "Preferences",
            {"fields": ("timezone", "preferences")},
        ),
        (
            "Timestamps",
            {"fields": ("created_at", "updated_at")},
        ),
    )


@admin.register(LinkedAccount)
class LinkedAccountAdmin(admin.ModelAdmin):
    """
    Admin configuration for LinkedAccount model.

    LinkedAccount tracks authentication providers linked to a user.
    """

    list_display = (
        "user",
        "provider",
        "provider_user_id",
        "created_at",
    )
    list_filter = ("provider", "created_at")
    search_fields = ("user__email", "provider_user_id")
    ordering = ("-created_at",)

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
