from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from authentication.models import User

logger = logging.getLogger(__name__)


class BiometricService:
    """
    Biometric authentication service (Face ID / Touch ID).

    This service handles biometric authentication flow:
    1. Enrollment: Store user's public key (from iOS Secure Enclave)
    2. Challenge: Generate time-limited nonce for signing
    3. Authentication: Verify signature and issue JWT tokens

    Security:
        - Public keys stored as base64-encoded DER format (P-256 curve)
        - Challenges stored in Redis with 5-minute TTL
        - Challenges are single-use (deleted after verification attempt)
        - ECDSA signature verification using cryptography library
        - No user enumeration: status returns false for non-existent users

    Usage:
        from authentication.services import BiometricService

        # Check if user has biometric enabled
        enabled = BiometricService.check_status('user@example.com')

        # Enroll a new public key
        BiometricService.enroll(user, public_key_base64)

        # Create a challenge for authentication
        challenge_data = BiometricService.create_challenge('user@example.com')

        # Verify signature and authenticate
        user = BiometricService.authenticate(email, challenge, signature)
    """

    # Challenge expiration time in seconds (5 minutes)
    CHALLENGE_TTL_SECONDS = 300

    # Redis key prefix for biometric challenges
    CHALLENGE_KEY_PREFIX = "biometric_challenge"

    @staticmethod
    def _get_cache():
        """Get Django cache backend (Redis)."""
        from django.core.cache import cache

        return cache

    @staticmethod
    def _generate_challenge() -> str:
        """
        Generate a cryptographically secure challenge nonce.

        Returns:
            Base64-encoded 32-byte random nonce
        """
        import secrets
        import base64

        return base64.b64encode(secrets.token_bytes(32)).decode("ascii")

    @staticmethod
    def _get_challenge_key(email: str, challenge: str) -> str:
        """
        Build Redis key for a challenge.

        Args:
            email: User's email
            challenge: Challenge nonce

        Returns:
            Redis key string
        """
        email_normalized = email.lower().strip()
        return f"{BiometricService.CHALLENGE_KEY_PREFIX}:{email_normalized}:{challenge}"

    @staticmethod
    def check_status(email: str) -> bool:
        """
        Check if user has biometric authentication enabled.

        Args:
            email: User's email address

        Returns:
            True if biometric is enabled, False otherwise.
            Returns False for non-existent users (no enumeration).
        """
        from authentication.models import User

        try:
            user = User.objects.select_related("profile").get(
                email=email.lower().strip()
            )
            return bool(user.profile.bio_public_key)
        except (User.DoesNotExist, AttributeError):
            return False

    @staticmethod
    def enroll(user: User, public_key: str) -> None:
        """
        Enroll biometric authentication for a user.

        Stores the public key from the user's device (generated in Secure Enclave
        with biometric protection). Overwrites any existing key (single device).

        Args:
            user: User to enroll
            public_key: Base64-encoded EC public key (DER format, P-256)

        Raises:
            ValueError: If public key is invalid
        """
        from authentication.services.auth_service import AuthService

        # Validate public key format
        BiometricService._validate_public_key(public_key)

        # Get or create profile and store key
        profile = AuthService.get_or_create_profile(user)
        profile.bio_public_key = public_key
        profile.save(update_fields=["bio_public_key", "updated_at"])

        logger.info(
            f"Biometric enrolled for user: {user.email}", extra={"user_id": user.id}
        )

    @staticmethod
    def _validate_public_key(public_key: str) -> None:
        """
        Validate that the public key is a valid EC P-256 key.

        Args:
            public_key: Base64-encoded DER public key

        Raises:
            ValueError: If key is invalid
        """
        import base64
        from cryptography.hazmat.primitives.serialization import load_der_public_key
        from cryptography.hazmat.primitives.asymmetric import ec

        try:
            # Decode base64
            key_bytes = base64.b64decode(public_key)

            # Load as DER public key
            key = load_der_public_key(key_bytes)

            # Verify it's an EC key on P-256 curve
            if not isinstance(key, ec.EllipticCurvePublicKey):
                raise ValueError("Key is not an elliptic curve public key")

            if not isinstance(key.curve, ec.SECP256R1):
                raise ValueError("Key must use P-256 (secp256r1) curve")

        except (ValueError, TypeError) as e:
            raise ValueError(f"Invalid public key: {e}")
        except Exception as e:
            raise ValueError(f"Failed to parse public key: {e}")

    @staticmethod
    def disable(user: User) -> None:
        """
        Disable biometric authentication for a user.

        Clears the stored public key, requiring re-enrollment to use biometrics.

        Args:
            user: User to disable biometric for
        """
        try:
            profile = user.profile
            profile.bio_public_key = None
            profile.save(update_fields=["bio_public_key", "updated_at"])

            logger.info(
                f"Biometric disabled for user: {user.email}", extra={"user_id": user.id}
            )
        except AttributeError:
            pass  # No profile exists

    @staticmethod
    def create_challenge(email: str) -> dict:
        """
        Create a challenge for biometric authentication.

        Generates a cryptographically secure nonce and stores it in Redis
        with a 5-minute TTL. The challenge must be signed by the user's
        private key (protected by biometrics) to complete authentication.

        Args:
            email: User's email address

        Returns:
            Dict with 'challenge' and 'expires_in' keys

        Raises:
            ValueError: If user not found or biometric not enabled
        """
        from authentication.models import User

        email_normalized = email.lower().strip()

        # Verify user exists and has biometric enabled
        try:
            user = User.objects.select_related("profile").get(email=email_normalized)
            if not user.profile.bio_public_key:
                raise ValueError("Biometric authentication is not enabled")
        except User.DoesNotExist:
            raise ValueError("User not found")
        except AttributeError:
            raise ValueError("Biometric authentication is not enabled")

        # Generate challenge
        challenge = BiometricService._generate_challenge()

        # Store in Redis with TTL
        cache = BiometricService._get_cache()
        key = BiometricService._get_challenge_key(email_normalized, challenge)
        cache.set(key, user.id, timeout=BiometricService.CHALLENGE_TTL_SECONDS)

        logger.debug(
            f"Biometric challenge created for user: {email_normalized}",
            extra={"user_id": user.id},
        )

        return {
            "challenge": challenge,
            "expires_in": BiometricService.CHALLENGE_TTL_SECONDS,
        }

    @staticmethod
    def authenticate(email: str, challenge: str, signature: str) -> User:
        """
        Authenticate user with biometric signature.

        Verifies that the signature is valid for the challenge using the
        user's stored public key. Challenge is deleted after verification
        attempt (success or failure) to prevent replay attacks.

        Args:
            email: User's email address
            challenge: The challenge nonce that was signed
            signature: Base64-encoded ECDSA signature

        Returns:
            Authenticated User instance

        Raises:
            ValueError: If authentication fails (invalid signature, expired
                       challenge, user not found, etc.)
        """
        import base64
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.serialization import load_der_public_key
        from cryptography.exceptions import InvalidSignature
        from authentication.models import User

        email_normalized = email.lower().strip()
        cache = BiometricService._get_cache()
        cache_key = BiometricService._get_challenge_key(email_normalized, challenge)

        try:
            # Retrieve and delete challenge from Redis (single-use)
            user_id = cache.get(cache_key)
            cache.delete(cache_key)

            if user_id is None:
                raise ValueError("Invalid or expired challenge")

            # Get user and public key
            user = User.objects.select_related("profile").get(id=user_id)
            public_key_b64 = user.profile.bio_public_key

            if not public_key_b64:
                raise ValueError("Biometric authentication is not enabled")

            # Load public key
            public_key_bytes = base64.b64decode(public_key_b64)
            public_key = load_der_public_key(public_key_bytes)

            # Decode challenge and signature
            challenge_bytes = base64.b64decode(challenge)
            signature_bytes = base64.b64decode(signature)

            # Verify signature
            public_key.verify(
                signature_bytes, challenge_bytes, ec.ECDSA(hashes.SHA256())
            )

            # Check if user is active
            if not user.is_active:
                raise ValueError("User account is deactivated")

            logger.info(
                f"Biometric authentication successful for user: {user.email}",
                extra={"user_id": user.id},
            )

            return user

        except User.DoesNotExist:
            raise ValueError("User not found")
        except InvalidSignature:
            logger.warning(
                f"Biometric authentication failed: invalid signature for {email_normalized}"
            )
            raise ValueError("Invalid signature")
        except (ValueError, TypeError) as e:
            if "Invalid" in str(e) or "expired" in str(e) or "not found" in str(e):
                raise
            raise ValueError(f"Authentication failed: {e}")
