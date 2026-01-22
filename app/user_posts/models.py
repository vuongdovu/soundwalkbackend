from email.policy import default
from random import choices
from django.db import models
from core.model_mixins import UUIDPrimaryKeyMixin, SoftDeleteMixin
from core.models import BaseModel
from django.conf import settings
from core.managers import SoftDeleteManager

# Create your models here.

class UserPost(BaseModel, UUIDPrimaryKeyMixin, SoftDeleteMixin):
    PUBLIC = "PU"
    FRIENDS = "FR"
    PRIVATE = "PR"
    POST_VISIBILITY = {
        "PU": "Public",
        "FR": "Friends",
        "PR": "Private", 
    }

    objects = SoftDeleteManager()
    all_objects = models.Manager()

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="UserPost",
        help_text="User this post belongs to",
    )
    photo = models.ImageField()
    visibility = models.CharField(max_length=2, choices=POST_VISIBILITY, default=PUBLIC)
    taken_at = models.DateTimeField()
    is_draft = models.BooleanField(default=False)
    # song = models.ForeignKey(Song, on_delete=models.PROTECT, related_name="posts")
    lat = models.FloatField()
    lng = models.FloatField()
    accuracy_m = models.FloatField(null=True, blank=True)
    h3_r4 = models.CharField(max_length=15, null=True, blank=True)
    h3_r6 = models.CharField(max_length=15, null=True, blank=True)
    h3_r9 = models.CharField(max_length=15, null=True, blank=True)

    # place = models.ForeignKey(Place, null=True, blank=True, on_delete=models.SET_NULL, related_name="posts")

    # def __str__(self):
    #     return f"User: {self.user.username}, Visibility: {self.visibility}, h3_r4: {self.h3_r4}"

class UserPostLike(BaseModel, SoftDeleteMixin):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    post = models.ForeignKey(UserPost, on_delete=models.CASCADE)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "post"], name="uniq_user_post_like")
        ]
