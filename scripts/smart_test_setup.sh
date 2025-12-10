#!/bin/bash

# =============================================================================
# SMART TEST DATABASE SETUP SCRIPT
# =============================================================================
# This script intelligently checks what's already set up and only runs
# necessary steps, making test runs much faster.
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
FORCE_SETUP=false
QUICK_CHECK=false
VERBOSE=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --force|-f)
            FORCE_SETUP=true
            shift
            ;;
        --quick|-q)
            QUICK_CHECK=true
            shift
            ;;
        --verbose|-v)
            VERBOSE=true
            shift
            ;;
        *)
            shift
            ;;
    esac
done

# Load test environment variables
export DJANGO_SETTINGS_MODULE=spot.app.settings_test

# Function to check if containers are running
check_containers() {
    if docker-compose -f docker-compose.test.yml ps | grep -q "spot-postgres-test.*Up" && \
       docker-compose -f docker-compose.test.yml ps | grep -q "spot-redis-test.*Up" && \
       docker-compose -f docker-compose.test.yml ps | grep -q "spot-web-test.*Up"; then
        return 0
    else
        return 1
    fi
}

# Function to check if pytest is installed
check_pytest() {
    docker-compose -f docker-compose.test.yml exec -T web-test python -c "import pytest" 2>/dev/null
    return $?
}

# Function to check if migrations are applied
check_migrations() {
    docker-compose -f docker-compose.test.yml exec -T web-test python spot/manage.py showmigrations --plan | grep -q "\[ \]" 2>/dev/null
    if [ $? -eq 0 ]; then
        return 1  # Found unapplied migrations
    else
        return 0  # All migrations applied
    fi
}

# Function to check if key fixtures are loaded
check_fixtures() {
    docker-compose -f docker-compose.test.yml exec -T web-test python spot/manage.py shell -c "
from spot.social.models import ProfileType, ProfileVisibility
from spot.user.models import Gender
import sys

# Check ProfileTypes
if ProfileType.objects.filter(id__in=['patron', 'driver', 'bartender']).count() != 3:
    sys.exit(1)

# Check ProfileVisibility
if ProfileVisibility.objects.filter(id__in=['public', 'connections', 'none']).count() != 3:
    sys.exit(1)

# Check Genders
if Gender.objects.filter(id__in=['M', 'F', 'O']).count() != 3:
    sys.exit(1)

sys.exit(0)
" 2>/dev/null
    return $?
}

# Function to start containers if needed
start_containers() {
    print_status "Starting test containers..."
    docker-compose -f docker-compose.test.yml up -d postgres-test redis-test

    # Wait for PostgreSQL to be ready
    for i in {1..10}; do
        if docker-compose -f docker-compose.test.yml exec -T postgres-test pg_isready -U spot_test_user -d spot_test_db &>/dev/null; then
            break
        fi
        echo -n "."
        sleep 1
    done
    echo

    # Start web-test container
    docker-compose -f docker-compose.test.yml up -d web-test
    sleep 2  # Give container time to start
}

# Function to install dependencies
install_dependencies() {
    print_status "Installing Python dependencies..."
    docker-compose -f docker-compose.test.yml exec -T web-test pip install -q -r requirements.txt 2>/dev/null || true
    docker-compose -f docker-compose.test.yml exec -T web-test pip install -q -r requirements-test.txt
}

# Function to run migrations
run_migrations() {
    print_status "Running database migrations..."
    docker-compose -f docker-compose.test.yml exec -T web-test python spot/manage.py migrate --no-input
}

# Function to load fixtures
load_fixtures() {
    print_status "Loading fixture data..."
    docker-compose -f docker-compose.test.yml exec -T web-test python spot/manage.py load_all_fixtures --skip-missing 2>/dev/null || true

    # Ensure critical data exists (fallback)
    docker-compose -f docker-compose.test.yml exec -T web-test python spot/manage.py shell << 'EOF' 2>/dev/null || true
from spot.social.models import ProfileType, ProfileVisibility
from spot.user.models import Gender

# Create ProfileTypes if missing
for pt_id, pt_name in [('patron', 'Patron'), ('driver', 'Driver'), ('bartender', 'Bartender')]:
    ProfileType.objects.get_or_create(id=pt_id, defaults={'name': pt_name})

# Create ProfileVisibility if missing
for pv_id, pv_name in [('public', 'Public'), ('connections', 'Connections'), ('none', 'None')]:
    ProfileVisibility.objects.get_or_create(id=pv_id, defaults={'name': pv_name})

# Create Genders if missing
for g_id, g_name in [('M', 'Male'), ('F', 'Female'), ('O', 'Other')]:
    Gender.objects.get_or_create(id=g_id, defaults={'name': g_name})
EOF
}

# Main execution
main() {
    local needs_setup=false
    local setup_reasons=()

    if [ "$FORCE_SETUP" = true ]; then
        print_warning "Force setup requested - will run full setup"
        needs_setup=true
        setup_reasons+=("Force flag specified")
    else
        # Quick check mode - just ensure containers are running
        if [ "$QUICK_CHECK" = true ]; then
            if ! check_containers; then
                print_warning "Containers not running - starting them..."
                start_containers
            else
                print_success "Test environment ready (quick check)"
            fi
            return 0
        fi

        # Full intelligent check
        print_status "Checking test environment status..."

        # Check containers
        if ! check_containers; then
            needs_setup=true
            setup_reasons+=("Containers not running")
        elif [ "$VERBOSE" = true ]; then
            print_success "✓ Containers are running"
        fi

        # If containers are running, check other requirements
        if check_containers; then
            # Check pytest
            if ! check_pytest; then
                needs_setup=true
                setup_reasons+=("pytest not installed")
            elif [ "$VERBOSE" = true ]; then
                print_success "✓ pytest is installed"
            fi

            # Check migrations
            if ! check_migrations; then
                needs_setup=true
                setup_reasons+=("Migrations not applied")
            elif [ "$VERBOSE" = true ]; then
                print_success "✓ Migrations are up to date"
            fi

            # Check fixtures
            if ! check_fixtures; then
                needs_setup=true
                setup_reasons+=("Fixtures not loaded")
            elif [ "$VERBOSE" = true ]; then
                print_success "✓ Fixtures are loaded"
            fi
        fi
    fi

    # Run setup if needed
    if [ "$needs_setup" = true ]; then
        print_warning "Setup needed. Reasons:"
        for reason in "${setup_reasons[@]}"; do
            echo "  • $reason"
        done
        echo

        # Start containers if needed
        if ! check_containers; then
            start_containers
        fi

        # Install dependencies if needed
        if ! check_pytest; then
            install_dependencies
        fi

        # Run migrations if needed
        if ! check_migrations; then
            run_migrations
        fi

        # Load fixtures if needed
        if ! check_fixtures; then
            load_fixtures
        fi

        print_success "Test environment setup complete!"
    else
        print_success "Test environment is ready - no setup needed!"
    fi

    # Final verification
    if [ "$VERBOSE" = true ]; then
        echo
        print_status "Final verification:"
        check_containers && echo "  ✓ Containers: Running"
        check_pytest && echo "  ✓ Dependencies: Installed"
        check_migrations && echo "  ✓ Migrations: Applied"
        check_fixtures && echo "  ✓ Fixtures: Loaded"
    fi
}

# Run main function
main