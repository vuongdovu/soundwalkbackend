"""
Authentication views.

This module provides API views for:
- Profile management (CRUD operations with conditional username validation)
- Email verification
- Social authentication (Google, Apple)
- Custom auth endpoints not covered by dj-rest-auth

Related files:
    - serializers.py: Request/response serialization
    - services.py: Business logic
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

    ProfileView handles both initial profile completion and subsequent updates.
    If profile.username is empty, username is required in the request.
"""

from allauth.socialaccount.providers.apple.views import AppleOAuth2Adapter
from allauth.socialaccount.providers.google.views import GoogleOAuth2Adapter
from dj_rest_auth.registration.views import SocialLoginView
from rest_framework import status
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from authentication.serializers import (
    ProfileSerializer,
    ProfileUpdateSerializer,
)
from authentication.services import AuthService


# =============================================================================
# Social Authentication Views
# =============================================================================


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
                {"detail": "Token is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        success, message = AuthService.verify_email(token)

        if success:
            return Response({"detail": message})
        else:
            return Response(
                {"detail": message},
                status=status.HTTP_400_BAD_REQUEST
            )


class ResendEmailView(APIView):
    """
    API view to resend verification email.

    POST: Resend verification email to current user

    URL: /api/v1/auth/resend-email/
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        """
        Resend verification email to current user.

        Returns:
            Success message
        """
        if request.user.email_verified:
            return Response(
                {"detail": "Email is already verified"},
                status=status.HTTP_400_BAD_REQUEST
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
