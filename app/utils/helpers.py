"""
Helper functions for common operations.

This module provides utility functions for:
- Token generation (cryptographic)
- String hashing
- Slug generation
- Request helpers (client IP, user agent)
- Data masking (email, phone)
- Validation utilities
- Pagination helpers

Usage:
    from utils.helpers import generate_token, mask_email, get_client_ip

    token = generate_token(32)
    masked = mask_email("user@example.com")  # u***@example.com
    ip = get_client_ip(request)
"""

from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from django.http import HttpRequest


def generate_token(length: int = 32) -> str:
    """
    Generate a cryptographically secure random token.

    Uses secrets module for secure random generation.

    Args:
        length: Number of bytes (resulting string is 2x length in hex)

    Returns:
        Hexadecimal token string

    Example:
        token = generate_token(32)  # Returns 64-character hex string
    """
    return secrets.token_hex(length)


def hash_string(value: str, algorithm: str = "sha256") -> str:
    """
    Hash a string using the specified algorithm.

    Args:
        value: String to hash
        algorithm: Hash algorithm (sha256, sha512, md5, etc.)

    Returns:
        Hexadecimal hash string

    Example:
        hashed = hash_string("password", "sha256")
    """
    hasher = hashlib.new(algorithm)
    hasher.update(value.encode("utf-8"))
    return hasher.hexdigest()


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


def get_client_ip(request: HttpRequest) -> str:
    """
    Extract client IP from request, handling proxies.

    Checks X-Forwarded-For header for proxy chains.

    Args:
        request: Django HTTP request

    Returns:
        Client IP address string

    Example:
        ip = get_client_ip(request)
    """
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        # Take the first IP in the chain (original client)
        ip = x_forwarded_for.split(",")[0].strip()
    else:
        ip = request.META.get("REMOTE_ADDR", "")
    return ip


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


def validate_uuid(value: str) -> bool:
    """
    Check if string is a valid UUID.

    Args:
        value: String to validate

    Returns:
        True if valid UUID format

    Example:
        is_valid = validate_uuid("550e8400-e29b-41d4-a716-446655440000")  # True
    """
    try:
        uuid.UUID(value)
        return True
    except (ValueError, TypeError):
        return False


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


def calculate_pagination(total: int, page: int, per_page: int) -> dict:
    """
    Calculate pagination metadata.

    Args:
        total: Total number of items
        page: Current page number (1-indexed)
        per_page: Items per page

    Returns:
        Dict with pagination metadata

    Example:
        pagination = calculate_pagination(total=100, page=3, per_page=20)
        # {
        #     "total": 100,
        #     "page": 3,
        #     "per_page": 20,
        #     "total_pages": 5,
        #     "has_next": True,
        #     "has_previous": True,
        #     "next_page": 4,
        #     "previous_page": 2,
        #     "start_index": 41,
        #     "end_index": 60
        # }
    """
    import math

    total_pages = math.ceil(total / per_page) if per_page > 0 else 0
    page = max(1, min(page, total_pages or 1))

    has_next = page < total_pages
    has_previous = page > 1

    start_index = (page - 1) * per_page + 1 if total > 0 else 0
    end_index = min(page * per_page, total)

    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "has_next": has_next,
        "has_previous": has_previous,
        "next_page": page + 1 if has_next else None,
        "previous_page": page - 1 if has_previous else None,
        "start_index": start_index,
        "end_index": end_index,
    }
