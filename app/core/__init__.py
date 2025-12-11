"""
Core Application - Infrastructure & Base Classes

This app contains infrastructure/skeleton code that provides a foundation
for the application. Following the skeleton project rules:

- Generic, reusable base classes (no domain-specific logic)
- Clear extension points for domain apps
- Infrastructure concerns separated from business logic

Models (import from core.models):
    - BaseModel: Abstract model with timestamps (created_at, updated_at)

Model Mixins (import from core.model_mixins):
    - UUIDPrimaryKeyMixin: UUID as primary key
    - SoftDeleteMixin: Soft delete support (is_deleted, deleted_at)
    - SlugMixin: Auto-generated URL slugs
    - OrderableMixin: User-defined ordering (position field)
    - MetadataMixin: Flexible JSON metadata storage

Services (import from core.services):
    - BaseService: Base class for service layer
    - ServiceResult: Standard result wrapper for success/failure handling

Managers (import from core.managers):
    - SoftDeleteManager: Filter deleted records by default
    - SoftDeleteQuerySet: QuerySet with soft delete operations
    - BaseQuerySet: Enhanced queryset with utility methods
    - BaseManager: Manager using BaseQuerySet

Exceptions (import from core.exceptions):
    - BaseApplicationError: Base exception with error codes
    - ValidationError: Input validation failures
    - NotFoundError: Resource not found
    - PermissionDeniedError: Authorization failures
    - ConflictError: State conflicts (duplicates, etc.)
    - RateLimitError: Rate limit exceeded
    - ExternalServiceError: Third-party service failures

Serializer Mixins (import from core.serializer_mixins):
    - TimestampMixin: Auto-include timestamp fields in serializers

ViewSet Mixins (import from core.viewset_mixins):
    - PaginationMixin: Pagination helpers for custom responses
    - BulkActionMixin: Batch create/update/delete operations
    - SoftDeleteViewSetMixin: Soft delete support at viewset level

Protocols (import from core.protocols):
    - CacheBackend: Generic cache interface

Decorators (import from core.decorators):
    - rate_limit: Rate limiting decorator
    - cache_response: Response caching decorator
    - log_request: Request/response logging decorator

Helpers (import from core.helpers):
    - generate_token: Cryptographically secure token generation
    - hash_string: String hashing
    - validate_uuid: UUID validation
    - calculate_pagination: Pagination metadata calculation
    - get_client_ip: Client IP extraction from request

Validators (import from core.validators):
    - validate_file_size: File size validation
    - validate_file_extension: File extension validation
    - validate_url_domain: URL domain restriction
    - validate_json_schema: JSON schema validation
    - validate_no_html: XSS prevention (no HTML tags)
    - validate_no_script: XSS prevention (no script content)
    - validate_slug: Slug format validation

Usage:
    from core.models import BaseModel
    from core.model_mixins import SoftDeleteMixin, UUIDPrimaryKeyMixin
    from core.serializer_mixins import TimestampMixin
    from core.viewset_mixins import PaginationMixin, BulkActionMixin, SoftDeleteViewSetMixin
    from core.services import BaseService, ServiceResult
    from core.managers import SoftDeleteManager
    from core.exceptions import ValidationError, NotFoundError
    from core.helpers import generate_token, get_client_ip
    from core.decorators import rate_limit, cache_response
    from core.validators import validate_file_size, validate_no_html

    class Article(SoftDeleteMixin, BaseModel):
        objects = SoftDeleteManager()
        all_objects = models.Manager()
        title = models.CharField(max_length=200)

    class ArticleSerializer(TimestampMixin, serializers.ModelSerializer):
        class Meta:
            model = Article
            fields = ["title"]

    class ArticleViewSet(PaginationMixin, SoftDeleteViewSetMixin, viewsets.ModelViewSet):
        queryset = Article.objects.all()
        serializer_class = ArticleSerializer

Note:
    - Business logic should NOT go here. Extend core classes in your domain apps.
    - For domain-specific protocols (email, payments), see toolkit.protocols
    - For domain-specific helpers (PII masking), see toolkit.helpers
    - Django models and model mixins are NOT imported here to avoid AppRegistryNotReady
      errors. Import them directly from their modules.
"""

# Services (no Django model dependencies)
from .services import BaseService, ServiceResult

# Exceptions (no Django dependencies)
from .exceptions import (
    BaseApplicationError,
    ConflictError,
    ExternalServiceError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ValidationError,
)

# Protocols (no Django dependencies)
from .protocols import CacheBackend

# Decorators (no Django model dependencies)
from .decorators import rate_limit, cache_response, log_request

# Helpers (no Django model dependencies)
from .helpers import (
    generate_token,
    hash_string,
    validate_uuid,
    calculate_pagination,
    get_client_ip,
)

# Validators (depends on Django ValidationError but not app registry)
from .validators import (
    validate_file_size,
    validate_file_extension,
    validate_url_domain,
    validate_json_schema,
    validate_no_html,
    validate_no_script,
    validate_slug,
)

# Note: Models, model mixins, managers, serializer mixins, and viewset mixins
# are NOT imported here because they depend on Django's app registry being ready.
# Import them directly from their modules:
#   from core.models import BaseModel
#   from core.model_mixins import SoftDeleteMixin, UUIDPrimaryKeyMixin
#   from core.managers import SoftDeleteManager, SoftDeleteQuerySet
#   from core.serializer_mixins import TimestampMixin
#   from core.viewset_mixins import PaginationMixin, BulkActionMixin, SoftDeleteViewSetMixin

__all__ = [
    # Services
    "BaseService",
    "ServiceResult",
    # Exceptions
    "BaseApplicationError",
    "ValidationError",
    "NotFoundError",
    "PermissionDeniedError",
    "ConflictError",
    "RateLimitError",
    "ExternalServiceError",
    # Protocols
    "CacheBackend",
    # Decorators
    "rate_limit",
    "cache_response",
    "log_request",
    # Helpers
    "generate_token",
    "hash_string",
    "validate_uuid",
    "calculate_pagination",
    "get_client_ip",
    # Validators
    "validate_file_size",
    "validate_file_extension",
    "validate_url_domain",
    "validate_json_schema",
    "validate_no_html",
    "validate_no_script",
    "validate_slug",
]
