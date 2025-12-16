"""
Django settings for the application.

This is the single settings file for all environments. Configuration is driven
by environment variables using django-environ, following the 12-factor app
methodology.

Environment files:
    - .env.development: Development settings (DEBUG=True, relaxed security)
    - .env.production: Production settings (DEBUG=False, hardened security)

For more information on this file, see:
https://docs.djangoproject.com/en/5.2/topics/settings/

For the full list of settings and their values, see:
https://docs.djangoproject.com/en/5.2/ref/settings/
"""

import os
from datetime import timedelta
from pathlib import Path

import environ

# =============================================================================
# Path Configuration
# =============================================================================
# Build paths inside the project: BASE_DIR / 'subdir'
BASE_DIR = Path(__file__).resolve().parent.parent

# =============================================================================
# Environment Configuration
# =============================================================================
# Initialize django-environ
env = environ.Env(
    # Set default values and casting for common settings
    DEBUG=(bool, False),  # Default to False for safety
    ALLOWED_HOSTS=(list, []),
    CORS_ALLOWED_ORIGINS=(list, []),
    LOG_LEVEL=(str, "INFO"),
)

# Read environment file based on DJANGO_ENV or default to development
# Note: In Docker, env vars are passed directly; .env files are for local dev
env_file = os.environ.get("ENV_FILE", BASE_DIR.parent / ".env.development")
if Path(env_file).exists():
    environ.Env.read_env(env_file)

# =============================================================================
# Core Settings
# =============================================================================
# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = env("SECRET_KEY")

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = env("DEBUG")

ALLOWED_HOSTS = env("ALLOWED_HOSTS")

# =============================================================================
# Application Definition
# =============================================================================
INSTALLED_APPS = [
    # Django core apps
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",  # Required by django-allauth
    # Third-party apps - Django Channels (must be before other apps)
    "channels",
    # Third-party apps - REST & Auth
    "rest_framework",
    "rest_framework.authtoken",  # Required by dj-rest-auth
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
    "allauth.socialaccount.providers.apple",
    "dj_rest_auth",
    "dj_rest_auth.registration",
    "django_celery_beat",
    "drf_spectacular",
    # Local apps
    "core",
    "authentication",
    "toolkit",
    "chat",
    "notifications",
    "payments",
    "media",
]

MIDDLEWARE = [
    # Security middleware (should be first)
    "django.middleware.security.SecurityMiddleware",
    # WhiteNoise for static files (after security, before all else)
    "whitenoise.middleware.WhiteNoiseMiddleware",
    # CORS headers (must be before CommonMiddleware)
    "corsheaders.middleware.CorsMiddleware",
    # Django default middleware
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Allauth middleware (required for social auth)
    "allauth.account.middleware.AccountMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# =============================================================================
# Database Configuration
# =============================================================================
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases
# Using psycopg3 (not psycopg2) for async support
DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="postgres://postgres:postgres@db:5432/app_dev",
    ),
}

# Use psycopg3's native connection options
DATABASES["default"]["OPTIONS"] = {
    "connect_timeout": 10,
}

# =============================================================================
# Cache Configuration
# =============================================================================
# https://docs.djangoproject.com/en/5.2/topics/cache/
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": env("REDIS_URL", default="redis://redis:6379/0"),
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            # Gracefully handle Redis connection failures
            "IGNORE_EXCEPTIONS": True,
        },
    }
}

# Use Redis for session storage (faster than database)
SESSION_ENGINE = "django.contrib.sessions.backends.cache"
SESSION_CACHE_ALIAS = "default"

# =============================================================================
# Authentication Configuration
# =============================================================================
# Custom user model (MUST be set before first migration)
AUTH_USER_MODEL = "authentication.User"

# Custom migrations for third-party apps that don't support UUID User PK
MIGRATION_MODULES = {
    "authtoken": "config.authtoken_migrations",
}

# Authentication backends (allauth for social, ModelBackend for admin)
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

# Password validation
# https://docs.djangoproject.com/en/5.2/ref/settings/#auth-password-validators
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# =============================================================================
# Django REST Framework Configuration
# =============================================================================
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        # JWT authentication (primary for API clients)
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        # Session authentication (for browsable API and admin)
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    # OpenAPI schema generation
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    # Pagination
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    # Throttling (rate limiting)
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "100/hour",
        "user": "1000/hour",
    },
}

# Add browsable API in debug mode
if DEBUG:
    REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"].append(
        "rest_framework.renderers.BrowsableAPIRenderer"
    )

# =============================================================================
# drf-spectacular (OpenAPI) Configuration
# =============================================================================
SPECTACULAR_SETTINGS = {
    "TITLE": "API Documentation",
    "DESCRIPTION": "REST API documentation",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    # Strip /api/v1 prefix from operation IDs (e.g., auth_login instead of api_v1_auth_login)
    "SCHEMA_PATH_PREFIX": r"/api/v[0-9]+",
    # Group auth endpoints (User, Profile, Auth) in documentation
    "POSTPROCESSING_HOOKS": ["core.openapi.group_auth_endpoints"],
    # Authentication schemes
    "SECURITY": [{"Bearer": []}],
    "APPEND_COMPONENTS": {
        "securitySchemes": {
            "Bearer": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
            }
        }
    },
    # Schema customization
    "COMPONENT_SPLIT_REQUEST": True,
    "SORT_OPERATIONS": False,
}

# =============================================================================
# Simple JWT Configuration
# =============================================================================
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=60),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": SECRET_KEY,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "AUTH_HEADER_NAME": "HTTP_AUTHORIZATION",
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}

# =============================================================================
# dj-rest-auth Configuration
# =============================================================================
REST_AUTH = {
    "USE_JWT": True,
    "JWT_AUTH_COOKIE": None,  # Don't use cookies for JWT
    "JWT_AUTH_HTTPONLY": False,
    "USER_DETAILS_SERIALIZER": "authentication.serializers.UserSerializer",
    "REGISTER_SERIALIZER": "authentication.serializers.RegisterSerializer",
}

# =============================================================================
# django-allauth Configuration
# =============================================================================
SITE_ID = 1  # Required by allauth

# Account settings
ACCOUNT_AUTHENTICATION_METHOD = "email"
ACCOUNT_USERNAME_REQUIRED = False
ACCOUNT_USER_MODEL_USERNAME_FIELD = None  # User model has no username field
ACCOUNT_EMAIL_REQUIRED = True
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_EMAIL_VERIFICATION = "optional"  # "mandatory" for production

# Custom adapters for social auth
ACCOUNT_ADAPTER = "authentication.adapters.CustomAccountAdapter"
SOCIALACCOUNT_ADAPTER = "authentication.adapters.CustomSocialAccountAdapter"

# Social account providers configuration
SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "SCOPE": ["profile", "email"],
        "AUTH_PARAMS": {"access_type": "online"},
        "APP": {
            "client_id": env("GOOGLE_CLIENT_ID", default=""),
            "secret": env("GOOGLE_CLIENT_SECRET", default=""),
        },
    },
    "apple": {
        "APP": {
            "client_id": env("APPLE_CLIENT_ID", default=""),
            "secret": env("APPLE_PRIVATE_KEY", default=""),
            "key": env("APPLE_KEY_ID", default=""),
            "settings": {
                "team_id": env("APPLE_TEAM_ID", default=""),
            },
        },
        "SCOPE": ["email", "name"],
    },
}

# =============================================================================
# CORS Configuration
# =============================================================================
CORS_ALLOWED_ORIGINS = env("CORS_ALLOWED_ORIGINS")
CORS_ALLOW_CREDENTIALS = True

# =============================================================================
# Celery Configuration
# =============================================================================
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://redis:6379/1")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default="redis://redis:6379/1")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "UTC"
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 30 * 60  # 30 minutes
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

# =============================================================================
# Stripe Configuration
# =============================================================================
# Get your API keys from: https://dashboard.stripe.com/apikeys
# Use test keys (sk_test_...) for development, live keys (sk_live_...) for production
STRIPE_SECRET_KEY = env("STRIPE_SECRET_KEY", default="")
STRIPE_PUBLISHABLE_KEY = env("STRIPE_PUBLISHABLE_KEY", default="")

# Webhook signing secret from: https://dashboard.stripe.com/webhooks
# Each webhook endpoint has its own signing secret
STRIPE_WEBHOOK_SECRET = env("STRIPE_WEBHOOK_SECRET", default="")

# API timeout in seconds (default: 10)
# Keep low for responsive error handling; increase if experiencing timeouts
STRIPE_API_TIMEOUT_SECONDS = env.int("STRIPE_API_TIMEOUT_SECONDS", default=10)

# Maximum retry attempts for transient failures (default: 3)
STRIPE_MAX_RETRIES = env.int("STRIPE_MAX_RETRIES", default=3)

# =============================================================================
# Platform Configuration
# =============================================================================
# Platform fee percentage taken from each payment (default: 15%)
PLATFORM_FEE_PERCENT = env.int("PLATFORM_FEE_PERCENT", default=15)

# =============================================================================
# Escrow Configuration
# =============================================================================
# Default hold duration for escrow payments (in days)
# Funds are held until service completion or expiration, whichever comes first
ESCROW_DEFAULT_HOLD_DURATION_DAYS = env.int(
    "ESCROW_DEFAULT_HOLD_DURATION_DAYS",
    default=42,  # 6 weeks
)

# Maximum hold duration allowed (in days)
# Prevents indefinite holds; expired holds auto-release to recipient
ESCROW_MAX_HOLD_DURATION_DAYS = env.int(
    "ESCROW_MAX_HOLD_DURATION_DAYS",
    default=90,
)

# =============================================================================
# ClamAV Configuration (Malware Scanning)
# =============================================================================
# ClamAV daemon connection settings
CLAMAV_HOST = env("CLAMAV_HOST", default="clamav")
CLAMAV_PORT = env.int("CLAMAV_PORT", default=3310)
CLAMAV_TIMEOUT = env.int("CLAMAV_TIMEOUT", default=30)

# Files larger than this threshold are scanned via file path instead of stream
# Stream scanning loads file into memory; path scanning reads directly from disk
CLAMAV_LARGE_FILE_THRESHOLD_MB = env.int("CLAMAV_LARGE_FILE_THRESHOLD_MB", default=50)

# Circuit breaker settings for ClamAV resilience
# Opens circuit after consecutive failures, allowing fail-open behavior
CLAMAV_CIRCUIT_FAILURE_THRESHOLD = env.int(
    "CLAMAV_CIRCUIT_FAILURE_THRESHOLD", default=5
)
CLAMAV_CIRCUIT_RECOVERY_TIMEOUT = env.int("CLAMAV_CIRCUIT_RECOVERY_TIMEOUT", default=60)

# Alert if virus definitions are older than this many days
CLAMAV_STALE_DEFINITIONS_DAYS = env.int("CLAMAV_STALE_DEFINITIONS_DAYS", default=3)

# Quarantine directory for infected files (relative to MEDIA_ROOT)
CLAMAV_QUARANTINE_DIR = env("CLAMAV_QUARANTINE_DIR", default="quarantine")

# =============================================================================
# Internationalization
# =============================================================================
# https://docs.djangoproject.com/en/5.2/topics/i18n/
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# =============================================================================
# Static Files (CSS, JavaScript, Images)
# =============================================================================
# https://docs.djangoproject.com/en/5.2/howto/static-files/
STATIC_URL = env("STATIC_URL", default="/static/")
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"] if (BASE_DIR / "static").exists() else []

# WhiteNoise configuration for static file serving
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

# =============================================================================
# Media Files (User Uploads)
# =============================================================================
MEDIA_URL = env("MEDIA_URL", default="/media/")
MEDIA_ROOT = BASE_DIR / "uploads"  # Storage dir, not app name

# =============================================================================
# Default Primary Key Field Type
# =============================================================================
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# =============================================================================
# Email Configuration
# =============================================================================
EMAIL_BACKEND = env(
    "EMAIL_BACKEND",
    default="django.core.mail.backends.console.EmailBackend",
)
EMAIL_HOST = env("EMAIL_HOST", default="localhost")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="noreply@example.com")

# =============================================================================
# Logging Configuration
# =============================================================================
LOG_LEVEL = env("LOG_LEVEL")

# Log file name determined by service (web, celery-worker, celery-beat)
# Set via LOG_FILE_NAME environment variable in docker-compose.yaml
LOG_FILE_NAME = env("LOG_FILE_NAME", default="django.log")
LOG_DIR = BASE_DIR / "logs"

# Ensure log directory exists (handles local development without Docker)
LOG_DIR.mkdir(parents=True, exist_ok=True)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {module} {process:d} {thread:d} {message}",
            "style": "{",
        },
        "simple": {
            "format": "{levelname} {message}",
            "style": "{",
        },
        "file": {
            # Detailed format for persistent logs with timestamp, level, logger name, and location
            "format": "[{asctime}] {levelname} {name} {module}:{lineno} - {message}",
            "style": "{",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
        "file": {
            # Rotating file handler prevents unbounded disk usage
            # Max 10MB per file, keeps 5 backups (60MB total per log type)
            "level": "DEBUG",
            "class": "logging.handlers.RotatingFileHandler",
            "filename": LOG_DIR / LOG_FILE_NAME,
            "maxBytes": 10 * 1024 * 1024,  # 10MB
            "backupCount": 5,
            "formatter": "file",
            "encoding": "utf-8",
        },
    },
    "root": {
        "handlers": ["console", "file"],
        "level": LOG_LEVEL,
    },
    "loggers": {
        "django": {
            "handlers": ["console", "file"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
        "django.request": {
            "handlers": ["console", "file"],
            "level": "ERROR",
            "propagate": False,
        },
        "celery": {
            "handlers": ["console", "file"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
    },
}

# =============================================================================
# Security Settings (Production Only)
# =============================================================================
# These settings are enforced only when DEBUG=False
if not DEBUG:
    # HTTPS/SSL settings
    SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

    # Cookie security
    SESSION_COOKIE_SECURE = env.bool("SESSION_COOKIE_SECURE", default=True)
    CSRF_COOKIE_SECURE = env.bool("CSRF_COOKIE_SECURE", default=True)

    # HSTS (HTTP Strict Transport Security)
    SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=31536000)  # 1 year
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool(
        "SECURE_HSTS_INCLUDE_SUBDOMAINS", default=True
    )
    SECURE_HSTS_PRELOAD = env.bool("SECURE_HSTS_PRELOAD", default=True)

    # Additional security headers
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = "DENY"

# =============================================================================
# Django Channels Configuration
# =============================================================================
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [env("REDIS_URL", default="redis://redis:6379/0")],
            "capacity": 1500,
            "expiry": 10,
        },
    },
}
