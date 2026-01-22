from gc import get_threshold
from django.shortcuts import render
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.decorators import action
from django.db.models import Count

from drf_spectacular.utils import (
    extend_schema,
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
)

from user_posts.serializers import UserPostSerializer
from user_posts.filters import UserPostFilter
from user_posts.models import UserPost
from user_posts.services import UserPostClusterService

import h3

from django_filters.rest_framework import DjangoFilterBackend

# Create your views here.

class UserPostViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = UserPostSerializer 
    filter_backends = [DjangoFilterBackend]
    filterset_class = UserPostFilter

    def list(self, request, *args, **kwargs):
        resolution_param = request.query_params.get("resolution", "0")
        try:
            resolution = int(resolution_param)
        except (TypeError, ValueError):
            return Response({"detail": "resolution must be an integer"}, status=400)

        if resolution <= 0:
            return super().list(request, *args, **kwargs)

        queryset = self.filter_queryset(self.get_queryset())
        result = UserPostClusterService.build_clusters(queryset, resolution)
        if not result.success:
            return Response(result.to_response(), status=400)
        return Response(result.data)

    # @extend_schema(
    #     summary="returns posts in clusters",
    #     description=(
    #         "Lists the posts withing a cluster. Use the h3_index from a cluster pin and then user the same params as list."
    #     ),
    #     tags=[""],
    #     request=UserPostSerializer,
    #     responses={200: UserPostSerializer},
    # )
    @action(detail=False, methods=["get"], url_path="cluster")
    def cluster(self, request):
        resolution_param = request.query_params.get("resolution")
        h3_index = request.query_params.get("h3_index")

        if not resolution_param or not h3_index:
            return Response(
                {"detail": "resolution and h3_index are required"},
                status=400,
            )

        try:
            resolution = int(resolution_param)
        except (TypeError, ValueError):
            return Response({"detail": "resolution must be an integer"}, status=400)

        field_map = {4: "h3_r4", 6: "h3_r6", 9: "h3_r9"}
        h3_field = field_map.get(resolution)
        if not h3_field:
            return Response(
                {"detail": "resolution must be 4, 6, or 9"},
                status=400,
            )

        queryset = self.filter_queryset(self.get_queryset()).filter(
            **{h3_field: h3_index}
        )
        print("query set: ", self.get_queryset())
        print("user post objects: ", UserPost.objects.all())

        print(self.get_queryset().filter(
            **{h3_field: h3_index}))

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    # def get_queryset(self):
    #     return UserPost.objects.filter(user=self.request.user)
    
    def get_queryset(self):
        return UserPost.objects.filter()
    
    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def perform_destory(self, instance):
        instance.soft_delete()



# zoom reaches a threshhold
# send new resolution to UserPost
# util will group them into a cluster and display cluster on map
