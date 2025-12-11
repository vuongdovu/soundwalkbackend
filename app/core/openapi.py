"""
OpenAPI schema customizations for drf-spectacular.

This module provides hooks to customize the generated OpenAPI schema,
including operation ID modifications and tag groupings for better
documentation organization in ReDoc/Swagger UI.
"""


def group_auth_endpoints(result, generator, request, public):
    """
    Postprocessing hook to group auth endpoints by function.

    Groups:
        - Auth - User: user retrieve/update endpoints
        - Auth - Profile: profile retrieve/update endpoints
        - Auth: authentication operations (login, logout, password, tokens)
    """
    paths = result.get("paths", {})

    for path, methods in paths.items():
        for method, operation in methods.items():
            if not isinstance(operation, dict):
                continue

            operation_id = operation.get("operationId", "")

            # Group user endpoints
            if operation_id.startswith("auth_user_"):
                operation["tags"] = ["Auth - User"]

            # Group profile endpoints
            elif operation_id.startswith("auth_profile_"):
                operation["tags"] = ["Auth - Profile"]

            # All other auth endpoints stay under "Auth"
            elif operation_id.startswith("auth_"):
                operation["tags"] = ["Auth"]

    return result
