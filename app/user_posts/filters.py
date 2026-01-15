import django_filters as filters

from user_posts.models import UserPost


class UserPostFilter(filters.FilterSet):
    start_date = filters.IsoDateTimeFilter(field_name="taken_at", lookup_expr="gte")
    end_date = filters.IsoDateTimeFilter(field_name="taken_at", lookup_expr="lte")

    class Meta:
        model = UserPost
        fields = ["is_draft", "visibility", "start_date", "end_date"]
