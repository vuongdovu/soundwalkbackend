"""
URL configuration for media app.

Routes:
    POST /upload/ - Upload a new media file
"""

from django.urls import path

from media.views import MediaUploadView

app_name = "media"

urlpatterns = [
    path("upload/", MediaUploadView.as_view(), name="upload"),
]
