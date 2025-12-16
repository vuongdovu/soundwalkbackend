"""
Root pytest configuration for the Django project.

This module configures pytest-django and provides project-wide fixtures.
App-specific fixtures are defined in each app's tests/conftest.py.
"""

import os
import django
import pytest

# Ensure Django settings are configured before any tests run
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")


def pytest_configure():
    """Configure Django settings before tests run."""
    django.setup()

    from django.conf import settings

    # Disable throttling during tests to prevent rate limit failures
    settings.REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"] = []
    settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] = {}

    # Use fast password hasher for tests (PBKDF2 is too slow with 870K iterations)
    settings.PASSWORD_HASHERS = [
        "django.contrib.auth.hashers.MD5PasswordHasher",
    ]


def pytest_collection_modifyitems(items):
    """
    Auto-mark tests based on filename patterns.

    Mapping:
    - test_integration.py → e2e (full user journey workflows)
    - test_views.py, test_services.py, test_tasks.py, etc. → integration
    - test_models.py, test_serializers.py, test_validators.py, etc. → unit
    - Unmatched files → integration (safe default for Django)

    Explicit markers on test functions/classes take precedence.
    """
    # Filename patterns for each category
    e2e_patterns = ["test_integration.py"]

    integration_patterns = [
        "test_views.py",
        "test_services.py",
        "test_tasks.py",
        "test_permissions.py",
        "test_webhooks.py",
        "test_handlers.py",
        "test_quarantine.py",
        "test_scanner.py",
        "test_scan_tasks.py",
        "test_orchestrator.py",
        "test_payout_service.py",
        "test_reconciliation_service.py",
        "test_refund_service.py",
        "test_payout_executor.py",
        "test_hold_manager.py",
        "test_optimistic_locking.py",
        "test_circuit_breaker.py",
        "test_processors.py",
    ]

    unit_patterns = [
        "test_models.py",
        "test_serializers.py",
        "test_validators.py",
        "test_managers.py",
        "test_signals.py",
        "test_adapters.py",
        "test_factories.py",
        "test_state_transitions.py",
        "test_locks.py",
        "test_media_asset.py",
    ]

    for item in items:
        # Skip if test already has unit/integration/e2e marker
        existing_markers = {m.name for m in item.iter_markers()}
        if existing_markers & {"unit", "integration", "e2e"}:
            continue

        filepath = str(item.fspath)
        filename = filepath.split("/")[-1]

        # Check patterns in priority order
        if any(pattern in filename for pattern in e2e_patterns):
            item.add_marker(pytest.mark.e2e)
        elif any(pattern in filename for pattern in integration_patterns):
            item.add_marker(pytest.mark.integration)
        elif any(pattern in filename for pattern in unit_patterns):
            item.add_marker(pytest.mark.unit)
        else:
            # Default: integration (safe for Django where most tests hit DB)
            item.add_marker(pytest.mark.integration)


@pytest.fixture(scope="session")
def django_db_setup():
    """Configure the test database for the session."""
    # Use the default database configuration from settings
    # PostgreSQL will be used as specified in docker-compose
    pass


@pytest.fixture(scope="session")
def django_db_modify_db_settings():
    """Allow database modifications for testing."""
    pass


def _patch_postgresql_flush_for_cascade():
    """
    Patch PostgreSQL flush to always use CASCADE.

    This fixes the "cannot truncate a table referenced in a foreign key constraint"
    error that occurs when TransactionTestCase tries to flush the database.

    Django's TransactionTestCase uses TRUNCATE to reset the database, but without
    CASCADE this fails when tables have foreign key constraints.
    """
    from django.db.backends.postgresql import operations

    original_sql_flush = operations.DatabaseOperations.sql_flush

    def sql_flush_with_cascade(
        self, style, tables, *, reset_sequences=False, allow_cascade=False
    ):
        # Force CASCADE for PostgreSQL to handle FK constraints
        return original_sql_flush(
            self, style, tables, reset_sequences=reset_sequences, allow_cascade=True
        )

    operations.DatabaseOperations.sql_flush = sql_flush_with_cascade


# Apply the patch when conftest is loaded
_patch_postgresql_flush_for_cascade()
