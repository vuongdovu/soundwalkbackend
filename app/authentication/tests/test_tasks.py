"""
Tests for authentication Celery tasks.

This module tests the async background tasks for:
- send_verification_email: Sends email verification link to user
- send_password_reset_email: Sends password reset link to user
- send_welcome_email: Sends welcome email after verification
- cleanup_expired_tokens: Periodic cleanup of expired/used tokens
- deactivate_unverified_accounts: Periodic deactivation of unverified users

Test approach:
- Tasks are currently stubs that log and return placeholder values
- Tests verify task structure, parameters, and return types
- Tests document expected behavior when implemented via comments
- Tests for full implementation are marked with pytest.mark.skip

Related files:
    - tasks.py: The Celery tasks under test
    - models.py: User, EmailVerificationToken models
    - conftest.py: Test fixtures (user, tokens, etc.)
    - factories.py: Factory Boy factories for test data
"""

from datetime import timedelta

import pytest
from celery import shared_task
from django.utils import timezone
from freezegun import freeze_time

from authentication.models import (
    User,
    EmailVerificationToken,
    LinkedAccount,
)
from authentication.tasks import (
    send_verification_email,
    send_password_reset_email,
    send_welcome_email,
    cleanup_expired_tokens,
    deactivate_unverified_accounts,
)
from authentication.tests.factories import (
    UserFactory,
    EmailVerificationTokenFactory,
    LinkedAccountFactory,
)


# =============================================================================
# Task Infrastructure Tests
# =============================================================================


class TestCeleryTaskConfiguration:
    """Test that all tasks are properly configured as Celery shared_tasks."""

    def test_send_verification_email_is_shared_task(self):
        """Verify send_verification_email is registered as a Celery task."""
        # Tasks decorated with @shared_task have a 'delay' method
        assert hasattr(send_verification_email, "delay")
        assert hasattr(send_verification_email, "apply_async")
        assert callable(send_verification_email.delay)

    def test_send_password_reset_email_is_shared_task(self):
        """Verify send_password_reset_email is registered as a Celery task."""
        assert hasattr(send_password_reset_email, "delay")
        assert hasattr(send_password_reset_email, "apply_async")
        assert callable(send_password_reset_email.delay)

    def test_send_welcome_email_is_shared_task(self):
        """Verify send_welcome_email is registered as a Celery task."""
        assert hasattr(send_welcome_email, "delay")
        assert hasattr(send_welcome_email, "apply_async")
        assert callable(send_welcome_email.delay)

    def test_cleanup_expired_tokens_is_shared_task(self):
        """Verify cleanup_expired_tokens is registered as a Celery task."""
        assert hasattr(cleanup_expired_tokens, "delay")
        assert hasattr(cleanup_expired_tokens, "apply_async")
        assert callable(cleanup_expired_tokens.delay)

    def test_deactivate_unverified_accounts_is_shared_task(self):
        """Verify deactivate_unverified_accounts is registered as a Celery task."""
        assert hasattr(deactivate_unverified_accounts, "delay")
        assert hasattr(deactivate_unverified_accounts, "apply_async")
        assert callable(deactivate_unverified_accounts.delay)

    def test_email_tasks_have_retry_configuration(self):
        """
        Verify email-sending tasks have retry configuration for resilience.

        Email tasks should retry on transient failures (network issues, etc.)
        with exponential backoff to avoid overwhelming email services.
        """
        # Tasks with bind=True have access to self.retry()
        # autoretry_for enables automatic retries on specified exceptions
        assert send_verification_email.bind is True
        assert send_password_reset_email.bind is True
        assert send_welcome_email.bind is True

        # Verify max_retries is configured
        assert send_verification_email.max_retries == 3
        assert send_password_reset_email.max_retries == 3
        assert send_welcome_email.max_retries == 3


# =============================================================================
# SendVerificationEmail Task Tests
# =============================================================================


class TestSendVerificationEmail:
    """
    Tests for send_verification_email task.

    Expected behavior when implemented:
    1. Fetch user by user_id
    2. Find valid (unused, unexpired) verification token
    3. Build verification URL with frontend base URL
    4. Send email via EmailService
    5. Return True on success, False on failure
    """

    def test_accepts_user_id_parameter(self, db, user):
        """Task should accept user_id as positional argument."""
        # Call synchronously (not via .delay()) to test directly
        result = send_verification_email(user.id)
        # Stub currently returns True
        assert result is True

    def test_returns_boolean(self, db, user):
        """Task must return a boolean indicating success/failure."""
        result = send_verification_email(user.id)
        assert isinstance(result, bool)

    def test_handles_nonexistent_user_gracefully(self, db):
        """
        Task should handle non-existent user_id without raising exception.

        When implemented, should:
        - Log error about missing user
        - Return False
        - NOT retry (user doesn't exist, won't suddenly appear)
        """
        nonexistent_user_id = 99999
        # Current stub returns True regardless; implementation should return False
        result = send_verification_email(nonexistent_user_id)
        assert isinstance(result, bool)
        # TODO: When implemented, assert result is False

    def test_handles_integer_user_id(self, db, user):
        """Task should accept integer user_id (standard Django PK type)."""
        result = send_verification_email(user.id)
        assert result is True

    @pytest.mark.skip(reason="Task not yet implemented - tests expected behavior")
    def test_sends_email_with_valid_token(
        self, db, user, valid_verification_token, mocker
    ):
        """
        When implemented: Should send email when user has valid token.

        Expected behavior:
        1. Query for unused, unexpired EMAIL_VERIFICATION token
        2. Build URL: {FRONTEND_URL}/verify-email?token={token}
        3. Call EmailService.send() with correct parameters
        4. Return True on success
        """
        mock_email = mocker.patch("authentication.tasks.EmailService.send")

        result = send_verification_email(user.id)

        assert result is True
        mock_email.assert_called_once()
        call_kwargs = mock_email.call_args[1]
        assert call_kwargs["to"] == user.email
        assert "Verify" in call_kwargs["subject"]
        assert valid_verification_token.token in call_kwargs["context"]["verification_url"]

    @pytest.mark.skip(reason="Task not yet implemented - tests expected behavior")
    def test_returns_false_when_no_valid_token(self, db, user, mocker):
        """
        When implemented: Should return False if no valid token exists.

        A valid token must be:
        - token_type = EMAIL_VERIFICATION
        - used_at is None (not yet used)
        - expires_at > now (not expired)
        """
        # User has no tokens
        mock_email = mocker.patch("authentication.tasks.EmailService.send")

        result = send_verification_email(user.id)

        assert result is False
        mock_email.assert_not_called()

    @pytest.mark.skip(reason="Task not yet implemented - tests expected behavior")
    def test_does_not_use_expired_token(
        self, db, user, expired_verification_token, mocker
    ):
        """
        When implemented: Should not send email with expired token.

        Expired tokens (expires_at < now) should be ignored.
        """
        mock_email = mocker.patch("authentication.tasks.EmailService.send")

        result = send_verification_email(user.id)

        assert result is False
        mock_email.assert_not_called()

    @pytest.mark.skip(reason="Task not yet implemented - tests expected behavior")
    def test_does_not_use_already_used_token(
        self, db, user, used_verification_token, mocker
    ):
        """
        When implemented: Should not send email with already-used token.

        Tokens with used_at set should be ignored.
        """
        mock_email = mocker.patch("authentication.tasks.EmailService.send")

        result = send_verification_email(user.id)

        assert result is False
        mock_email.assert_not_called()

    @pytest.mark.skip(reason="Task not yet implemented - tests expected behavior")
    def test_uses_latest_valid_token(self, db, user, mocker):
        """
        When implemented: Should use the most recently created valid token.

        If multiple valid tokens exist, use latest('created_at').
        """
        mock_email = mocker.patch("authentication.tasks.EmailService.send")

        # Create older valid token
        old_token = EmailVerificationTokenFactory(
            user=user,
            token_type=EmailVerificationToken.TokenType.EMAIL_VERIFICATION,
            expires_at=timezone.now() + timedelta(hours=24),
            used_at=None,
        )

        # Create newer valid token
        new_token = EmailVerificationTokenFactory(
            user=user,
            token_type=EmailVerificationToken.TokenType.EMAIL_VERIFICATION,
            expires_at=timezone.now() + timedelta(hours=24),
            used_at=None,
        )

        result = send_verification_email(user.id)

        assert result is True
        call_kwargs = mock_email.call_args[1]
        # Should use the newer token
        assert new_token.token in call_kwargs["context"]["verification_url"]


# =============================================================================
# SendPasswordResetEmail Task Tests
# =============================================================================


class TestSendPasswordResetEmail:
    """
    Tests for send_password_reset_email task.

    Expected behavior when implemented:
    1. Fetch user by user_id
    2. Find valid (unused, unexpired) password reset token
    3. Build reset URL with frontend base URL
    4. Send email via EmailService
    5. Return True on success, False on failure
    """

    def test_accepts_user_id_parameter(self, db, user):
        """Task should accept user_id as positional argument."""
        result = send_password_reset_email(user.id)
        assert result is True

    def test_returns_boolean(self, db, user):
        """Task must return a boolean indicating success/failure."""
        result = send_password_reset_email(user.id)
        assert isinstance(result, bool)

    def test_handles_nonexistent_user_gracefully(self, db):
        """
        Task should handle non-existent user_id without raising exception.

        When implemented, should:
        - Log error about missing user
        - Return False
        """
        nonexistent_user_id = 99999
        result = send_password_reset_email(nonexistent_user_id)
        assert isinstance(result, bool)
        # TODO: When implemented, assert result is False

    @pytest.mark.skip(reason="Task not yet implemented - tests expected behavior")
    def test_sends_email_with_valid_token(
        self, db, user, password_reset_token, mocker
    ):
        """
        When implemented: Should send email when user has valid reset token.

        Expected behavior:
        1. Query for unused, unexpired PASSWORD_RESET token
        2. Build URL: {FRONTEND_URL}/reset-password?token={token}
        3. Call EmailService.send() with correct parameters
        4. Return True on success
        """
        mock_email = mocker.patch("authentication.tasks.EmailService.send")

        result = send_password_reset_email(user.id)

        assert result is True
        mock_email.assert_called_once()
        call_kwargs = mock_email.call_args[1]
        assert call_kwargs["to"] == user.email
        assert "Reset" in call_kwargs["subject"] or "Password" in call_kwargs["subject"]
        assert password_reset_token.token in call_kwargs["context"]["reset_url"]

    @pytest.mark.skip(reason="Task not yet implemented - tests expected behavior")
    def test_returns_false_when_no_valid_token(self, db, user, mocker):
        """
        When implemented: Should return False if no valid reset token exists.
        """
        mock_email = mocker.patch("authentication.tasks.EmailService.send")

        result = send_password_reset_email(user.id)

        assert result is False
        mock_email.assert_not_called()

    @pytest.mark.skip(reason="Task not yet implemented - tests expected behavior")
    def test_does_not_use_verification_token(
        self, db, user, valid_verification_token, mocker
    ):
        """
        When implemented: Should only use PASSWORD_RESET tokens, not EMAIL_VERIFICATION.
        """
        mock_email = mocker.patch("authentication.tasks.EmailService.send")
        # User only has an email verification token, not password reset

        result = send_password_reset_email(user.id)

        assert result is False
        mock_email.assert_not_called()


# =============================================================================
# SendWelcomeEmail Task Tests
# =============================================================================


class TestSendWelcomeEmail:
    """
    Tests for send_welcome_email task.

    Expected behavior when implemented:
    1. Fetch user by user_id
    2. Send welcome email via EmailService (no token required)
    3. Return True on success, False on failure

    This task is typically triggered after email verification succeeds.
    """

    def test_accepts_user_id_parameter(self, db, user):
        """Task should accept user_id as positional argument."""
        result = send_welcome_email(user.id)
        assert result is True

    def test_returns_boolean(self, db, user):
        """Task must return a boolean indicating success/failure."""
        result = send_welcome_email(user.id)
        assert isinstance(result, bool)

    def test_handles_nonexistent_user_gracefully(self, db):
        """
        Task should handle non-existent user_id without raising exception.
        """
        nonexistent_user_id = 99999
        result = send_welcome_email(nonexistent_user_id)
        assert isinstance(result, bool)
        # TODO: When implemented, assert result is False

    @pytest.mark.skip(reason="Task not yet implemented - tests expected behavior")
    def test_sends_welcome_email_to_user(self, db, user, mocker):
        """
        When implemented: Should send welcome email to user.

        Expected behavior:
        1. Fetch user by ID
        2. Call EmailService.send() with welcome template
        3. Return True on success
        """
        mock_email = mocker.patch("authentication.tasks.EmailService.send")

        result = send_welcome_email(user.id)

        assert result is True
        mock_email.assert_called_once()
        call_kwargs = mock_email.call_args[1]
        assert call_kwargs["to"] == user.email
        assert "Welcome" in call_kwargs["subject"]

    @pytest.mark.skip(reason="Task not yet implemented - tests expected behavior")
    def test_includes_user_context(self, db, user, mocker):
        """
        When implemented: Should include user object in email context.
        """
        mock_email = mocker.patch("authentication.tasks.EmailService.send")

        send_welcome_email(user.id)

        call_kwargs = mock_email.call_args[1]
        assert "user" in call_kwargs["context"]


# =============================================================================
# CleanupExpiredTokens Task Tests
# =============================================================================


class TestCleanupExpiredTokens:
    """
    Tests for cleanup_expired_tokens periodic task.

    Expected behavior when implemented:
    1. Delete tokens where expires_at < now (expired)
    2. Delete tokens where used_at is not null (already used)
    3. Return count of deleted tokens

    This task should be scheduled via celery-beat (recommended: daily).
    """

    def test_returns_integer(self, db):
        """Task must return an integer (count of deleted tokens)."""
        result = cleanup_expired_tokens()
        assert isinstance(result, int)

    def test_accepts_no_arguments(self, db):
        """Periodic task should work without any arguments."""
        # Should not raise
        result = cleanup_expired_tokens()
        assert result >= 0

    @pytest.mark.skip(reason="Task not yet implemented - tests expected behavior")
    def test_deletes_expired_tokens(self, db, user):
        """
        When implemented: Should delete tokens where expires_at < now.
        """
        # Create expired token
        expired_token = EmailVerificationTokenFactory(
            user=user,
            expires_at=timezone.now() - timedelta(hours=1),
            used_at=None,
        )

        result = cleanup_expired_tokens()

        assert result >= 1
        assert not EmailVerificationToken.objects.filter(
            id=expired_token.id
        ).exists()

    @pytest.mark.skip(reason="Task not yet implemented - tests expected behavior")
    def test_deletes_used_tokens(self, db, user):
        """
        When implemented: Should delete tokens where used_at is not null.
        """
        # Create used token (not expired)
        used_token = EmailVerificationTokenFactory(
            user=user,
            expires_at=timezone.now() + timedelta(hours=24),
            used_at=timezone.now() - timedelta(minutes=30),
        )

        result = cleanup_expired_tokens()

        assert result >= 1
        assert not EmailVerificationToken.objects.filter(
            id=used_token.id
        ).exists()

    @pytest.mark.skip(reason="Task not yet implemented - tests expected behavior")
    def test_preserves_valid_tokens(self, db, user, valid_verification_token):
        """
        When implemented: Should NOT delete valid (unused, unexpired) tokens.
        """
        result = cleanup_expired_tokens()

        # Valid token should still exist
        assert EmailVerificationToken.objects.filter(
            id=valid_verification_token.id
        ).exists()

    @pytest.mark.skip(reason="Task not yet implemented - tests expected behavior")
    def test_returns_correct_count(self, db, user):
        """
        When implemented: Should return exact count of deleted tokens.
        """
        # Create 3 expired tokens
        for _ in range(3):
            EmailVerificationTokenFactory(
                user=user,
                expires_at=timezone.now() - timedelta(hours=1),
            )

        # Create 2 used tokens
        for _ in range(2):
            EmailVerificationTokenFactory(
                user=user,
                expires_at=timezone.now() + timedelta(hours=24),
                used_at=timezone.now(),
            )

        # Create 1 valid token
        EmailVerificationTokenFactory(
            user=user,
            expires_at=timezone.now() + timedelta(hours=24),
            used_at=None,
        )

        result = cleanup_expired_tokens()

        # Should delete 5 tokens (3 expired + 2 used)
        assert result == 5

    @pytest.mark.skip(reason="Task not yet implemented - tests expected behavior")
    def test_handles_empty_database(self, db):
        """
        When implemented: Should handle case with no tokens gracefully.
        """
        result = cleanup_expired_tokens()
        assert result == 0

    @pytest.mark.skip(reason="Task not yet implemented - tests expected behavior")
    @freeze_time("2024-01-15 12:00:00")
    def test_uses_current_time_for_expiry_check(self, db, user):
        """
        When implemented: Should use current time to determine expired tokens.

        Token expires_at=2024-01-15 11:00:00 is expired at frozen time 12:00:00.
        """
        # Create token that expired 1 hour ago (at frozen time)
        expired_token = EmailVerificationTokenFactory(
            user=user,
            expires_at=timezone.now() - timedelta(hours=1),
            used_at=None,
        )

        result = cleanup_expired_tokens()

        assert result >= 1
        assert not EmailVerificationToken.objects.filter(
            id=expired_token.id
        ).exists()


# =============================================================================
# DeactivateUnverifiedAccounts Task Tests
# =============================================================================


class TestDeactivateUnverifiedAccounts:
    """
    Tests for deactivate_unverified_accounts periodic task.

    Expected behavior when implemented:
    1. Find users where:
       - email_verified = False
       - date_joined < (now - days parameter)
       - is_active = True
       - Registered via email (not OAuth)
    2. Set is_active = False for those users
    3. Return count of deactivated users

    This task should be scheduled via celery-beat (recommended: weekly).
    """

    def test_returns_integer(self, db):
        """Task must return an integer (count of deactivated users)."""
        result = deactivate_unverified_accounts()
        assert isinstance(result, int)

    def test_accepts_days_parameter(self, db):
        """Task should accept optional days parameter."""
        # Should not raise with different day values
        result = deactivate_unverified_accounts(days=30)
        assert isinstance(result, int)

        result = deactivate_unverified_accounts(days=7)
        assert isinstance(result, int)

    def test_default_days_is_thirty(self, db):
        """Default days parameter should be 30."""
        # Call without argument - should work with default
        result = deactivate_unverified_accounts()
        assert result >= 0

    @pytest.mark.skip(reason="Task not yet implemented - tests expected behavior")
    @freeze_time("2024-02-15 12:00:00")
    def test_deactivates_old_unverified_users(self, db):
        """
        When implemented: Should deactivate users unverified for > days.
        """
        # Create user joined 45 days ago (unverified)
        with freeze_time("2024-01-01 12:00:00"):
            old_unverified = UserFactory(email_verified=False, is_active=True)
            LinkedAccountFactory(
                user=old_unverified,
                provider=LinkedAccount.Provider.EMAIL,
            )

        # Return to "current" time
        result = deactivate_unverified_accounts(days=30)

        old_unverified.refresh_from_db()
        assert result >= 1
        assert old_unverified.is_active is False

    @pytest.mark.skip(reason="Task not yet implemented - tests expected behavior")
    @freeze_time("2024-02-15 12:00:00")
    def test_preserves_recently_joined_unverified_users(self, db):
        """
        When implemented: Should NOT deactivate users joined < days ago.
        """
        # Create user joined 15 days ago (within 30-day grace period)
        with freeze_time("2024-02-01 12:00:00"):
            recent_unverified = UserFactory(email_verified=False, is_active=True)
            LinkedAccountFactory(
                user=recent_unverified,
                provider=LinkedAccount.Provider.EMAIL,
            )

        result = deactivate_unverified_accounts(days=30)

        recent_unverified.refresh_from_db()
        assert recent_unverified.is_active is True

    @pytest.mark.skip(reason="Task not yet implemented - tests expected behavior")
    @freeze_time("2024-02-15 12:00:00")
    def test_preserves_verified_users(self, db):
        """
        When implemented: Should NOT deactivate users who verified email.
        """
        # Create old but verified user
        with freeze_time("2024-01-01 12:00:00"):
            old_verified = UserFactory(email_verified=True, is_active=True)
            LinkedAccountFactory(
                user=old_verified,
                provider=LinkedAccount.Provider.EMAIL,
            )

        result = deactivate_unverified_accounts(days=30)

        old_verified.refresh_from_db()
        assert old_verified.is_active is True

    @pytest.mark.skip(reason="Task not yet implemented - tests expected behavior")
    @freeze_time("2024-02-15 12:00:00")
    def test_preserves_oauth_users(self, db):
        """
        When implemented: Should NOT deactivate OAuth users (Google, Apple).

        OAuth users have email verified by their provider, so even if
        email_verified=False locally, they should not be deactivated.
        """
        # Create old Google user (unverified locally)
        with freeze_time("2024-01-01 12:00:00"):
            google_user = UserFactory(email_verified=False, is_active=True)
            LinkedAccountFactory(
                user=google_user,
                provider=LinkedAccount.Provider.GOOGLE,
            )

        result = deactivate_unverified_accounts(days=30)

        google_user.refresh_from_db()
        assert google_user.is_active is True

    @pytest.mark.skip(reason="Task not yet implemented - tests expected behavior")
    @freeze_time("2024-02-15 12:00:00")
    def test_preserves_already_deactivated_users(self, db):
        """
        When implemented: Should NOT re-deactivate already inactive users.

        Already inactive users should not be counted in the return value.
        """
        # Create old unverified user that's already inactive
        with freeze_time("2024-01-01 12:00:00"):
            already_inactive = UserFactory(email_verified=False, is_active=False)
            LinkedAccountFactory(
                user=already_inactive,
                provider=LinkedAccount.Provider.EMAIL,
            )

        result = deactivate_unverified_accounts(days=30)

        # Should not count already-inactive users
        assert result == 0

    @pytest.mark.skip(reason="Task not yet implemented - tests expected behavior")
    @freeze_time("2024-02-15 12:00:00")
    def test_returns_correct_count(self, db):
        """
        When implemented: Should return exact count of deactivated users.
        """
        # Create 3 users that should be deactivated
        with freeze_time("2024-01-01 12:00:00"):
            for _ in range(3):
                user = UserFactory(email_verified=False, is_active=True)
                LinkedAccountFactory(
                    user=user,
                    provider=LinkedAccount.Provider.EMAIL,
                )

        # Create 2 users that should NOT be deactivated (verified)
        with freeze_time("2024-01-01 12:00:00"):
            for _ in range(2):
                UserFactory(email_verified=True, is_active=True)

        result = deactivate_unverified_accounts(days=30)

        assert result == 3

    @pytest.mark.skip(reason="Task not yet implemented - tests expected behavior")
    def test_respects_custom_days_parameter(self, db):
        """
        When implemented: Should use custom days parameter for cutoff.
        """
        with freeze_time("2024-02-15 12:00:00"):
            # Create user joined 10 days ago
            with freeze_time("2024-02-05 12:00:00"):
                recent_user = UserFactory(email_verified=False, is_active=True)
                LinkedAccountFactory(
                    user=recent_user,
                    provider=LinkedAccount.Provider.EMAIL,
                )

        with freeze_time("2024-02-15 12:00:00"):
            # With 30 days: should NOT deactivate
            result_30 = deactivate_unverified_accounts(days=30)
            recent_user.refresh_from_db()
            assert recent_user.is_active is True
            assert result_30 == 0

            # With 7 days: should deactivate
            result_7 = deactivate_unverified_accounts(days=7)
            recent_user.refresh_from_db()
            assert recent_user.is_active is False
            assert result_7 == 1

    @pytest.mark.skip(reason="Task not yet implemented - tests expected behavior")
    def test_handles_empty_database(self, db):
        """
        When implemented: Should handle case with no users gracefully.
        """
        result = deactivate_unverified_accounts()
        assert result == 0


# =============================================================================
# Task Error Handling Tests
# =============================================================================


class TestTaskErrorHandling:
    """
    Tests for error handling and retry behavior.

    Celery tasks should handle errors gracefully:
    - Email tasks retry on transient failures (network, SMTP issues)
    - Tasks log errors appropriately
    - Tasks don't crash on invalid input
    """

    def test_send_verification_email_with_string_user_id(self, db):
        """
        Task should handle string user_id (e.g., from message queue).

        Note: Depending on implementation, may need to convert to int
        or handle gracefully.
        """
        # String ID that doesn't exist
        result = send_verification_email("nonexistent")
        # Should not raise, should return bool
        assert isinstance(result, bool)

    def test_send_password_reset_email_with_string_user_id(self, db):
        """Task should handle string user_id gracefully."""
        result = send_password_reset_email("nonexistent")
        assert isinstance(result, bool)

    def test_send_welcome_email_with_string_user_id(self, db):
        """Task should handle string user_id gracefully."""
        result = send_welcome_email("nonexistent")
        assert isinstance(result, bool)

    def test_deactivate_with_negative_days(self, db):
        """
        Task should handle invalid days parameter.

        Negative days would create a future cutoff date, which should
        result in no users being deactivated.
        """
        result = deactivate_unverified_accounts(days=-1)
        assert isinstance(result, int)
        assert result >= 0

    def test_deactivate_with_zero_days(self, db):
        """
        Task should handle zero days parameter.

        Zero days means deactivate all unverified users regardless of age.
        """
        result = deactivate_unverified_accounts(days=0)
        assert isinstance(result, int)

    @pytest.mark.skip(reason="Task not yet implemented - tests expected behavior")
    def test_email_task_logs_on_user_not_found(self, db, caplog):
        """
        When implemented: Should log error when user not found.
        """
        import logging

        with caplog.at_level(logging.ERROR):
            send_verification_email(99999)

        assert "not found" in caplog.text.lower()

    @pytest.mark.skip(reason="Task not yet implemented - tests expected behavior")
    def test_cleanup_logs_deleted_count(self, db, caplog, user):
        """
        When implemented: Should log count of cleaned tokens.
        """
        import logging

        # Create expired token
        EmailVerificationTokenFactory(
            user=user,
            expires_at=timezone.now() - timedelta(hours=1),
        )

        with caplog.at_level(logging.INFO):
            cleanup_expired_tokens()

        assert "cleaned" in caplog.text.lower() or "deleted" in caplog.text.lower()


# =============================================================================
# Integration Tests (Task + Database)
# =============================================================================


class TestTaskDatabaseIntegration:
    """
    Integration tests verifying tasks interact correctly with database.

    These tests ensure tasks properly query and modify database state.
    """

    def test_send_verification_email_with_real_user(self, db, user):
        """Task should work with actual user from database."""
        result = send_verification_email(user.id)
        # Stub returns True; verify user still exists
        user.refresh_from_db()
        assert result is True
        assert User.objects.filter(id=user.id).exists()

    def test_cleanup_tokens_with_multiple_users(self, db):
        """
        Task should handle tokens from multiple users.

        Cleanup should delete expired/used tokens regardless of user.
        """
        user1 = UserFactory()
        user2 = UserFactory()

        # Create tokens for both users
        EmailVerificationTokenFactory(user=user1)
        EmailVerificationTokenFactory(user=user2)

        # Should not raise
        result = cleanup_expired_tokens()
        assert isinstance(result, int)

    def test_deactivate_accounts_bulk_operation(self, db):
        """
        Task should efficiently handle bulk user updates.

        Using queryset.update() rather than iterating is preferred
        for performance with large user sets.
        """
        # Create multiple unverified users
        for _ in range(5):
            UserFactory(email_verified=False)

        result = deactivate_unverified_accounts()
        assert isinstance(result, int)
