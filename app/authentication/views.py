"""
Authentication views.

This module provides API views for:
- Profile management (CRUD operations)
- Email verification
- Custom auth endpoints not covered by dj-rest-auth

Related files:
    - serializers.py: Request/response serialization
    - services.py: Business logic
    - urls.py: URL routing

Note:
    Most authentication endpoints are handled by dj-rest-auth:
    - Login: /api/v1/auth/login/
    - Logout: /api/v1/auth/logout/
    - Register: /api/v1/auth/registration/
    - Password reset: /api/v1/auth/password/reset/
    - Password change: /api/v1/auth/password/change/
    - Current user: /api/v1/auth/user/
"""

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

# TODO: Import serializers and services when implemented
# from authentication.serializers import ProfileSerializer, ProfileUpdateSerializer
# from authentication.services import AuthService


class ProfileView(APIView):
    """
    API view for user profile operations.

    GET: Retrieve current user's profile
    PUT/PATCH: Update current user's profile

    URL: /api/v1/auth/profile/
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        """
        Retrieve the current user's profile.

        Returns:
            Profile data including avatar, display_name, timezone, preferences
        """
        # TODO: Implement profile retrieval
        # from authentication.services import AuthService
        # from authentication.serializers import ProfileSerializer
        #
        # profile = AuthService.get_or_create_profile(request.user)
        # serializer = ProfileSerializer(profile)
        # return Response(serializer.data)
        return Response(
            {"detail": "Profile retrieval not yet implemented"},
            status=status.HTTP_501_NOT_IMPLEMENTED,
        )

    def put(self, request):
        """
        Update the current user's profile (full update).

        Request body:
            {
                "avatar_url": "https://example.com/avatar.jpg",
                "display_name": "John Doe",
                "timezone": "America/New_York",
                "preferences": {"theme": "dark"}
            }

        Returns:
            Updated profile data
        """
        return self._update_profile(request, partial=False)

    def patch(self, request):
        """
        Partially update the current user's profile.

        Request body:
            Any subset of profile fields

        Returns:
            Updated profile data
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
        # TODO: Implement profile update
        # from authentication.services import AuthService
        # from authentication.serializers import ProfileUpdateSerializer, ProfileSerializer
        #
        # profile = AuthService.get_or_create_profile(request.user)
        # serializer = ProfileUpdateSerializer(
        #     profile,
        #     data=request.data,
        #     partial=partial
        # )
        # serializer.is_valid(raise_exception=True)
        # updated_profile = serializer.save()
        #
        # return Response(ProfileSerializer(updated_profile).data)
        return Response(
            {"detail": "Profile update not yet implemented"},
            status=status.HTTP_501_NOT_IMPLEMENTED,
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
        # TODO: Implement email verification
        # from authentication.services import AuthService
        #
        # token = request.data.get("token")
        # if not token:
        #     return Response(
        #         {"detail": "Token is required"},
        #         status=status.HTTP_400_BAD_REQUEST
        #     )
        #
        # success, message = AuthService.verify_email(token)
        #
        # if success:
        #     return Response({"detail": message})
        # else:
        #     return Response(
        #         {"detail": message},
        #         status=status.HTTP_400_BAD_REQUEST
        #     )
        return Response(
            {"detail": "Email verification not yet implemented"},
            status=status.HTTP_501_NOT_IMPLEMENTED,
        )


class ResendVerificationView(APIView):
    """
    API view to resend verification email.

    POST: Resend verification email to current user

    URL: /api/v1/auth/resend-verification/
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        """
        Resend verification email to current user.

        Returns:
            Success message
        """
        # TODO: Implement resend verification
        # from authentication.services import AuthService
        #
        # if request.user.email_verified:
        #     return Response(
        #         {"detail": "Email is already verified"},
        #         status=status.HTTP_400_BAD_REQUEST
        #     )
        #
        # AuthService.send_verification_email(request.user)
        # return Response({"detail": "Verification email sent"})
        return Response(
            {"detail": "Resend verification not yet implemented"},
            status=status.HTTP_501_NOT_IMPLEMENTED,
        )


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
        # TODO: Implement account deactivation
        # from authentication.services import AuthService
        #
        # reason = request.data.get("reason", "")
        # AuthService.deactivate_user(request.user, reason)
        #
        # return Response({"detail": "Account deactivated successfully"})
        return Response(
            {"detail": "Account deactivation not yet implemented"},
            status=status.HTTP_501_NOT_IMPLEMENTED,
        )
