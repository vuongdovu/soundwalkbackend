"""
Custom decorators for views and functions.

This module provides generic infrastructure decorators for:
- Rate limiting (Redis-based)
- Response caching
- Request/response logging

These are domain-agnostic decorators that can be used in any Django project.

Usage:
    from core.decorators import rate_limit, cache_response, log_request

    @rate_limit(key="api_call", limit=100, period=3600)
    def my_view(request):
        ...

    @cache_response(timeout=600)
    def expensive_view(request):
        ...

    @log_request()
    def debug_view(request):
        ...

Note:
    - For domain-specific decorators (subscription checks), see toolkit.decorators
"""

from __future__ import annotations

import functools
import logging
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def rate_limit(key: str, limit: int, period: int):
    """
    Rate limit decorator using Redis.

    Limits the number of calls within a time period.
    Uses the authenticated user's ID or client IP as identifier.

    Args:
        key: Unique key prefix for this rate limit
        limit: Maximum number of requests
        period: Time period in seconds

    Returns:
        Decorator function

    Example:
        @rate_limit(key="api_call", limit=100, period=3600)
        def my_view(request):
            ...

    HTTP 429 Response:
        Returns 429 Too Many Requests when limit exceeded.
    """

    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(request, *args, **kwargs):
            # TODO: Implement rate limiting
            # from django.core.cache import cache
            # from django.http import JsonResponse
            # from core.helpers import get_client_ip
            #
            # # Build rate limit key
            # if hasattr(request, "user") and request.user.is_authenticated:
            #     identifier = f"user:{request.user.id}"
            # else:
            #     identifier = f"ip:{get_client_ip(request)}"
            #
            # cache_key = f"rate_limit:{key}:{identifier}"
            #
            # # Get current count
            # current = cache.get(cache_key, 0)
            #
            # if current >= limit:
            #     logger.warning(f"Rate limit exceeded for {identifier}: {key}")
            #     return JsonResponse(
            #         {"detail": "Rate limit exceeded. Try again later."},
            #         status=429
            #     )
            #
            # # Increment counter
            # cache.set(cache_key, current + 1, timeout=period)
            #
            return func(request, *args, **kwargs)

        return wrapper

    return decorator


def cache_response(timeout: int = 300, key_func: Callable | None = None):
    """
    Cache view response.

    Caches the response for the specified timeout period.
    By default, uses the request path as cache key.

    Args:
        timeout: Cache timeout in seconds
        key_func: Optional function to generate cache key from request

    Returns:
        Decorator function

    Example:
        @cache_response(timeout=600)
        def expensive_view(request):
            ...

        @cache_response(timeout=300, key_func=lambda r: f"view:{r.path}:{r.user.id}")
        def user_specific_view(request):
            ...
    """

    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(request, *args, **kwargs):
            # TODO: Implement response caching
            # from django.core.cache import cache
            #
            # # Generate cache key
            # if key_func:
            #     cache_key = key_func(request)
            # else:
            #     cache_key = f"response_cache:{request.path}"
            #
            # # Check cache
            # cached_response = cache.get(cache_key)
            # if cached_response is not None:
            #     return cached_response
            #
            # # Call view
            # response = func(request, *args, **kwargs)
            #
            # # Cache successful responses only
            # if hasattr(response, "status_code") and response.status_code == 200:
            #     cache.set(cache_key, response, timeout=timeout)
            #
            return func(request, *args, **kwargs)

        return wrapper

    return decorator


def log_request(logger_name: str | None = None):
    """
    Log request/response for debugging.

    Logs request method, path, user, and response status.

    Args:
        logger_name: Optional logger name (defaults to view module)

    Returns:
        Decorator function

    Example:
        @log_request()
        def my_view(request):
            ...

        @log_request(logger_name="api.views")
        def api_view(request):
            ...
    """

    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(request, *args, **kwargs):
            log = logging.getLogger(logger_name or func.__module__)

            # Log request
            user_str = (
                str(request.user) if hasattr(request, "user") else "anonymous"
            )
            log.debug(
                f"Request: {request.method} {request.path}",
                extra={
                    "user": user_str,
                    "method": request.method,
                    "path": request.path,
                },
            )

            # Call view
            response = func(request, *args, **kwargs)

            # Log response
            status_code = getattr(response, "status_code", "unknown")
            log.debug(
                f"Response: {status_code} for {request.method} {request.path}",
                extra={
                    "status_code": status_code,
                    "method": request.method,
                    "path": request.path,
                },
            )

            return response

        return wrapper

    return decorator
