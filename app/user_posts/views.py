from django.shortcuts import render
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from user_posts.serializers import UserPostSerializer
from user_posts.models import UserPost
# Create your views here.

class UserPostViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = UserPostSerializer 
    def get_queryset(self):
        return UserPost.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def perform_destory(self, instance):
        instance.soft_delete()

# class UserPosts(APIView):
#     permission_classes: [IsAuthenticated]
#     pass