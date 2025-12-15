"""
ConnectedAccount model for Stripe Connect integration.

This model represents a Stripe Connected Account that can receive payouts.
Each recipient/service provider has a ConnectedAccount linked to their profile.

Usage:
    from payments.models import ConnectedAccount

    # Create connected account for a profile
    account = ConnectedAccount.objects.create(
        profile=recipient_profile,
        stripe_account_id="acct_1234567890",
        onboarding_status=OnboardingStatus.IN_PROGRESS,
    )

    # Check if ready for payouts
    if account.is_ready_for_payouts:
        # Can create payouts to this account
        pass

    # Update after Stripe webhook
    account.onboarding_status = OnboardingStatus.COMPLETE
    account.payouts_enabled = True
    account.charges_enabled = True
    account.save()
"""

from __future__ import annotations

from django.db import models
from django.db.models import F

from core.models import BaseModel
from core.model_mixins import UUIDPrimaryKeyMixin

from payments.state_machines import OnboardingStatus


class ConnectedAccount(UUIDPrimaryKeyMixin, BaseModel):
    """
    Represents a Stripe Connected Account for receiving payouts.

    Links a user profile to their Stripe Connect account, tracking
    onboarding status and payout eligibility.

    Fields:
        profile: OneToOne link to the user's Profile
        stripe_account_id: Unique Stripe Account ID (acct_xxx)
        onboarding_status: Current state of Stripe Connect onboarding
        payouts_enabled: Whether Stripe has enabled payouts
        charges_enabled: Whether Stripe has enabled charges
        version: Optimistic locking version field
        metadata: Flexible JSON storage for additional data

    Properties:
        is_ready_for_payouts: True if account can receive payouts

    Lifecycle:
        1. Account created when recipient starts onboarding (NOT_STARTED)
        2. Onboarding link sent to recipient (IN_PROGRESS)
        3. Stripe verifies information (IN_PROGRESS)
        4. Stripe enables account (COMPLETE)
        5. Can now receive payouts

    Note:
        The profile field uses PROTECT on_delete to prevent accidental
        deletion of profiles with connected accounts. Handle cleanup
        explicitly in business logic.
    """

    profile = models.OneToOneField(
        "authentication.Profile",
        on_delete=models.PROTECT,
        related_name="connected_account",
        help_text="Profile this connected account belongs to",
    )

    stripe_account_id = models.CharField(
        max_length=255,
        unique=True,
        db_index=True,
        help_text="Stripe Account ID (acct_xxx)",
    )

    onboarding_status = models.CharField(
        max_length=20,
        choices=OnboardingStatus.choices,
        default=OnboardingStatus.NOT_STARTED,
        db_index=True,
        help_text="Current Stripe Connect onboarding status",
    )

    payouts_enabled = models.BooleanField(
        default=False,
        help_text="Whether Stripe has enabled payouts for this account",
    )

    charges_enabled = models.BooleanField(
        default=False,
        help_text="Whether Stripe has enabled charges for this account",
    )

    # Optimistic locking
    version = models.PositiveIntegerField(
        default=1,
        help_text="Version for optimistic locking - incremented on each save",
    )

    # Flexible metadata storage
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Arbitrary JSON metadata (e.g., business type, country)",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Connected Account"
        verbose_name_plural = "Connected Accounts"

    def __str__(self) -> str:
        """Return string representation with Stripe ID and status."""
        return f"ConnectedAccount({self.stripe_account_id}, {self.onboarding_status})"

    def save(self, *args, **kwargs):
        """
        Save with version auto-increment for optimistic locking.

        On update (not force_insert), atomically increments the version
        field to detect concurrent modifications.
        """
        is_update = self.pk and not kwargs.get("force_insert", False)
        if is_update:
            self.version = F("version") + 1
        super().save(*args, **kwargs)
        if is_update:
            # Refresh to get actual version value after F() expression
            self.refresh_from_db(fields=["version"])

    @property
    def is_ready_for_payouts(self) -> bool:
        """
        Check if account can receive payouts.

        An account is ready for payouts when:
        - Onboarding is complete
        - Stripe has enabled payouts for the account

        Returns:
            True if account can receive payouts
        """
        return (
            self.onboarding_status == OnboardingStatus.COMPLETE and self.payouts_enabled
        )

    @property
    def is_fully_enabled(self) -> bool:
        """
        Check if account is fully enabled for all operations.

        Returns:
            True if both payouts and charges are enabled
        """
        return self.payouts_enabled and self.charges_enabled
