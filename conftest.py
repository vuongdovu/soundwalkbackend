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
