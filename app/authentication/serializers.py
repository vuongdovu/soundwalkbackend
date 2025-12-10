"""
Serializers for authentication models.

This module provides DRF serializers for:
- User model (read/update operations)
- Profile model (read/update operations)
- Registration (create user)

Related files:
    - models.py: User and Profile models
    - views.py: Views that use these serializers
    - settings.py: REST_AUTH serializer configuration

Security:
    - Password fields are write-only
    - Sensitive fields (oauth_provider, email_verified) are read-only
"""

from rest_framework import serializers

# TODO: Uncomment when models are fully implemented
# from authentication.models import User, Profile


class UserSerializer(serializers.Serializer):
    """
    Serializer for User model (read operations).

    Used by dj-rest-auth for the /api/v1/auth/user/ endpoint and
    for serializing user data in API responses.

    Excludes sensitive fields like password.

    TODO: Convert to ModelSerializer when User model is implemented
    """

    id = serializers.IntegerField(read_only=True)
    email = serializers.EmailField(read_only=True)
    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    full_name = serializers.SerializerMethodField()
    oauth_provider = serializers.CharField(read_only=True)
    email_verified = serializers.BooleanField(read_only=True)
    is_oauth_user = serializers.SerializerMethodField()
    date_joined = serializers.DateTimeField(read_only=True)

    def get_full_name(self, obj):
        """Return the user's full name."""
        # TODO: Implement when model is available
        # return obj.get_full_name()
        first = getattr(obj, "first_name", "") or ""
        last = getattr(obj, "last_name", "") or ""
        full = f"{first} {last}".strip()
        return full if full else getattr(obj, "email", "")

    def get_is_oauth_user(self, obj):
        """Return whether user registered via OAuth."""
        # TODO: Implement when model is available
        # return obj.is_oauth_user
        return getattr(obj, "oauth_provider", "email") != "email"


class UserUpdateSerializer(serializers.Serializer):
    """
    Serializer for updating user profile information.

    Allows users to update their name but not email or auth-related fields.

    TODO: Convert to ModelSerializer when User model is implemented
    """

    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True)

    def update(self, instance, validated_data):
        """Update user profile fields."""
        instance.first_name = validated_data.get("first_name", instance.first_name)
        instance.last_name = validated_data.get("last_name", instance.last_name)
        instance.save(update_fields=["first_name", "last_name", "updated_at"])
        return instance


class RegisterSerializer(serializers.Serializer):
    """
    Serializer for user registration.

    Used by dj-rest-auth for the /api/v1/auth/registration/ endpoint.
    Handles email/password registration (not OAuth).

    TODO: Convert to ModelSerializer when User model is implemented
    """

    email = serializers.EmailField(required=True)
    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
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
        # TODO: Implement when model is available
        # from authentication.models import User
        # if User.objects.filter(email__iexact=value).exists():
        #     raise serializers.ValidationError("A user with this email already exists.")
        return value.lower()

    def validate(self, attrs):
        """Validate that passwords match."""
        if attrs["password"] != attrs["password_confirm"]:
            raise serializers.ValidationError(
                {"password_confirm": "Passwords do not match."}
            )
        return attrs

    def create(self, validated_data):
        """Create a new user with the validated data."""
        # Remove password_confirm as it's not a model field
        validated_data.pop("password_confirm")

        # TODO: Implement when model is available
        # from authentication.models import User
        # user = User.objects.create_user(
        #     email=validated_data["email"],
        #     password=validated_data["password"],
        #     first_name=validated_data.get("first_name", ""),
        #     last_name=validated_data.get("last_name", ""),
        # )
        # return user
        raise NotImplementedError("User creation not yet implemented")


class ProfileSerializer(serializers.Serializer):
    """
    Serializer for Profile model.

    Provides read and update operations for user profile data.

    TODO: Convert to ModelSerializer when Profile model is implemented
    """

    # user field shows basic user info
    user_id = serializers.IntegerField(source="user.id", read_only=True)
    user_email = serializers.EmailField(source="user.email", read_only=True)

    # Profile fields
    avatar_url = serializers.URLField(max_length=500, required=False, allow_blank=True)
    display_name = serializers.CharField(max_length=50, required=False, allow_blank=True)
    timezone = serializers.CharField(max_length=50, required=False, default="UTC")
    preferences = serializers.JSONField(required=False, default=dict)

    # Timestamps (read-only)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    def validate_timezone(self, value):
        """Validate timezone string."""
        # TODO: Validate against pytz or zoneinfo
        # import zoneinfo
        # try:
        #     zoneinfo.ZoneInfo(value)
        # except KeyError:
        #     raise serializers.ValidationError(f"Invalid timezone: {value}")
        return value

    def validate_preferences(self, value):
        """Validate preferences JSON structure."""
        if not isinstance(value, dict):
            raise serializers.ValidationError("Preferences must be a JSON object")
        return value


class ProfileUpdateSerializer(serializers.Serializer):
    """
    Serializer for updating profile information.

    Only allows updating modifiable profile fields.

    TODO: Convert to ModelSerializer when Profile model is implemented
    """

    avatar_url = serializers.URLField(max_length=500, required=False, allow_blank=True)
    display_name = serializers.CharField(max_length=50, required=False, allow_blank=True)
    timezone = serializers.CharField(max_length=50, required=False)
    preferences = serializers.JSONField(required=False)

    def update(self, instance, validated_data):
        """Update profile fields."""
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        return instance
