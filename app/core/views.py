"""
Core views providing infrastructure endpoints.

This module contains views that are not part of the business domain but are
essential for application infrastructure, such as health checks.
"""

from django.db import connection
from django.http import JsonResponse


def health_check(request):
    """
    Health check endpoint for monitoring and orchestration.

    This endpoint is used by:
    - Docker health checks
    - Kubernetes liveness/readiness probes
    - Load balancers (AWS ALB, nginx)
    - Monitoring systems (Datadog, New Relic)

    Returns:
        JsonResponse with status and component health:
        - status: "healthy" or "unhealthy"
        - database: "connected" or "disconnected"
        - cache: "connected" or "disconnected" (optional)

    HTTP Status Codes:
        200: All systems operational
        503: One or more systems unhealthy

    Example Response:
        {
            "status": "healthy",
            "database": "connected",
            "cache": "connected"
        }
    """
    health_status = {
        "status": "healthy",
        "database": "unknown",
        "cache": "unknown",
    }
    is_healthy = True

    # Check database connectivity
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        health_status["database"] = "connected"
    except Exception:
        health_status["database"] = "disconnected"
        health_status["status"] = "unhealthy"
        is_healthy = False

    # Check Redis cache connectivity
    try:
        from django.core.cache import cache

        cache.set("health_check", "ok", timeout=1)
        if cache.get("health_check") == "ok":
            health_status["cache"] = "connected"
        else:
            health_status["cache"] = "disconnected"
            # Cache failure is not critical - mark as degraded but still healthy
    except Exception:
        health_status["cache"] = "disconnected"
        # Don't fail health check for cache issues (graceful degradation)

    # Return appropriate HTTP status
    status_code = 200 if is_healthy else 503

    return JsonResponse(health_status, status=status_code)
