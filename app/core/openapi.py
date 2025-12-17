"""
OpenAPI schema customizations for drf-spectacular.

This module provides hooks to customize the generated OpenAPI schema,
including operation ID modifications and tag groupings for better
documentation organization in ReDoc/Swagger UI.

Tag naming follows the pattern: [App Name] - [Group Name]
Examples:
- Auth - User (user CRUD)
- Auth - Profile (profile CRUD)
- Media - Upload (file uploads)
- Media - Files (file operations)
- Media - Sharing (share management)
- Media - Chunked Upload (resumable uploads)
- Media - Tags (tagging system)
- Media - Search (full-text search)
- Media - Quota (storage quota)
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
    Postprocessing hook to group API endpoints by function.

    Groups endpoints following the [App Name] - [Group Name] pattern:

    Auth groups:
        - Auth - User: user retrieve/update endpoints
        - Auth - Profile: profile retrieve/update endpoints
        - Auth - Biometric: biometric authentication
        - Auth: authentication operations (login, logout, password, tokens)

    Media groups (set via tags= in @extend_schema, this ensures consistency):
        - Media - Upload: single file upload
        - Media - Files: file details, download, view
        - Media - Sharing: share management
        - Media - Chunked Upload: resumable large file uploads
        - Media - Tags: tagging system
        - Media - Search: full-text search
        - Media - Quota: storage quota status

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

            # Group auth endpoints (not media - those use tags= in views)
            if operation_id.startswith("auth_user_"):
                operation["tags"] = ["Auth - User"]

            elif operation_id.startswith("auth_profile_"):
                operation["tags"] = ["Auth - Profile"]

            elif operation_id.startswith("auth_biometric_"):
                operation["tags"] = ["Auth - Biometric"]

            elif operation_id.startswith("auth_"):
                operation["tags"] = ["Auth"]

    # Add tag descriptions for better documentation
    result["tags"] = [
        {
            "name": "Auth",
            "description": "Authentication operations including login, logout, password reset, and token management.",
        },
        {
            "name": "Auth - User",
            "description": "Current user retrieval and updates.",
        },
        {
            "name": "Auth - Profile",
            "description": "User profile management including personal details and preferences.",
        },
        {
            "name": "Auth - Biometric",
            "description": "Biometric authentication using device-native security (Face ID, Touch ID).",
        },
        {
            "name": "Media - Upload",
            "description": "Single file upload with MIME type validation, size limits, and quota checks.",
        },
        {
            "name": "Media - Files",
            "description": "File operations including metadata retrieval, download, and inline viewing.",
        },
        {
            "name": "Media - Search",
            "description": "Full-text search across media files using PostgreSQL with relevance ranking.",
        },
        {
            "name": "Media - Sharing",
            "description": "File sharing with other users including permission management and expiration.",
        },
        {
            "name": "Media - Tags",
            "description": "Tagging system for organizing and filtering media files.",
        },
        {
            "name": "Media - Chunked Upload",
            "description": "Resumable uploads for large files with S3 multipart support.",
        },
        {
            "name": "Media - Quota",
            "description": "Storage quota monitoring and management.",
        },
    ]

    return result
