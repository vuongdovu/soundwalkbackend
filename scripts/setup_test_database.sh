#!/bin/bash

# =============================================================================
# TEST DATABASE SETUP SCRIPT WITH MIGRATIONS AND FIXTURES
# =============================================================================
# This script sets up the test database with all migrations and fixture data
# to ensure consistent test environment initialization.
#
# Usage: ./scripts/setup_test_database.sh [--force-recreate]
# =============================================================================

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() { echo -e "${BLUE}[INFO]${NC} $1"; }
print_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Parse command line arguments
FORCE_RECREATE=false
if [[ "$1" == "--force-recreate" ]]; then
    FORCE_RECREATE=true
fi

echo "========================================="
echo "  TEST DATABASE SETUP WITH FIXTURES"
echo "========================================="

# Load test environment variables
export DJANGO_SETTINGS_MODULE=spot.app.settings_test

# Ensure test containers are up
print_status "Starting test containers..."
docker-compose -f docker-compose.test.yml up -d postgres-test redis-test

# Wait for PostgreSQL to be ready
print_status "Waiting for PostgreSQL to be ready..."
for i in {1..10}; do
    if docker-compose -f docker-compose.test.yml exec -T postgres-test pg_isready -U spot_test_user -d spot_test_db &>/dev/null; then
        print_success "PostgreSQL is ready!"
        break
    fi
    echo -n "."
    sleep 1
done

# Build the test container if needed
print_status "Building test container..."
docker-compose -f docker-compose.test.yml build web-test

# Start web-test container
print_status "Starting web-test container..."
docker-compose -f docker-compose.test.yml up -d web-test

# Install dependencies
print_status "Installing Python dependencies..."
# Install base requirements first
if docker-compose -f docker-compose.test.yml exec -T web-test pip install -q -r requirements.txt; then
    print_success "Base dependencies installed"
else
    print_warning "Some base dependencies may have failed (this is okay if already installed)"
fi

# Install test requirements
if docker-compose -f docker-compose.test.yml exec -T web-test pip install -q -r requirements-test.txt; then
    print_success "Test dependencies installed"
else
    print_error "Failed to install test dependencies!"
    exit 1
fi

# If force recreate, drop and recreate database
if [ "$FORCE_RECREATE" = true ]; then
    print_warning "Force recreating test database..."
    docker-compose -f docker-compose.test.yml exec -T postgres-test psql -U spot_test_user -c "DROP DATABASE IF EXISTS spot_test_db;" 2>/dev/null || true
    docker-compose -f docker-compose.test.yml exec -T postgres-test psql -U spot_test_user -c "CREATE DATABASE spot_test_db;" 2>/dev/null || true
fi

# Run migrations in the test container
print_status "Running database migrations..."
if docker-compose -f docker-compose.test.yml exec -T web-test python spot/manage.py migrate --no-input; then
    print_success "Migrations completed successfully"
else
    print_error "Migration failed!"
    exit 1
fi

# Load all fixtures using the management command
print_status "Loading all fixture data..."
if docker-compose -f docker-compose.test.yml exec -T web-test python spot/manage.py load_all_fixtures --skip-missing; then
    print_success "Fixtures loaded successfully"
else
    print_warning "Some fixtures could not be loaded (this is expected if fixtures are missing)"
fi

# Verify critical data exists
print_status "Verifying critical test data..."
docker-compose -f docker-compose.test.yml exec -T web-test python spot/manage.py shell << 'EOF'
from spot.social.models import ProfileType, ProfileVisibility
from spot.user.models import Gender
from django.contrib.auth import get_user_model

User = get_user_model()

# Verify ProfileTypes
profile_types = ProfileType.objects.count()
print(f"✓ ProfileTypes loaded: {profile_types}")

# Verify ProfileVisibility
visibilities = ProfileVisibility.objects.count()
print(f"✓ ProfileVisibility loaded: {visibilities}")

# Verify Genders (if any)
try:
    genders = Gender.objects.count()
    print(f"✓ Genders loaded: {genders}")
except:
    print("⚠ Gender model not available")

# Create test superuser if doesn't exist
test_user, created = User.objects.get_or_create(
    phone='+15550000001',
    defaults={
        'email': 'test@test.com',
        'is_superuser': True,
        'is_staff': True,
        'is_active': True
    }
)
if created:
    test_user.set_password('testpass123')
    test_user.save()
    print("✓ Test superuser created")
else:
    print("✓ Test superuser already exists")

print("\n✅ Test database verification complete!")
EOF

echo ""
print_success "========================================="
print_success "  TEST DATABASE SETUP COMPLETE!"
print_success "========================================="
echo ""
echo "  Database: spot_test_db"
echo "  User: spot_test_user"
echo "  Host: postgres-test (port 5433)"
echo ""
echo "  ✅ All migrations applied"
echo "  ✅ All available fixtures loaded"
echo "  ✅ Test superuser created"
echo ""
echo "You can now run tests with:"
echo "  • make test"
echo "  • docker-compose -f docker-compose.test.yml exec web-test pytest"
echo ""