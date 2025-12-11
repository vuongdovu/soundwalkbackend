"""
Helper functions for domain-specific operations.

This module provides domain-aware utility functions for:
- Slug generation (with model uniqueness checking)
- Data masking (email, phone - PII handling)
- User-Agent parsing (analytics/UX decisions)

These utilities are specific to user-facing applications that handle
PII, analytics, and domain model operations.

Usage:
    from toolkit.helpers import mask_email, slugify_unique, parse_user_agent

    masked = mask_email("user@example.com")  # u***@example.com
    slug = slugify_unique("My Article", Article)  # "my-article" or "my-article-2"
    info = parse_user_agent(request.META.get("HTTP_USER_AGENT", ""))

Note:
    - For generic infrastructure helpers (token generation, hashing, pagination,
      client IP), see core.helpers
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


def slugify_unique(value: str, model_class, field_name: str = "slug") -> str:
    """
    Generate a unique slug for a model instance.

    If the base slug already exists, appends a number suffix.

    Args:
        value: String to slugify
        model_class: Django model class to check uniqueness against
        field_name: Name of the slug field on the model

    Returns:
        Unique slug string

    Example:
        from myapp.models import Article
        slug = slugify_unique("My Article", Article)  # "my-article" or "my-article-2"
    """
    from django.utils.text import slugify

    base_slug = slugify(value)
    slug = base_slug
    counter = 1

    # Check for existing slugs
    filter_kwargs = {field_name: slug}
    while model_class.objects.filter(**filter_kwargs).exists():
        slug = f"{base_slug}-{counter}"
        filter_kwargs = {field_name: slug}
        counter += 1

    return slug


def mask_email(email: str) -> str:
    """
    Mask email for display.

    Keeps first character, domain, and TLD visible.

    Args:
        email: Email address to mask

    Returns:
        Masked email (e.g., "j***@example.com")

    Example:
        masked = mask_email("john.doe@example.com")  # "j***@example.com"
    """
    if not email or "@" not in email:
        return "***"

    local, domain = email.rsplit("@", 1)

    if len(local) > 1:
        masked_local = local[0] + "***"
    else:
        masked_local = "***"

    return f"{masked_local}@{domain}"


def mask_phone(phone: str) -> str:
    """
    Mask phone number for display.

    Keeps country code and last 4 digits visible.

    Args:
        phone: Phone number to mask

    Returns:
        Masked phone (e.g., "+1***-***-1234")

    Example:
        masked = mask_phone("+1-555-123-4567")  # "+1***-***-4567"
    """
    # Remove non-digit characters except +
    digits_only = re.sub(r"[^\d+]", "", phone)

    if len(digits_only) < 4:
        return "***"

    # Keep first part (country code) and last 4 digits
    if digits_only.startswith("+"):
        # Find where country code ends (assume max 3 digits)
        country_code_end = 1
        for i, char in enumerate(digits_only[1:], 1):
            if i <= 4:  # +1 to +999
                country_code_end = i + 1
            else:
                break
        prefix = digits_only[:country_code_end]
    else:
        prefix = ""

    suffix = digits_only[-4:]
    return f"{prefix}***-***-{suffix}"


def parse_user_agent(user_agent: str) -> dict:
    """
    Parse User-Agent string into device/browser info.

    Provides basic parsing without external dependencies.
    For more accurate parsing, consider using user-agents library.

    Args:
        user_agent: User-Agent header string

    Returns:
        Dict with device_type, browser, os keys

    Example:
        info = parse_user_agent(request.META.get("HTTP_USER_AGENT", ""))
    """
    result = {
        "device_type": "unknown",
        "browser": "unknown",
        "os": "unknown",
        "raw": user_agent,
    }

    if not user_agent:
        return result

    ua_lower = user_agent.lower()

    # Detect device type
    if any(mobile in ua_lower for mobile in ["mobile", "android", "iphone", "ipad"]):
        if "ipad" in ua_lower or "tablet" in ua_lower:
            result["device_type"] = "tablet"
        else:
            result["device_type"] = "mobile"
    else:
        result["device_type"] = "desktop"

    # Detect browser
    if "chrome" in ua_lower and "edg" not in ua_lower:
        result["browser"] = "Chrome"
    elif "firefox" in ua_lower:
        result["browser"] = "Firefox"
    elif "safari" in ua_lower and "chrome" not in ua_lower:
        result["browser"] = "Safari"
    elif "edg" in ua_lower:
        result["browser"] = "Edge"
    elif "opera" in ua_lower or "opr" in ua_lower:
        result["browser"] = "Opera"

    # Detect OS
    if "windows" in ua_lower:
        result["os"] = "Windows"
    elif "mac os" in ua_lower or "macos" in ua_lower:
        result["os"] = "macOS"
    elif "iphone" in ua_lower or "ipad" in ua_lower:
        result["os"] = "iOS"
    elif "android" in ua_lower:
        result["os"] = "Android"
    elif "linux" in ua_lower:
        result["os"] = "Linux"

    return result
