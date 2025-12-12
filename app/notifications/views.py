"""
Views for notification API.

This module provides the ViewSet for notification endpoints.

ViewSets:
    NotificationViewSet: ReadOnlyModelViewSet with custom actions for read status

Endpoints:
    GET /api/v1/notifications/ - List user's notifications (paginated, filtered)
    GET /api/v1/notifications/{id}/ - Get notification detail
    GET /api/v1/notifications/unread-count/ - Get unread count
    POST /api/v1/notifications/{id}/read/ - Mark single notification as read
    POST /api/v1/notifications/read-all/ - Mark all notifications as read

Usage:
    # In urls.py
    from rest_framework.routers import DefaultRouter
    from notifications.views import NotificationViewSet

    router = DefaultRouter()
    router.register(r"", NotificationViewSet, basename="notification")
"""

from __future__ import annotations

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from notifications.models import Notification
from notifications.serializers import (
    MarkAllReadResponseSerializer,
    NotificationSerializer,
    UnreadCountSerializer,
)
from notifications.services import NotificationService


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
