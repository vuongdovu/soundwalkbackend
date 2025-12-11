"""
Helper functions for common infrastructure operations.

This module provides domain-agnostic utility functions for:
- Token generation (cryptographic)
- String hashing
- UUID validation
- Pagination helpers
- HTTP request helpers (client IP extraction)

These utilities are pure infrastructure - they have no knowledge
of domain concepts like users, subscriptions, or business logic.

Usage:
    from core.helpers import generate_token, hash_string, get_client_ip

    token = generate_token(32)
    hashed = hash_string("password", "sha256")
    ip = get_client_ip(request)

Note:
    - For domain-aware helpers (PII masking, user-agent parsing), see toolkit.helpers
"""

from __future__ import annotations

import hashlib
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
