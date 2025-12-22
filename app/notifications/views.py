"""
Views for notification API.

This module provides the ViewSets for notification and preference endpoints.

ViewSets:
    NotificationViewSet: ReadOnlyModelViewSet with custom actions for read status
    PreferenceViewSet: ViewSet for managing user notification preferences
    NotificationTypeViewSet: ReadOnlyModelViewSet for listing notification types

Endpoints:
    Notifications:
        GET /api/v1/notifications/ - List user's notifications (paginated, filtered)
        GET /api/v1/notifications/{id}/ - Get notification detail
        GET /api/v1/notifications/unread-count/ - Get unread count
        POST /api/v1/notifications/{id}/read/ - Mark single notification as read
        POST /api/v1/notifications/read-all/ - Mark all notifications as read

    Preferences:
        GET /api/v1/notifications/preferences/ - List all preferences
        PATCH /api/v1/notifications/preferences/global/ - Update global mute
        PATCH /api/v1/notifications/preferences/category/ - Update category preference
        PATCH /api/v1/notifications/preferences/type/ - Update type preference
        POST /api/v1/notifications/preferences/bulk/ - Bulk update type preferences
        POST /api/v1/notifications/preferences/reset/ - Reset to defaults

    Types:
        GET /api/v1/notifications/types/ - List available notification types

Usage:
    # In urls.py
    from rest_framework.routers import DefaultRouter
    from notifications.views import (
        NotificationViewSet,
        PreferenceViewSet,
        NotificationTypeViewSet,
    )

    router = DefaultRouter()
    router.register(r"", NotificationViewSet, basename="notification")
    router.register(r"preferences", PreferenceViewSet, basename="preference")
    router.register(r"types", NotificationTypeViewSet, basename="notification-type")
"""

from __future__ import annotations

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from drf_spectacular.utils import (
    extend_schema,
    extend_schema_view,
    OpenApiParameter,
    OpenApiResponse,
)

from notifications.models import Notification, NotificationType
from notifications.serializers import (
    BulkPreferenceSerializer,
    CategoryPreferenceSerializer,
    GlobalPreferenceSerializer,
    MarkAllReadResponseSerializer,
    NotificationSerializer,
    NotificationTypeSerializer,
    ResetPreferencesResponseSerializer,
    TypePreferenceSerializer,
    UnreadCountSerializer,
    UserPreferencesResponseSerializer,
)
from notifications.services import NotificationService, PreferenceService


@extend_schema_view(
    list=extend_schema(
        operation_id="list_notifications",
        summary="List notifications",
        description=(
            "Get paginated list of notifications for the authenticated user. "
            "Supports filtering by read status and notification type."
        ),
        parameters=[
            OpenApiParameter(
                name="is_read",
                type=bool,
                location=OpenApiParameter.QUERY,
                description="Filter by read status (true/false)",
                required=False,
            ),
            OpenApiParameter(
                name="type",
                type=str,
                location=OpenApiParameter.QUERY,
                description="Filter by notification type key",
                required=False,
            ),
        ],
        tags=["Notifications - Inbox"],
    ),
    retrieve=extend_schema(
        operation_id="get_notification",
        summary="Get notification",
        description="Get details of a specific notification.",
        tags=["Notifications - Inbox"],
    ),
)
class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for notification operations.

    Provides:
    - list: GET / - List user's notifications with filtering
    - retrieve: GET /{id}/ - Get notification detail
    - unread_count: GET /unread-count/ - Get badge count
    - read: POST /{id}/read/ - Mark single as read
    - read_all: POST /read-all/ - Mark all as read

    Filtering:
    - ?is_read=true/false - Filter by read status
    - ?type=key - Filter by notification type key

    Permissions:
    - All endpoints require authentication
    - Users can only access their own notifications
    """

    permission_classes = [IsAuthenticated]
    serializer_class = NotificationSerializer

    def get_queryset(self):
        """
        Get queryset filtered to user's notifications.

        Supports query parameters:
        - is_read: "true" or "false" to filter by read status
        - type: notification type key to filter by type

        Returns:
            QuerySet of Notification objects for current user
        """
        user = self.request.user
        queryset = Notification.objects.filter(recipient=user).select_related(
            "notification_type", "actor"
        )

        # Filter by is_read
        is_read = self.request.query_params.get("is_read")
        if is_read is not None:
            queryset = queryset.filter(is_read=is_read.lower() == "true")

        # Filter by notification type
        type_key = self.request.query_params.get("type")
        if type_key:
            queryset = queryset.filter(notification_type__key=type_key)

        return queryset

    @extend_schema(
        operation_id="get_unread_notification_count",
        summary="Get unread notification count",
        description="Get the count of unread notifications for badge display.",
        responses={200: UnreadCountSerializer},
        tags=["Notifications - Inbox"],
    )
    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        """
        Get count of unread notifications.

        Returns:
            {"unread_count": <int>}
        """
        count = Notification.objects.filter(
            recipient=request.user,
            is_read=False,
        ).count()

        serializer = UnreadCountSerializer({"unread_count": count})
        return Response(serializer.data)

    @extend_schema(
        operation_id="mark_notification_read",
        summary="Mark notification as read",
        description=(
            "Mark a single notification as read. "
            "This operation is idempotent - already-read notifications return success."
        ),
        responses={
            200: NotificationSerializer,
            400: OpenApiResponse(description="Failed to mark notification as read"),
            404: OpenApiResponse(description="Notification not found"),
        },
        tags=["Notifications - Inbox"],
    )
    @action(detail=True, methods=["post"])
    def read(self, request, pk=None):
        """
        Mark single notification as read.

        Idempotent - already-read notifications return success.
        Returns 404 if notification doesn't exist or belongs to another user.

        Returns:
            Serialized notification data
        """
        try:
            notification = Notification.objects.get(
                pk=pk,
                recipient=request.user,
            )
        except Notification.DoesNotExist:
            return Response(
                {"detail": "Not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        result = NotificationService.mark_as_read(notification, request.user)

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = self.get_serializer(result.data)
        return Response(serializer.data)

    @extend_schema(
        operation_id="mark_all_notifications_read",
        summary="Mark all notifications as read",
        description="Mark all unread notifications for the authenticated user as read.",
        responses={200: MarkAllReadResponseSerializer},
        tags=["Notifications - Inbox"],
    )
    @action(detail=False, methods=["post"], url_path="read-all")
    def read_all(self, request):
        """
        Mark all user's notifications as read.

        Returns count of notifications marked.

        Returns:
            {"marked_count": <int>}
        """
        result = NotificationService.mark_all_as_read(request.user)

        serializer = MarkAllReadResponseSerializer({"marked_count": result.data})
        return Response(serializer.data)


@extend_schema_view(
    list=extend_schema(
        operation_id="list_notification_preferences",
        summary="List notification preferences",
        description="Get all notification preferences for the current user.",
        responses={200: UserPreferencesResponseSerializer},
        tags=["Notifications - Preferences"],
    ),
)
class PreferenceViewSet(viewsets.ViewSet):
    """
    ViewSet for managing user notification preferences.

    Provides:
    - list: GET / - Get all preferences
    - global_preference: PATCH /global/ - Update global mute setting
    - category_preference: PATCH /category/ - Update category preference
    - type_preference: PATCH /type/ - Update type preference
    - bulk_update: POST /bulk/ - Bulk update type preferences
    - reset: POST /reset/ - Reset all preferences to defaults

    Permissions:
    - All endpoints require authentication
    - Users can only manage their own preferences
    """

    permission_classes = [IsAuthenticated]

    def list(self, request):
        """
        Get all notification preferences for the current user.

        Returns:
            {
                "global": {"all_disabled": bool},
                "categories": [{"category": str, "disabled": bool}, ...],
                "types": [{"type_key": str, "disabled": bool, ...}, ...]
            }
        """
        result = PreferenceService.get_user_preferences(request.user)
        serializer = UserPreferencesResponseSerializer(result.data)
        return Response(serializer.data)

    @extend_schema(
        operation_id="update_global_notification_preference",
        summary="Update global notification preference",
        description="Enable or disable all notifications globally.",
        request=GlobalPreferenceSerializer,
        responses={200: GlobalPreferenceSerializer},
        tags=["Notifications - Preferences"],
    )
    @action(detail=False, methods=["patch"], url_path="global")
    def global_preference(self, request):
        """
        Update global mute setting.

        When all_disabled is True, no notifications will be sent.
        """
        serializer = GlobalPreferenceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = PreferenceService.set_global_preference(
            user=request.user,
            all_disabled=serializer.validated_data["all_disabled"],
        )

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {"all_disabled": result.data.all_disabled},
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        operation_id="update_category_notification_preference",
        summary="Update category notification preference",
        description="Enable or disable notifications for a specific category.",
        request=CategoryPreferenceSerializer,
        responses={200: CategoryPreferenceSerializer},
        tags=["Notifications - Preferences"],
    )
    @action(detail=False, methods=["patch"], url_path="category")
    def category_preference(self, request):
        """
        Update category preference.

        When disabled is True, no notifications of this category will be sent.
        """
        serializer = CategoryPreferenceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = PreferenceService.set_category_preference(
            user=request.user,
            category=serializer.validated_data["category"],
            disabled=serializer.validated_data["disabled"],
        )

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "category": result.data.category,
                "disabled": result.data.disabled,
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        operation_id="update_type_notification_preference",
        summary="Update type notification preference",
        description="Update notification preferences for a specific notification type.",
        request=TypePreferenceSerializer,
        responses={200: TypePreferenceSerializer},
        tags=["Notifications - Preferences"],
    )
    @action(detail=False, methods=["patch"], url_path="type")
    def type_preference(self, request):
        """
        Update type-level preference.

        Allows enabling/disabling the entire type or individual channels.
        Channel values of null mean "inherit from type defaults".
        """
        serializer = TypePreferenceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        validated = serializer.validated_data
        result = PreferenceService.set_type_preference(
            user=request.user,
            type_key=validated["type_key"],
            disabled=validated.get("disabled"),
            push_enabled=validated.get("push_enabled"),
            email_enabled=validated.get("email_enabled"),
            websocket_enabled=validated.get("websocket_enabled"),
        )

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        pref = result.data
        return Response(
            {
                "type_key": pref.notification_type.key,
                "disabled": pref.disabled,
                "push_enabled": pref.push_enabled,
                "email_enabled": pref.email_enabled,
                "websocket_enabled": pref.websocket_enabled,
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        operation_id="bulk_update_notification_preferences",
        summary="Bulk update type preferences",
        description="Update multiple notification type preferences at once.",
        request=BulkPreferenceSerializer,
        responses={
            200: BulkPreferenceSerializer,
            207: OpenApiResponse(description="Partial success with some errors"),
        },
        tags=["Notifications - Preferences"],
    )
    @action(detail=False, methods=["post"], url_path="bulk")
    def bulk_update(self, request):
        """
        Bulk update multiple type preferences.

        Accepts a list of type preferences to update.
        """
        serializer = BulkPreferenceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        results = []
        errors = []

        for pref_data in serializer.validated_data["preferences"]:
            result = PreferenceService.set_type_preference(
                user=request.user,
                type_key=pref_data["type_key"],
                disabled=pref_data.get("disabled"),
                push_enabled=pref_data.get("push_enabled"),
                email_enabled=pref_data.get("email_enabled"),
                websocket_enabled=pref_data.get("websocket_enabled"),
            )

            if result.success:
                pref = result.data
                results.append(
                    {
                        "type_key": pref.notification_type.key,
                        "disabled": pref.disabled,
                        "push_enabled": pref.push_enabled,
                        "email_enabled": pref.email_enabled,
                        "websocket_enabled": pref.websocket_enabled,
                    }
                )
            else:
                errors.append(
                    {
                        "type_key": pref_data["type_key"],
                        "error": result.error,
                    }
                )

        response_data = {"preferences": results}
        if errors:
            response_data["errors"] = errors
            return Response(response_data, status=status.HTTP_207_MULTI_STATUS)

        return Response(response_data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="reset_notification_preferences",
        summary="Reset all preferences",
        description="Reset all notification preferences to their default values.",
        responses={200: ResetPreferencesResponseSerializer},
        tags=["Notifications - Preferences"],
    )
    @action(detail=False, methods=["post"], url_path="reset")
    def reset(self, request):
        """
        Reset all preferences to defaults.

        Deletes all preference records, returning to default behavior.
        """
        result = PreferenceService.reset_preferences(request.user)

        serializer = ResetPreferencesResponseSerializer({"deleted_count": result.data})
        return Response(serializer.data, status=status.HTTP_200_OK)


@extend_schema_view(
    list=extend_schema(
        operation_id="list_notification_types",
        summary="List notification types",
        description="Get all available notification types.",
        tags=["Notifications - Types"],
    ),
    retrieve=extend_schema(
        operation_id="get_notification_type",
        summary="Get notification type",
        description="Get details of a specific notification type.",
        tags=["Notifications - Types"],
    ),
)
class NotificationTypeViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for listing notification types.

    Read-only viewset that lists all active notification types,
    allowing clients to discover what notifications are available
    and configure preferences.

    Permissions:
    - All endpoints require authentication
    """

    permission_classes = [IsAuthenticated]
    serializer_class = NotificationTypeSerializer
    lookup_field = "key"

    def get_queryset(self):
        """
        Get active notification types.

        Returns:
            QuerySet of active NotificationType objects
        """
        return NotificationType.objects.filter(is_active=True).order_by(
            "category", "name"
        )
