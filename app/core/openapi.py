"""
OpenAPI schema customizations for drf-spectacular.

This module provides hooks to customize the generated OpenAPI schema,
including operation ID modifications and tag groupings for better
documentation organization in ReDoc/Swagger UI.
"""

# Natural language summaries for dj-rest-auth endpoints
# Maps operation_id to (summary, description)
DJ_REST_AUTH_SUMMARIES = {
    # Login/Logout
    "auth_login_create": (
        "Log in",
        "Authenticate with email and password to receive JWT tokens.",
    ),
    "auth_logout_create": (
        "Log out",
        "Invalidate the current refresh token.",
    ),
    # Registration
    "auth_registration_create": (
        "Register new account",
        "Create a new user account with email and password.",
    ),
    "auth_registration_resend_email_create": (
        "Resend registration email",
        "Resend the account verification email.",
    ),
    "auth_registration_verify_email_create": (
        "Verify registration email",
        "Verify email address using the token from the registration email.",
    ),
    # Current user
    "auth_user_retrieve": (
        "Get current user",
        "Retrieve the currently authenticated user's details.",
    ),
    "auth_user_update": (
        "Update current user",
        "Full update of the currently authenticated user's details.",
    ),
    "auth_user_partial_update": (
        "Partially update current user",
        "Partial update of the currently authenticated user's details.",
    ),
    # Password management
    "auth_password_reset_create": (
        "Request password reset",
        "Send a password reset email to the specified email address.",
    ),
    "auth_password_reset_confirm_create": (
        "Confirm password reset",
        "Reset password using the token from the password reset email.",
    ),
    "auth_password_change_create": (
        "Change password",
        "Change password for the currently authenticated user.",
    ),
    # Token management
    "auth_token_refresh_create": (
        "Refresh access token",
        "Get a new access token using a valid refresh token.",
    ),
    "auth_token_verify_create": (
        "Verify token",
        "Verify that an access token is valid.",
    ),
}


def group_auth_endpoints(result, generator, request, public):
    """
    Postprocessing hook to group auth endpoints by function.

    Groups:
        - Auth - User: user retrieve/update endpoints
        - Auth - Profile: profile retrieve/update endpoints
        - Auth: authentication operations (login, logout, password, tokens)

    Also adds natural language summaries to dj-rest-auth endpoints.
    """
    paths = result.get("paths", {})

    for path, methods in paths.items():
        for method, operation in methods.items():
            if not isinstance(operation, dict):
                continue

            operation_id = operation.get("operationId", "")

            # Add natural language summaries for dj-rest-auth endpoints
            if operation_id in DJ_REST_AUTH_SUMMARIES:
                summary, description = DJ_REST_AUTH_SUMMARIES[operation_id]
                operation["summary"] = summary
                operation["description"] = description

            # Group user endpoints
            if operation_id.startswith("auth_user_"):
                operation["tags"] = ["Auth - User"]

            # Group profile endpoints
            elif operation_id.startswith("auth_profile_"):
                operation["tags"] = ["Auth - Profile"]

            # Group biometric endpoints
            elif operation_id.startswith("auth_biometric_"):
                operation["tags"] = ["Auth - Biometric"]

            # All other auth endpoints stay under "Auth"
            elif operation_id.startswith("auth_"):
                operation["tags"] = ["Auth"]

    return result
