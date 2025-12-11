"""
Custom validators for Django models and DRF serializers.

This module provides domain-agnostic validators for:
- File uploads (size, type)
- URLs (domain restrictions)
- JSON schema validation
- Security (XSS prevention)
- Format validation (slugs)

These validators are generic infrastructure - they have no knowledge
of domain concepts like users, subscriptions, or business logic.

Usage:
    from core.validators import validate_file_size, validate_no_html

    class MyModel(models.Model):
        avatar = models.FileField(validators=[validate_file_size(max_mb=5)])
        bio = models.TextField(validators=[validate_no_html])

Note:
    - For domain-aware validators (phone numbers), see toolkit.validators
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError

if TYPE_CHECKING:
    from django.core.files import File


def validate_file_size(max_mb: int = 10):
    """
    Validator factory for file size limits.

    Args:
        max_mb: Maximum file size in megabytes

    Returns:
        Validator function

    Usage:
        class MyModel(models.Model):
            file = models.FileField(validators=[validate_file_size(max_mb=5)])
    """

    def validator(file: File):
        max_bytes = max_mb * 1024 * 1024
        if file.size > max_bytes:
            raise ValidationError(
                f"File size must be less than {max_mb}MB. "
                f"Current size: {file.size / 1024 / 1024:.1f}MB"
            )

    return validator


def validate_file_extension(allowed_extensions: list[str]):
    """
    Validator factory for file extension limits.

    Args:
        allowed_extensions: List of allowed extensions (without dot)

    Returns:
        Validator function

    Usage:
        class MyModel(models.Model):
            image = models.FileField(
                validators=[validate_file_extension(["jpg", "png", "gif"])]
            )
    """

    def validator(file: File):
        import os

        ext = os.path.splitext(file.name)[1].lower().lstrip(".")
        if ext not in allowed_extensions:
            raise ValidationError(
                f"File extension '{ext}' is not allowed. "
                f"Allowed: {', '.join(allowed_extensions)}"
            )

    return validator


def validate_url_domain(allowed_domains: list[str]):
    """
    Validator factory for URL domain restrictions.

    Args:
        allowed_domains: List of allowed domains

    Returns:
        Validator function

    Usage:
        validate = validate_url_domain(["example.com", "cdn.example.com"])
        validate("https://example.com/image.png")  # OK
        validate("https://evil.com/image.png")  # Raises ValidationError
    """

    def validator(value: str):
        from urllib.parse import urlparse

        parsed = urlparse(value)
        domain = parsed.netloc.lower()

        # Remove port if present
        if ":" in domain:
            domain = domain.split(":")[0]

        if domain not in allowed_domains:
            raise ValidationError(
                f"URL domain '{domain}' is not allowed. "
                f"Allowed: {', '.join(allowed_domains)}"
            )

    return validator


def validate_json_schema(schema: dict):
    """
    Validator factory for JSON schema validation.

    Requires jsonschema package.

    Args:
        schema: JSON Schema dict

    Returns:
        Validator function

    Usage:
        preference_schema = {
            "type": "object",
            "properties": {
                "theme": {"type": "string", "enum": ["light", "dark"]},
                "language": {"type": "string"}
            }
        }
        validate = validate_json_schema(preference_schema)
    """

    def validator(value):
        try:
            import jsonschema
        except ImportError:
            # Skip validation if jsonschema not installed
            return

        try:
            jsonschema.validate(instance=value, schema=schema)
        except jsonschema.ValidationError as e:
            raise ValidationError(f"Invalid JSON structure: {e.message}")

    return validator


def validate_no_html(value: str):
    """
    Validate that string contains no HTML tags.

    Useful for preventing XSS in text fields.

    Args:
        value: String to validate

    Raises:
        ValidationError: If HTML tags found
    """
    if re.search(r"<[^>]+>", value):
        raise ValidationError("HTML tags are not allowed in this field.")


def validate_no_script(value: str):
    """
    Validate that string contains no script-like content.

    Checks for script tags, event handlers, javascript: URLs.

    Args:
        value: String to validate

    Raises:
        ValidationError: If script content found
    """
    patterns = [
        r"<\s*script",
        r"javascript:",
        r"on\w+\s*=",  # onclick, onload, etc.
        r"data:\s*text/html",
    ]

    for pattern in patterns:
        if re.search(pattern, value, re.IGNORECASE):
            raise ValidationError("Script content is not allowed in this field.")


def validate_slug(value: str):
    """
    Validate slug format (lowercase, alphanumeric, hyphens).

    Args:
        value: String to validate

    Raises:
        ValidationError: If format is invalid
    """
    if not re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", value):
        raise ValidationError(
            "Slug must be lowercase letters, numbers, and hyphens only. "
            "Cannot start or end with hyphen."
        )
