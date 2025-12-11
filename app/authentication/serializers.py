"""
Serializers for authentication models.

This module provides DRF serializers for:
- User model (read operations)
- Profile model (read/update operations with conditional username validation)
- LinkedAccount model (read operations)
- Registration (create user)

Related files:
    - models.py: User, Profile, and LinkedAccount models
    - views.py: Views that use these serializers
    - settings.py: REST_AUTH serializer configuration

Security:
    - Password fields are write-only
    - Sensitive fields are read-only where appropriate

Note:
    ProfileUpdateSerializer handles both initial profile completion and
    subsequent updates. If profile.username is empty, username is required.
"""

import re

from django.core.validators import FileExtensionValidator
from rest_framework import serializers

from authentication.models import User, Profile, LinkedAccount, RESERVED_USERNAMES


class UserSerializer(serializers.ModelSerializer):
    """
    Serializer for User model (read operations).

    Used by dj-rest-auth for the /api/v1/auth/user/ endpoint and
    for serializing user data in API responses.

    Includes profile data for convenience.
    """

    full_name = serializers.SerializerMethodField()
    username = serializers.CharField(source="profile.username", read_only=True)
    profile_completed = serializers.BooleanField(
        source="has_completed_profile", read_only=True
    )
    linked_providers = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "full_name",
            "username",
            "email_verified",
            "profile_completed",
            "linked_providers",
            "date_joined",
        ]
        read_only_fields = fields

    def get_full_name(self, obj):
        """Return the user's full name from profile."""
        return obj.get_full_name()

    def get_linked_providers(self, obj):
        """Return list of linked authentication providers."""
        return list(obj.linked_accounts.values_list("provider", flat=True))


class ProfileSerializer(serializers.ModelSerializer):
    """
    Serializer for Profile model (read operations).

    Provides complete profile data including computed fields.
    """

    user_id = serializers.IntegerField(source="user.id", read_only=True)
    user_email = serializers.EmailField(source="user.email", read_only=True)
    full_name = serializers.CharField(read_only=True)
    profile_picture_url = serializers.SerializerMethodField()
    is_complete = serializers.SerializerMethodField()

    class Meta:
        model = Profile
        fields = [
            "user_id",
            "user_email",
            "username",
            "first_name",
            "last_name",
            "full_name",
            "profile_picture",
            "profile_picture_url",
            "timezone",
            "preferences",
            "is_complete",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["user_id", "user_email", "full_name", "created_at", "updated_at"]

    def get_profile_picture_url(self, obj):
        """Return absolute URL for profile picture."""
        if obj.profile_picture:
            request = self.context.get("request")
            if request:
                return request.build_absolute_uri(obj.profile_picture.url)
            return obj.profile_picture.url
        return None

    def get_is_complete(self, obj):
        """Return whether the profile is complete (has username set)."""
        return bool(obj.username)


class ProfileUpdateSerializer(serializers.ModelSerializer):
    """
    Serializer for updating profile information.

    Handles both initial profile completion and subsequent updates:
    - If profile has no username, username is REQUIRED
    - If profile has a username, username is optional (can update or omit)

    This replaces the separate ProfileCompletionSerializer.
    """

    username = serializers.CharField(
        min_length=3,
        max_length=30,
        required=False,  # Dynamic requirement handled in __init__
        help_text="Unique username (3-30 chars, alphanumeric + _ + -)",
    )
    profile_picture = serializers.ImageField(
        required=False,
        allow_null=True,
        validators=[
            FileExtensionValidator(
                allowed_extensions=["jpg", "jpeg", "png", "gif", "webp"]
            ),
        ],
    )

    class Meta:
        model = Profile
        fields = [
            "username",
            "first_name",
            "last_name",
            "profile_picture",
            "timezone",
            "preferences",
        ]

    def __init__(self, *args, **kwargs):
        """Make username required if profile doesn't have one set."""
        super().__init__(*args, **kwargs)
        # If profile has no username, make it required
        if self.instance and not self.instance.username:
            self.fields["username"].required = True

    def validate(self, attrs):
        """Ensure username is provided when profile has no username set."""
        # For partial updates, DRF skips required check for missing fields
        # We need to explicitly check if username is required but missing
        if self.instance and not self.instance.username:
            if "username" not in attrs:
                raise serializers.ValidationError(
                    {"username": "Username is required to complete your profile."}
                )
        return super().validate(attrs)

    def validate_username(self, value):
        """Validate username format, uniqueness, and reserved names."""
        if value is None:
            return value

        username = value.lower().strip()

        # Check format: 3-30 chars, alphanumeric + _ + -
        if not re.match(r"^[a-zA-Z0-9_-]{3,30}$", username):
            raise serializers.ValidationError(
                "Username must be 3-30 characters and contain only "
                "letters, numbers, underscores, and hyphens."
            )

        # Check reserved names
        if username in RESERVED_USERNAMES:
            raise serializers.ValidationError(
                f"The username '{username}' is reserved and cannot be used."
            )

        # Check uniqueness (case-insensitive), excluding current user
        user = self.context.get("user")
        existing = Profile.objects.filter(username__iexact=username)
        if user:
            existing = existing.exclude(user=user)
        if existing.exists():
            raise serializers.ValidationError("This username is already taken.")

        return username

    def validate_profile_picture(self, value):
        """Validate profile picture size and format."""
        if value:
            # Max 5MB
            max_size = 5 * 1024 * 1024
            if value.size > max_size:
                raise serializers.ValidationError(
                    "Profile picture must be smaller than 5MB."
                )
        return value

    def validate_preferences(self, value):
        """Validate preferences is a dictionary."""
        if not isinstance(value, dict):
            raise serializers.ValidationError("Preferences must be a JSON object")
        return value


class LinkedAccountSerializer(serializers.ModelSerializer):
    """
    Serializer for LinkedAccount model (read operations).
    """

    provider_display = serializers.CharField(
        source="get_provider_display", read_only=True
    )

    class Meta:
        model = LinkedAccount
        fields = [
            "id",
            "provider",
            "provider_display",
            "provider_user_id",
            "created_at",
        ]
        read_only_fields = fields


class RegisterSerializer(serializers.Serializer):
    """
    Serializer for user registration.

    Used by dj-rest-auth for the /api/v1/auth/registration/ endpoint.
    Handles email/password registration (not OAuth).

    Note: Profile data (first_name, last_name, username) is set later
    via the profile completion endpoint.
    """

    email = serializers.EmailField(required=True)
    password = serializers.CharField(
        write_only=True,
        min_length=8,
        style={"input_type": "password"},
        help_text="Password must be at least 8 characters.",
    )
    password_confirm = serializers.CharField(
        write_only=True,
        style={"input_type": "password"},
        help_text="Confirm your password.",
    )

    def validate_email(self, value):
        """Validate that email is not already in use."""
        email = value.lower().strip()
        if User.objects.filter(email__iexact=email).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return email

    def validate(self, attrs):
        """Validate that passwords match."""
        if attrs["password"] != attrs["password_confirm"]:
            raise serializers.ValidationError(
                {"password_confirm": "Passwords do not match."}
            )
        return attrs

    def create(self, validated_data):
        """Create a new user with the validated data."""
        validated_data.pop("password_confirm")

        user = User.objects.create_user(
            email=validated_data["email"],
            password=validated_data["password"],
        )

        # Create LinkedAccount for email registration
        LinkedAccount.objects.create(
            user=user,
            provider=LinkedAccount.Provider.EMAIL,
            provider_user_id=user.email,
        )

        return user
