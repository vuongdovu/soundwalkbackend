"""
User posts service layer.

This module provides business logic for user post clustering.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import h3
from django.db.models import Count

from core.services import BaseService, ServiceResult

from user_posts.models import UserPost

if TYPE_CHECKING:
    from django.db.models import QuerySet


class UserPostClusterService(BaseService):
    """
    Service for clustering user posts by H3 index.
    """

    H3_FIELD_BY_RESOLUTION = {
        4: "h3_r4",
        6: "h3_r6",
        9: "h3_r9",
    }

    @classmethod
    def build_clusters(
        cls,
        queryset: "QuerySet[UserPost]",
        resolution: int | str,
    ) -> ServiceResult[dict[str, object]]:
        """
        Build clusters for a queryset of posts at the given H3 resolution.

        Returns a dict with resolution and clusters:
            {
                "resolution": 6,
                "clusters": [
                    {"h3_index": "...", "lat": 0.0, "lng": 0.0, "count": 10},
                    ...
                ]
            }
        """
        try:
            resolution = int(resolution)
        except (TypeError, ValueError):
            return ServiceResult.failure(
                "resolution must be an integer",
                error_code="INVALID_RESOLUTION",
            )

        h3_field = cls.H3_FIELD_BY_RESOLUTION.get(resolution)
        if not h3_field:
            return ServiceResult.failure(
                "resolution must be one of 4, 6, or 9",
                error_code="INVALID_RESOLUTION",
            )

        rows = (
            queryset.exclude(**{f"{h3_field}__isnull": True})
            .values(h3_field)
            .annotate(count=Count("id"))
            .order_by()
        )

        clusters: list[dict[str, object]] = []
        for row in rows:
            h3_index = row.get(h3_field)
            if not h3_index:
                continue
            lat, lng = h3.cell_to_latlng(h3_index)
            clusters.append(
                {
                    "h3_index": h3_index,
                    "lat": lat,
                    "lng": lng,
                    "count": row["count"],
                }
            )

        return ServiceResult.success(
            {
                "resolution": resolution,
                "clusters": clusters,
            }
        )
