from rest_framework import serializers

from user_posts.models import UserPost, UserPostLike


class UserPostSerializer(serializers.ModelSerializer):
    user = serializers.HiddenField(default=serializers.CurrentUserDefault())

    class Meta:
        model = UserPost
        fields = [
            "id",
            "user",
            "photo",
            "visibility",
            "taken_at",
            "is_draft",
            # "song",
            "lat",
            "lng",
            "accuracy_m",
            # "place",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate(self, attrs):
        lat = attrs.get("lat")
        lng = attrs.get("lng")

        if lat is not None and not (-90 <= lat <= 90):
            raise serializers.ValidationError({"lat": "Must be between -90 and 90."})
        if lng is not None and not (-180 <= lng <= 180):
            raise serializers.ValidationError({"lng": "Must be between -180 and 180."})

        return attrs

class UserPostLikeSerializer(serializers.ModelSerializer):
    user = serializers.HiddenField(default=serializers.CurrentUserDefault())

    class Meta:
        model = UserPostLike
        fields = ["id", "user", "post", "created_at"]
        read_only_fields = ["id", "created_at"]

    def validate(self, attrs):
        user = attrs["user"]
        post = attrs["post"]
        if UserPostLike.objects.filter(user=user, post=post).exists():
            raise serializers.ValidationError("Already liked.")
        return attrs