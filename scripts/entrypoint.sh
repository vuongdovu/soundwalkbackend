#!/bin/bash
# =============================================================================
# Docker Container Entrypoint Script
# =============================================================================
# This script runs when the Django container starts. It:
# 1. Waits for the database to be ready
# 2. Runs database migrations
# 3. Collects static files
# 4. Starts the application server
#
# Usage:
#   ./entrypoint.sh [command]
#
# If a command is provided, it runs that command after initialization.
# If no command is provided, it starts Uvicorn.
# =============================================================================

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# -----------------------------------------------------------------------------
# Wait for Database
# -----------------------------------------------------------------------------
# Waits for PostgreSQL to be ready before proceeding.
# This is important because docker-compose depends_on doesn't wait for
# the database to be accepting connections.

wait_for_db() {
    log_info "Waiting for database..."

    local max_attempts=30
    local attempt=1

    while [ $attempt -le $max_attempts ]; do
        # Try to connect to the database
        if python -c "
import os
import sys
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()
from django.db import connection
try:
    connection.ensure_connection()
    sys.exit(0)
except Exception as e:
    print(f'Database not ready: {e}')
    sys.exit(1)
" 2>/dev/null; then
            log_info "Database is ready!"
            return 0
        fi

        log_warn "Database not ready (attempt $attempt/$max_attempts)..."
        sleep 2
        attempt=$((attempt + 1))
    done

    log_error "Database connection failed after $max_attempts attempts"
    return 1
}

# -----------------------------------------------------------------------------
# Run Migrations
# -----------------------------------------------------------------------------
# Applies any pending database migrations.
# This ensures the database schema is up to date.

run_migrations() {
    log_info "Running database migrations..."
    python manage.py migrate --noinput
    log_info "Migrations complete!"
}

# -----------------------------------------------------------------------------
# Collect Static Files
# -----------------------------------------------------------------------------
# Collects static files into STATIC_ROOT for serving by nginx.
# Only runs if STATIC_ROOT is configured.

collect_static() {
    log_info "Collecting static files..."
    python manage.py collectstatic --noinput --clear
    log_info "Static files collected!"
}

# -----------------------------------------------------------------------------
# Create Superuser (Optional)
# -----------------------------------------------------------------------------
# Creates a superuser if environment variables are set.
# Useful for initial setup in development/staging.

create_superuser() {
    if [ -n "$DJANGO_SUPERUSER_EMAIL" ] && [ -n "$DJANGO_SUPERUSER_PASSWORD" ]; then
        log_info "Creating superuser..."
        python manage.py createsuperuser --noinput --email "$DJANGO_SUPERUSER_EMAIL" || true
        log_info "Superuser creation attempted (may already exist)"
    fi
}

# -----------------------------------------------------------------------------
# Main Entrypoint
# -----------------------------------------------------------------------------

main() {
    log_info "Starting Django application entrypoint..."

    # Wait for database to be ready
    wait_for_db

    # Run migrations
    run_migrations

    # Collect static files (production)
    if [ "$COLLECT_STATIC" != "false" ]; then
        collect_static
    fi

    # Create superuser if configured
    create_superuser

    log_info "Initialization complete!"

    # Execute the provided command or default to uvicorn
    if [ $# -eq 0 ]; then
        log_info "Starting Uvicorn server..."
        exec uvicorn config.asgi:application --host 0.0.0.0 --port 8080
    else
        log_info "Executing command: $@"
        exec "$@"
    fi
}

# Run main function with all script arguments
main "$@"
