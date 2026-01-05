from rest_framework.routers import DefaultRouter
from user_posts.views import UserPostViewSet

router = DefaultRouter()
router.register("", UserPostViewSet, basename="user-posts")

urlpatterns = router.urls
