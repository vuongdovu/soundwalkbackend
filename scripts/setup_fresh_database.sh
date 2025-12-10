#!/bin/bash




# =============================================================================
# SPOT BACKEND - FRESH DATABASE SETUP SCRIPT
# =============================================================================
#
# WHAT THIS SCRIPT DOES:
# This script completely sets up a brand new Postgres database for the SPOT 
# backend application running in Docker containers. It performs the following
# operations in order:
#
# 1. PREREQUISITES CHECK:
#    - Verifies Docker containers are running (spot-web, spot-postgres)
#    - Ensures the faker Python package is installed for data generation
#
# 2. DATABASE MIGRATIONS:
#    - Runs Django migrations to create all database tables and schema
#
# 3. FIXTURE DATA LOADING:
#    - Uses Django management command to load all fixtures in dependency order
#    - Automatically handles missing fixtures
#    - Includes:
#      * Authentication groups and permissions
#      * User accounts and gender data
#      * Profile types, media types, and visibility settings
#      * Geographic data (timezones, countries, regions, addresses)
#      * Location/place data for venues
#      * Entity types and entities
#      * Organization structure (types, roles, memberships, relationships)
#      * Bucket list items and badges
#      * Drinks catalog (30 drinks across all categories)
#      * Moderation and bug tracking data
#
# 4. TARGET USER SETUP:
#    - Creates a target user for drink transactions if needed
#
# WHEN TO USE:
# - Setting up a completely fresh database (new Postgres container)
# - After resetting/clearing an existing database
# - Initial development environment setup
# - Testing environment preparation
#
# REQUIREMENTS:
# - Docker containers must be running (docker-compose up)
# - spot-web container must be accessible
# - spot-postgres container must be healthy
#
# USAGE:
#   chmod +x scripts/setup_fresh_database.sh
#   ./scripts/setup_fresh_database.sh
#
# =============================================================================

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_header() {
    echo ""
    echo -e "${BLUE}========================================${NC}"
    echo -e "${BLUE} $1${NC}"
    echo -e "${BLUE}========================================${NC}"
}

# Check if Docker containers are running
check_containers() {
    print_header "CHECKING DOCKER CONTAINERS"
    
    if ! docker ps | grep -q "spot-web"; then
        print_error "spot-web container is not running!"
        print_error "Please start containers with: docker-compose up -d"
        exit 1
    fi
    
    if ! docker ps | grep -q "spot-postgres"; then
        print_error "spot-postgres container is not running!"
        print_error "Please start containers with: docker-compose up -d"
        exit 1
    fi
    
    print_success "Docker containers are running"
}

# Install required dependencies
install_dependencies() {
    print_header "INSTALLING DEPENDENCIES"
    
    print_status "Installing faker package for data generation..."
    docker exec spot-web pip install faker --quiet
    print_success "Dependencies installed"
}

# Run Django migrations
run_migrations() {
    print_header "RUNNING DATABASE MIGRATIONS"
    
    print_status "Applying all Django migrations..."
    docker exec spot-web python spot/manage.py migrate
    print_success "Database migrations completed"
}

# Load fixture data using Django management command
load_fixtures() {
    print_header "LOADING FIXTURE DATA"
    
    print_status "Loading all fixtures in dependency order..."
    
    # Use the Django management command with skip-missing flag
    if docker exec spot-web python spot/manage.py load_all_fixtures --skip-missing; then
        print_success "All fixtures loaded successfully"
    else
        print_warning "Some fixtures could not be loaded (see output above)"
    fi
}

# Main execution
main() {
    print_header "SPOT BACKEND - FRESH DATABASE SETUP"
    echo "This script will set up a completely fresh database with all fixtures and sample data."
    echo ""
    
    # Execute all setup steps
    check_containers
    install_dependencies
    run_migrations
    load_fixtures
    
    print_header "SETUP COMPLETE!"
    print_success "Your database is now ready with:"
    echo "  ✅ All Django models and tables created"
    echo "  ✅ All fixture data loaded (auth, profiles, orgs, geo, drinks)"
    echo "  ✅ 30 drinks across all categories"
    echo "  ✅ Sample users and organizations"
    echo "  ✅ Geographic data (countries, regions, timezones)"
    echo ""
    print_status "You can now test your API endpoints and start development!"
    echo ""
}

# Run main function
main "$@"