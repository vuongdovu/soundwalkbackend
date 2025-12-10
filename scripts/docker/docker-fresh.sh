#!/bin/bash

# docker-fresh.sh - Complete fresh start with clean database and containers
# Usage: ./docker-fresh.sh [--keep-images] [--no-fixtures]

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
NC='\033[0m' # No Color

# Default values
KEEP_IMAGES=false
NO_FIXTURES=false

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --keep-images)
            KEEP_IMAGES=true
            shift
            ;;
        --no-fixtures)
            NO_FIXTURES=true
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [--keep-images] [--no-fixtures]"
            echo "  --keep-images   Don't remove Docker images (faster rebuilds)"
            echo "  --no-fixtures   Skip loading database fixtures"
            echo "  -h, --help      Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

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

print_step() {
    echo -e "${PURPLE}[STEP]${NC} $1"
}

# Function to show confirmation prompt
confirm_action() {
    print_warning "This will completely reset your development environment:"
    echo "  - Stop all containers"
    echo "  - Remove all containers and volumes"
    echo "  - Reset database (all data will be lost)"
    if [ "$KEEP_IMAGES" = false ]; then
        echo "  - Remove Docker images"
    fi
    echo "  - Rebuild and restart everything"
    echo
    
    read -p "Are you sure you want to continue? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        print_status "Operation cancelled"
        exit 0
    fi
}

# Function to stop all containers
stop_containers() {
    print_step "Stopping all containers..."
    
    # Stop docker-compose services
    docker-compose down --volumes --remove-orphans 2>/dev/null || true
    
    # Stop any running containers related to the project
    CONTAINERS=$(docker ps -q --filter "name=spot-")
    if [ ! -z "$CONTAINERS" ]; then
        docker stop $CONTAINERS 2>/dev/null || true
    fi
    
    print_success "All containers stopped"
}

# Function to remove containers and volumes
remove_containers_volumes() {
    print_step "Removing containers and volumes..."
    
    # Remove containers
    docker-compose rm -f 2>/dev/null || true
    
    # Remove project-specific containers
    CONTAINERS=$(docker ps -aq --filter "name=spot-")
    if [ ! -z "$CONTAINERS" ]; then
        docker rm -f $CONTAINERS 2>/dev/null || true
    fi
    
    # Remove volumes
    docker volume prune -f 2>/dev/null || true
    
    # Remove project-specific volumes
    VOLUMES=$(docker volume ls -q --filter "name=spot-")
    if [ ! -z "$VOLUMES" ]; then
        docker volume rm $VOLUMES 2>/dev/null || true
    fi
    
    print_success "Containers and volumes removed"
}

# Function to remove images
remove_images() {
    if [ "$KEEP_IMAGES" = true ]; then
        print_status "Keeping Docker images (--keep-images flag)"
        return 0
    fi
    
    print_step "Removing Docker images..."
    
    # Remove project images
    docker-compose down --rmi all 2>/dev/null || true
    
    # Remove dangling images
    DANGLING=$(docker images -f "dangling=true" -q)
    if [ ! -z "$DANGLING" ]; then
        docker rmi $DANGLING 2>/dev/null || true
    fi
    
    # Remove project-specific images
    IMAGES=$(docker images --format "{{.Repository}}:{{.Tag}}" | grep "spot-backend")
    if [ ! -z "$IMAGES" ]; then
        echo "$IMAGES" | xargs docker rmi 2>/dev/null || true
    fi
    
    print_success "Docker images removed"
}

# Function to clean up system
cleanup_system() {
    print_step "Cleaning up Docker system..."
    
    # Clean up system (this removes unused data)
    docker system prune -f 2>/dev/null || true
    
    # Clean up networks
    docker network prune -f 2>/dev/null || true
    
    print_success "System cleanup completed"
}

# Function to rebuild services
rebuild_services() {
    print_step "Rebuilding services..."
    
    # Build with no cache to ensure fresh build
    docker-compose build --no-cache --parallel
    
    if [ $? -eq 0 ]; then
        print_success "Services rebuilt successfully"
        return 0
    else
        print_error "Failed to rebuild services"
        return 1
    fi
}

# Function to start services
start_services() {
    print_step "Starting services..."
    
    # Start services
    docker-compose up -d
    
    if [ $? -eq 0 ]; then
        print_success "Services started successfully"
        return 0
    else
        print_error "Failed to start services"
        return 1
    fi
}

# Function to wait for services to be ready
wait_for_services() {
    print_step "Waiting for services to be ready..."
    
    local services=("redis" "web")
    local max_wait=90  # Longer wait for fresh start
    local wait_time=0
    
    for service in "${services[@]}"; do
        print_status "Checking $service..."
        
        while [ $wait_time -lt $max_wait ]; do
            if docker-compose exec -T $service echo "ready" >/dev/null 2>&1; then
                print_success "$service is ready"
                break
            fi
            
            sleep 3
            wait_time=$((wait_time + 3))
            echo -n "."
        done
        
        if [ $wait_time -ge $max_wait ]; then
            print_warning "$service may not be ready (timeout)"
        fi
        
        wait_time=0
        echo # New line after dots
    done
}

# Function to setup fresh database
setup_fresh_database() {
    print_step "Setting up fresh database..."
    
    # Wait a bit more for database to be ready
    sleep 5
    
    # Create database tables
    print_status "Creating database tables..."
    docker-compose exec -T web python spot/manage.py migrate --noinput
    
    if [ $? -eq 0 ]; then
        print_success "Database tables created"
    else
        print_error "Failed to create database tables"
        return 1
    fi
    
    # Create superuser if needed
    print_status "Creating superuser..."
    docker-compose exec -T web python spot/manage.py shell -c "
from django.contrib.auth import get_user_model
User = get_user_model()
if not User.objects.filter(username='admin').exists():
    User.objects.create_superuser('admin', 'admin@example.com', 'admin123')
    print('Superuser created: admin/admin123')
else:
    print('Superuser already exists')
" 2>/dev/null || print_warning "Superuser creation skipped"
    
    return 0
}

# Function to load fixtures
load_fixtures() {
    if [ "$NO_FIXTURES" = true ]; then
        print_status "Skipping fixtures (--no-fixtures flag)"
        return 0
    fi
    
    print_step "Loading database fixtures..."
    
    # Look for fixture files
    local fixture_dirs=("spot/*/fixtures" "spot/fixtures")
    local fixtures_found=false
    
    for dir in "${fixture_dirs[@]}"; do
        if [ -d "$dir" ]; then
            for fixture in "$dir"/*.json; do
                if [ -f "$fixture" ]; then
                    print_status "Loading fixture: $fixture"
                    docker-compose exec -T web python spot/manage.py loaddata "$fixture" 2>/dev/null || print_warning "Failed to load $fixture"
                    fixtures_found=true
                fi
            done
        fi
    done
    
    if [ "$fixtures_found" = false ]; then
        print_warning "No fixtures found to load"
    else
        print_success "Fixtures loaded"
    fi
}

# Function to verify fresh environment
verify_environment() {
    print_step "Verifying fresh environment..."
    
    # Check service health
    local services=("redis" "web")
    local all_healthy=true
    
    for service in "${services[@]}"; do
        if docker-compose exec -T $service echo "healthy" >/dev/null 2>&1; then
            print_success "$service is healthy"
        else
            print_warning "$service health check failed"
            all_healthy=false
        fi
    done
    
    # Check database connectivity
    if docker-compose exec -T web python spot/manage.py check --database default >/dev/null 2>&1; then
        print_success "Database connectivity verified"
    else
        print_warning "Database connectivity check failed"
        all_healthy=false
    fi
    
    # Check Redis connectivity
    if docker-compose exec -T redis redis-cli ping >/dev/null 2>&1; then
        print_success "Redis connectivity verified"
    else
        print_warning "Redis connectivity check failed"
        all_healthy=false
    fi
    
    if [ "$all_healthy" = true ]; then
        print_success "Environment verification passed"
    else
        print_warning "Some health checks failed - environment may not be fully ready"
    fi
}

# Function to show final status
show_final_status() {
    print_step "Fresh Environment Status:"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    # Show container status
    docker-compose ps
    
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    # Show service URLs
    print_status "Service URLs:"
    echo "  Django API: http://localhost:8080"
    echo "  Nginx Proxy: http://localhost:80"
    echo "  Redis: localhost:6379"
    echo "  Admin Panel: http://localhost:8080/admin/ (admin/admin123)"
    
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    # Show disk usage
    print_status "Docker disk usage:"
    docker system df
    
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

# Main execution
main() {
    print_status "Starting fresh environment setup..."
    
    # Change to project root
    cd "$(dirname "$0")/../.."
    
    # Show confirmation
    confirm_action
    
    print_status "Beginning fresh setup process..."
    
    # Stop everything
    stop_containers
    
    # Remove containers and volumes
    remove_containers_volumes
    
    # Remove images if requested
    remove_images
    
    # System cleanup
    cleanup_system
    
    # Rebuild services
    if ! rebuild_services; then
        exit 1
    fi
    
    # Start services
    if ! start_services; then
        exit 1
    fi
    
    # Wait for services
    wait_for_services
    
    # Setup fresh database
    if ! setup_fresh_database; then
        exit 1
    fi
    
    # Load fixtures
    load_fixtures

    # Verify environment
    verify_environment
    
    # Show final status
    show_final_status
    
    print_success "Fresh environment setup completed successfully!"
    print_status "Your development environment is now completely fresh and ready to use"
    print_status "All previous data has been cleared and replaced with fresh fixtures"
    print_status "Run 'docker-compose logs -f' to monitor the services"
}

# Run main function
main "$@"