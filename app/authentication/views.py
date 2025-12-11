"""
Authentication views.

This module provides API views for:
- Profile management (CRUD operations with conditional username validation)
- Email verification
- Social authentication (Google, Apple)
- Biometric authentication (Face ID / Touch ID)
- Custom auth endpoints not covered by dj-rest-auth

Related files:
    - serializers.py: Request/response serialization
    - services.py: Business logic (AuthService, BiometricService)
    - urls.py: URL routing
    - adapters.py: Social auth adapters

Note:
    Most authentication endpoints are handled by dj-rest-auth:
    - Login: /api/v1/auth/login/
    - Logout: /api/v1/auth/logout/
    - Register: /api/v1/auth/registration/
    - Password reset: /api/v1/auth/password/reset/
    - Password change: /api/v1/auth/password/change/
    - Current user: /api/v1/auth/user/

    Social login endpoints:
    - Google: /api/v1/auth/google/
    - Apple: /api/v1/auth/apple/

    Biometric endpoints:
    - Enroll: /api/v1/auth/biometric/enroll/
    - Challenge: /api/v1/auth/biometric/challenge/
    - Authenticate: /api/v1/auth/biometric/authenticate/
    - Status: /api/v1/auth/biometric/status/

    ProfileView handles both initial profile completion and subsequent updates.
    If profile.username is empty, username is required in the request.
"""

from allauth.socialaccount.providers.apple.views import AppleOAuth2Adapter
from allauth.socialaccount.providers.google.views import GoogleOAuth2Adapter
from dj_rest_auth.registration.views import SocialLoginView
from drf_spectacular.utils import (
    extend_schema,
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
)
from rest_framework import status
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from authentication.serializers import (
    ProfileSerializer,
    ProfileUpdateSerializer,
    UserSerializer,
    BiometricEnrollSerializer,
    BiometricEnrollResponseSerializer,
    BiometricChallengeRequestSerializer,
    BiometricChallengeResponseSerializer,
    BiometricAuthenticateSerializer,
    BiometricStatusSerializer,
    BiometricDisableResponseSerializer,
)
from authentication.services import AuthService, BiometricService


# =============================================================================
# Social Authentication Views
# =============================================================================


@extend_schema(
    summary="Sign in with Google",
    description=(
        "Authenticate using a Google OAuth2 token. For mobile apps, use the id_token "
        "from Google Sign-In SDK. For web apps, use the authorization code flow."
    ),
    tags=["Auth"],
)
class GoogleLoginView(SocialLoginView):
    """
    API view for Google OAuth2 authentication.

    POST: Authenticate with Google OAuth2 token

    URL: /api/v1/auth/google/

    Request body:
        {
            "access_token": "google_oauth_access_token"
        }
        OR
        {
            "id_token": "google_oauth_id_token"
        }
        OR (for authorization code flow)
        {
            "code": "authorization_code"
        }

    Returns:
        {
            "access": "jwt_access_token",
            "refresh": "jwt_refresh_token",
            "user": { ... }
        }

    Note:
        For mobile apps, use id_token from Google Sign-In SDK.
        For web apps, use authorization code flow.
    """

    adapter_class = GoogleOAuth2Adapter


@extend_schema(
    summary="Sign in with Apple",
    description=(
        "Authenticate using Apple Sign-In. Apple only sends the user's name on the "
        "first authentication, so it's captured and stored at that time."
    ),
    tags=["Auth"],
)
class AppleLoginView(SocialLoginView):
    """
    API view for Apple Sign-In authentication.

    POST: Authenticate with Apple Sign-In token

    URL: /api/v1/auth/apple/

    Request body:
        {
            "id_token": "apple_identity_token",
            "access_token": "apple_authorization_code"
        }

        On first login, Apple may also send user info:
        {
            "id_token": "...",
            "access_token": "...",
            "user": {
                "name": {
                    "firstName": "John",
                    "lastName": "Doe"
                },
                "email": "user@example.com"
            }
        }

    Returns:
        {
            "access": "jwt_access_token",
            "refresh": "jwt_refresh_token",
            "user": { ... }
        }

    Note:
        Apple only sends user's name on the FIRST authentication.
        The CustomSocialAccountAdapter captures this data.
    """

    adapter_class = AppleOAuth2Adapter


# =============================================================================
# Profile & Account Management Views
# =============================================================================


class ProfileView(APIView):
    """
    API view for user profile operations.

    GET: Retrieve current user's profile
    PUT/PATCH: Update current user's profile (including initial completion)

    URL: /api/v1/auth/profile/

    This endpoint handles both initial profile completion (setting username
    for the first time) and subsequent updates. If the profile has no username,
    the username field is required in the request.

    Response includes `is_complete` field indicating whether username is set.
    """

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @extend_schema(
        summary="Get current user's profile",
        description="Retrieve profile data including completion status.",
        tags=["Auth - Profile"],
        responses={200: ProfileSerializer},
    )
    def get(self, request):
        """
        Retrieve the current user's profile.

        Returns:
            Profile data including username, name, profile_picture, timezone,
            preferences, and is_complete status.
        """
        profile = AuthService.get_or_create_profile(request.user)
        serializer = ProfileSerializer(profile, context={"request": request})
        return Response(serializer.data)

    @extend_schema(
        summary="Update profile",
        description=(
            "Full profile update. Username is required if not previously set."
        ),
        tags=["Auth - Profile"],
        request=ProfileUpdateSerializer,
        responses={200: ProfileSerializer},
    )
    def put(self, request):
        """
        Update the current user's profile (full update).

        Request body:
            {
                "username": "johndoe",          // Required if not set, optional otherwise
                "first_name": "John",           // Optional
                "last_name": "Doe",             // Optional
                "profile_picture": <file>,      // Optional, image upload
                "timezone": "America/New_York", // Optional
                "preferences": {"theme": "dark"} // Optional
            }

        Returns:
            Updated profile data with is_complete status
        """
        return self._update_profile(request, partial=False)

    @extend_schema(
        summary="Partially update profile",
        description=(
            "Partial profile update. Username is required if not previously set."
        ),
        tags=["Auth - Profile"],
        request=ProfileUpdateSerializer,
        responses={200: ProfileSerializer},
    )
    def patch(self, request):
        """
        Partially update the current user's profile.

        Request body:
            Any subset of profile fields.
            Note: username is required if profile doesn't have one set.

        Returns:
            Updated profile data with is_complete status
        """
        return self._update_profile(request, partial=True)

    def _update_profile(self, request, partial=False):
        """
        Internal method to handle profile updates.

        Args:
            request: The HTTP request
            partial: Whether this is a partial update (PATCH)

        Returns:
            Response with updated profile data
        """
        profile = AuthService.get_or_create_profile(request.user)
        serializer = ProfileUpdateSerializer(
            profile,
            data=request.data,
            partial=partial,
            context={"request": request, "user": request.user},
        )
        serializer.is_valid(raise_exception=True)
        updated_profile = serializer.save()

        return Response(
            ProfileSerializer(updated_profile, context={"request": request}).data
        )


class EmailVerificationView(APIView):
    """
    API view for email verification.

    POST: Verify email with token

    URL: /api/v1/auth/verify-email/
    """

    permission_classes = []  # No authentication required

    @extend_schema(
        summary="Verify email address",
        description="Verify the user's email using the token sent to their inbox.",
        tags=["Auth"],
    )
    def post(self, request):
        """
        Verify email address with token.

        Request body:
            {
                "token": "verification_token_string"
            }

        Returns:
            Success message or error
        """
        token = request.data.get("token")
        if not token:
            return Response(
                {"detail": "Token is required"}, status=status.HTTP_400_BAD_REQUEST
            )

        success, message = AuthService.verify_email(token)

        if success:
            return Response({"detail": message})
        else:
            return Response({"detail": message}, status=status.HTTP_400_BAD_REQUEST)


class ResendEmailView(APIView):
    """
    API view to resend verification email.

    POST: Resend verification email to current user

    URL: /api/v1/auth/resend-email/
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Resend verification email",
        description="Send a new verification email to the current user.",
        tags=["Auth"],
    )
    def post(self, request):
        """
        Resend verification email to current user.

        Returns:
            Success message
        """
        if request.user.email_verified:
            return Response(
                {"detail": "Email is already verified"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        AuthService.send_verification_email(request.user)
        return Response({"detail": "Verification email sent"})


class DeactivateAccountView(APIView):
    """
    API view for account deactivation.

    POST: Deactivate (soft-delete) current user's account

    URL: /api/v1/auth/deactivate/
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Deactivate account",
        description=(
            "Soft-delete the current user's account. The account can be "
            "reactivated by an admin if needed."
        ),
        tags=["Auth"],
    )
    def post(self, request):
        """
        Deactivate the current user's account.

        This is a soft-delete that sets is_active=False.
        The account can be reactivated by an admin.

        Request body (optional):
            {
                "reason": "User requested account deletion"
            }

        Returns:
            Success message
        """
        reason = request.data.get("reason", "")
        AuthService.deactivate_user(request.user, reason)

        return Response({"detail": "Account deactivated successfully"})


# =============================================================================
# Biometric Authentication Views
# =============================================================================


class BiometricEnrollView(APIView):
    """
    API view for biometric enrollment.

    POST: Enroll biometric authentication for the current user

    URL: /api/v1/auth/biometric/enroll/

    Requires authentication. The user must be logged in before enrolling
    biometric authentication.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Enroll biometric authentication",
        description=(
            "Store the user's EC public key for Face ID/Touch ID authentication. "
            "The public key should be generated in the iOS Secure Enclave with biometric "
            "protection. Overwrites any existing key (single device support)."
        ),
        tags=["Auth - Biometric"],
        request=BiometricEnrollSerializer,
        responses={
            200: OpenApiResponse(
                response=BiometricEnrollResponseSerializer,
                description="Successfully enrolled biometric authentication",
                examples=[
                    OpenApiExample(
                        "Success",
                        value={"enrolled_at": "2025-01-15T10:30:00Z"},
                    ),
                ],
            ),
            400: OpenApiResponse(
                description="Invalid public key format",
                examples=[
                    OpenApiExample(
                        "Invalid Key",
                        value={
                            "detail": "Invalid EC public key format. Must be P-256 curve."
                        },
                    ),
                ],
            ),
        },
        examples=[
            OpenApiExample(
                "Enroll Request",
                value={"public_key": "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE..."},
                request_only=True,
            ),
        ],
    )
    def post(self, request):
        """
        Enroll biometric authentication.

        Stores the user's public key (generated in iOS Secure Enclave with
        biometric protection). Overwrites any existing key (single device).

        Request body:
            {
                "public_key": "<base64_der_ec_public_key>"
            }

        Returns:
            {
                "enrolled_at": "2025-01-15T10:30:00Z"
            }
        """
        serializer = BiometricEnrollSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            BiometricService.enroll(
                user=request.user,
                public_key=serializer.validated_data["public_key"],
            )
        except ValueError as e:
            return Response(
                {"detail": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from django.utils import timezone

        return Response({"enrolled_at": timezone.now()})


class BiometricChallengeView(APIView):
    """
    API view for requesting a biometric authentication challenge.

    POST: Generate a challenge nonce for biometric authentication

    URL: /api/v1/auth/biometric/challenge/

    No authentication required (this is part of the login flow).
    """

    permission_classes = []

    @extend_schema(
        summary="Request biometric challenge",
        description=(
            "Generate a cryptographically secure challenge nonce for biometric authentication. "
            "The client must sign this challenge with the private key protected by Face ID/Touch ID. "
            "Challenges expire after 5 minutes (300 seconds)."
        ),
        tags=["Auth - Biometric"],
        request=BiometricChallengeRequestSerializer,
        responses={
            200: OpenApiResponse(
                response=BiometricChallengeResponseSerializer,
                description="Challenge generated successfully",
                examples=[
                    OpenApiExample(
                        "Success",
                        value={
                            "challenge": "dGhpcyBpcyBhIHRlc3QgY2hhbGxlbmdl...",
                            "expires_in": 300,
                        },
                    ),
                ],
            ),
            404: OpenApiResponse(
                description="User not found or biometric not enabled",
                examples=[
                    OpenApiExample(
                        "Not Enabled",
                        value={
                            "error": "biometric_not_enabled",
                            "detail": "Biometric authentication is not enabled for this user",
                        },
                    ),
                ],
            ),
        },
        examples=[
            OpenApiExample(
                "Challenge Request",
                value={"email": "user@example.com"},
                request_only=True,
            ),
        ],
    )
    def post(self, request):
        """
        Generate a biometric authentication challenge.

        Creates a cryptographically secure nonce that must be signed by
        the user's private key (protected by Face ID/Touch ID).

        Request body:
            {
                "email": "user@example.com"
            }

        Returns:
            {
                "challenge": "<base64_nonce>",
                "expires_in": 300
            }

        Errors:
            404: User not found or biometric not enabled
        """
        serializer = BiometricChallengeRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            challenge_data = BiometricService.create_challenge(
                email=serializer.validated_data["email"],
            )
        except ValueError as e:
            return Response(
                {"error": "biometric_not_enabled", "detail": str(e)},
                status=status.HTTP_404_NOT_FOUND,
            )

        response_serializer = BiometricChallengeResponseSerializer(challenge_data)
        return Response(response_serializer.data)


class BiometricAuthenticateView(APIView):
    """
    API view for biometric authentication.

    POST: Authenticate with biometric signature

    URL: /api/v1/auth/biometric/authenticate/

    No authentication required (this IS the login).
    """

    permission_classes = []

    @extend_schema(
        summary="Authenticate with biometrics",
        description=(
            "Verify the ECDSA signature of the challenge and issue JWT tokens on success. "
            "The signature must be created using the private key protected by Face ID/Touch ID. "
            "Challenges are single-use and expire after 5 minutes."
        ),
        tags=["Auth - Biometric"],
        request=BiometricAuthenticateSerializer,
        responses={
            200: OpenApiResponse(
                description="Authentication successful",
                examples=[
                    OpenApiExample(
                        "Success",
                        value={
                            "access": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                            "refresh": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                            "user": {
                                "id": 1,
                                "email": "user@example.com",
                                "full_name": "John Doe",
                                "username": "johndoe",
                                "email_verified": True,
                                "profile_completed": True,
                                "linked_providers": ["email", "google"],
                                "date_joined": "2025-01-01T00:00:00Z",
                            },
                        },
                    ),
                ],
            ),
            401: OpenApiResponse(
                description="Invalid signature, expired challenge, or user not found",
                examples=[
                    OpenApiExample(
                        "Invalid Signature",
                        value={
                            "error": "invalid_signature",
                            "detail": "Signature verification failed",
                        },
                    ),
                    OpenApiExample(
                        "Expired Challenge",
                        value={
                            "error": "invalid_signature",
                            "detail": "Challenge expired or not found",
                        },
                    ),
                ],
            ),
        },
        examples=[
            OpenApiExample(
                "Authenticate Request",
                value={
                    "email": "user@example.com",
                    "challenge": "dGhpcyBpcyBhIHRlc3QgY2hhbGxlbmdl...",
                    "signature": "MEUCIQD...",
                },
                request_only=True,
            ),
        ],
    )
    def post(self, request):
        """
        Authenticate using biometric signature.

        Verifies the signature and issues JWT tokens on success.

        Request body:
            {
                "email": "user@example.com",
                "challenge": "<base64_nonce>",
                "signature": "<base64_ecdsa_signature>"
            }

        Returns:
            {
                "access": "<jwt_access_token>",
                "refresh": "<jwt_refresh_token>",
                "user": { ... }
            }

        Errors:
            401: Invalid signature, expired challenge, or user not found
        """
        serializer = BiometricAuthenticateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            user = BiometricService.authenticate(
                email=serializer.validated_data["email"],
                challenge=serializer.validated_data["challenge"],
                signature=serializer.validated_data["signature"],
            )
        except ValueError as e:
            return Response(
                {"error": "invalid_signature", "detail": str(e)},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # Generate JWT tokens (same as dj-rest-auth login)
        from rest_framework_simplejwt.tokens import RefreshToken

        refresh = RefreshToken.for_user(user)

        # Update last login
        from django.contrib.auth import user_logged_in

        user_logged_in.send(sender=user.__class__, request=request, user=user)

        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "user": UserSerializer(user).data,
            }
        )


class BiometricDisableView(APIView):
    """
    API view for disabling biometric authentication.

    DELETE: Disable biometric authentication for the current user

    URL: /api/v1/auth/biometric/

    Requires authentication.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Disable biometric authentication",
        description=(
            "Clear the stored public key, disabling Face ID/Touch ID authentication. "
            "The user will need to re-enroll to use biometrics again."
        ),
        tags=["Auth - Biometric"],
        responses={
            200: OpenApiResponse(
                response=BiometricDisableResponseSerializer,
                description="Biometric authentication disabled",
                examples=[
                    OpenApiExample(
                        "Success",
                        value={"disabled": True},
                    ),
                ],
            ),
        },
    )
    def delete(self, request):
        """
        Disable biometric authentication.

        Clears the stored public key, requiring re-enrollment to use
        biometrics again.

        Returns:
            {
                "disabled": true
            }
        """
        BiometricService.disable(request.user)
        return Response({"disabled": True})


class BiometricStatusView(APIView):
    """
    API view for checking biometric authentication status.

    GET: Check if biometric authentication is enabled for a user

    URL: /api/v1/auth/biometric/status/?email=user@example.com

    No authentication required. Used by the login screen to determine
    whether to show the Face ID/Touch ID option.

    Security:
        Returns false for non-existent users (no user enumeration).
    """

    permission_classes = []

    @extend_schema(
        summary="Check biometric status",
        description=(
            "Check if biometric authentication is enabled for a user. "
            "Used by the login screen to determine whether to show the Face ID/Touch ID option. "
            "Returns false for non-existent users to prevent user enumeration."
        ),
        tags=["Auth - Biometric"],
        parameters=[
            OpenApiParameter(
                name="email",
                location=OpenApiParameter.QUERY,
                required=True,
                description="User's email address",
                type=str,
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=BiometricStatusSerializer,
                description="Biometric status retrieved",
                examples=[
                    OpenApiExample(
                        "Enabled",
                        value={"biometric_enabled": True},
                    ),
                    OpenApiExample(
                        "Disabled",
                        value={"biometric_enabled": False},
                    ),
                ],
            ),
            400: OpenApiResponse(
                description="Missing email parameter",
                examples=[
                    OpenApiExample(
                        "Missing Email",
                        value={"detail": "Email parameter is required"},
                    ),
                ],
            ),
        },
    )
    def get(self, request):
        """
        Check if biometric authentication is enabled.

        Query parameters:
            email: User's email address

        Returns:
            {
                "biometric_enabled": true/false
            }
        """
        email = request.query_params.get("email", "")
        if not email:
            return Response(
                {"detail": "Email parameter is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        enabled = BiometricService.check_status(email)
        serializer = BiometricStatusSerializer({"biometric_enabled": enabled})
        return Response(serializer.data)
