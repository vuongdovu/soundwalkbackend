"""
Serializer mixins providing reusable functionality for DRF serializers.

This module contains mixin classes that can be combined with
DRF serializers to add specific functionality.

Available Mixins:
    TimestampMixin: Auto-include timestamp fields in serializer output

Usage:
    from core.serializer_mixins import TimestampMixin

    class MySerializer(TimestampMixin, serializers.ModelSerializer):
        class Meta:
            model = MyModel
            fields = ["name"]  # created_at and updated_at auto-included

Note:
    - These are generic infrastructure patterns, not domain-specific
    - For viewset mixins, see core.viewset_mixins
    - For model mixins, see core.model_mixins
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any


class TimestampMixin:
    """
    Add timestamp fields to serializer output.

    Automatically includes created_at and updated_at as read-only fields
    for any serializer that uses this mixin, if the model has these fields.

    Works with models that inherit from core.models.BaseModel.

    Usage:
        from core.serializer_mixins import TimestampMixin

        class ArticleSerializer(TimestampMixin, serializers.ModelSerializer):
            class Meta:
                model = Article
                fields = ["title", "content"]
                # created_at and updated_at are automatically included

    Note:
        Only adds timestamp fields if the model has them.
        Fields are added as read-only (auto_now/auto_now_add).
    """

    def get_field_names(self, declared_fields: Any, info: Any) -> list[str]:
        """
        Add timestamp fields to the list of fields.

        Extends the parent's field list to include created_at and updated_at
        if the model has these fields and they're not already included.

        Args:
            declared_fields: Fields explicitly declared on the serializer
            info: Serializer metadata including model information

        Returns:
            List of field names including timestamps
        """
        fields = super().get_field_names(declared_fields, info)  # type: ignore[misc]
        # Ensure timestamp fields are included
        if hasattr(info.model, "created_at") and "created_at" not in fields:
            fields = list(fields) + ["created_at"]
        if hasattr(info.model, "updated_at") and "updated_at" not in fields:
            fields = list(fields) + ["updated_at"]
        return fields
