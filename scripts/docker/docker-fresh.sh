#!/bin/bash

# =============================================================================
# Docker Fresh Start Script
# =============================================================================
# Complete fresh start with clean database and containers
# Usage: ./scripts/docker/docker-fresh.sh [--keep-images]
# =============================================================================

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default values
KEEP_IMAGES=false

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --keep-images)
            KEEP_IMAGES=true
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [--keep-images]"
            echo "  --keep-images   Don't remove Docker images (faster rebuilds)"
            echo "  -h, --help      Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Helper functions
print_status() { echo -e "${BLUE}[INFO]${NC} $1"; }
print_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; }
print_step() { echo -e "${BLUE}==>${NC} $1"; }

# Confirmation prompt
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

# Stop all containers
stop_containers() {
    print_step "Stopping all containers..."
    docker-compose down --volumes --remove-orphans 2>/dev/null || true

    CONTAINERS=$(docker ps -q --filter "name=app-")
    if [ ! -z "$CONTAINERS" ]; then
        docker stop $CONTAINERS 2>/dev/null || true
    fi

    print_success "All containers stopped"
}

# Remove containers and volumes
remove_containers_volumes() {
    print_step "Removing containers and volumes..."

    docker-compose rm -f 2>/dev/null || true

    CONTAINERS=$(docker ps -aq --filter "name=app-")
    if [ ! -z "$CONTAINERS" ]; then
        docker rm -f $CONTAINERS 2>/dev/null || true
    fi

    docker volume prune -f 2>/dev/null || true

    VOLUMES=$(docker volume ls -q --filter "name=app-")
    if [ ! -z "$VOLUMES" ]; then
        docker volume rm $VOLUMES 2>/dev/null || true
    fi

    print_success "Containers and volumes removed"
}

# Remove images
remove_images() {
    if [ "$KEEP_IMAGES" = true ]; then
        print_status "Keeping Docker images (--keep-images flag)"
        return 0
    fi

    print_step "Removing Docker images..."

    docker-compose down --rmi all 2>/dev/null || true

    DANGLING=$(docker images -f "dangling=true" -q)
    if [ ! -z "$DANGLING" ]; then
        docker rmi $DANGLING 2>/dev/null || true
    fi

    print_success "Docker images removed"
}

# Cleanup system
cleanup_system() {
    print_step "Cleaning up Docker system..."
    docker system prune -f 2>/dev/null || true
    docker network prune -f 2>/dev/null || true
    print_success "System cleanup completed"
}

# Rebuild services
rebuild_services() {
    print_step "Rebuilding services..."

    docker-compose build --no-cache --parallel

    if [ $? -eq 0 ]; then
        print_success "Services rebuilt successfully"
        return 0
    else
        print_error "Failed to rebuild services"
        return 1
    fi
}

# Start services
start_services() {
    print_step "Starting services..."

    docker-compose up -d

    if [ $? -eq 0 ]; then
        print_success "Services started successfully"
        return 0
    else
        print_error "Failed to start services"
        return 1
    fi
}

# Wait for services
wait_for_services() {
    print_step "Waiting for services to be ready..."

    local max_wait=60
    local wait_time=0

    while [ $wait_time -lt $max_wait ]; do
        if docker-compose exec -T web echo "ready" >/dev/null 2>&1; then
            print_success "Services are ready"
            return 0
        fi

        sleep 3
        wait_time=$((wait_time + 3))
        echo -n "."
    done

    print_warning "Services may not be fully ready (timeout)"
}

# Setup fresh database
setup_fresh_database() {
    print_step "Setting up fresh database..."

    sleep 5

    print_status "Running database migrations..."
    docker-compose exec -T web python manage.py migrate --noinput

    if [ $? -eq 0 ]; then
        print_success "Database migrations completed"
    else
        print_error "Failed to run migrations"
        return 1
    fi

    return 0
}

# Show final status
show_final_status() {
    print_step "Fresh Environment Status:"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    docker-compose ps

    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    print_status "Service URLs:"
    echo "  Application: http://localhost (via nginx)"
    echo "  Django Direct: http://localhost:8080"
    echo "  Health Check: http://localhost/health/"

    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

# Main execution
main() {
    print_status "Starting fresh environment setup..."

    cd "$(dirname "$0")/../.."

    confirm_action

    print_status "Beginning fresh setup process..."

    stop_containers
    remove_containers_volumes
    remove_images
    cleanup_system

    if ! rebuild_services; then
        exit 1
    fi

    if ! start_services; then
        exit 1
    fi

    wait_for_services

    if ! setup_fresh_database; then
        exit 1
    fi

    show_final_status

    print_success "Fresh environment setup completed successfully!"
    print_status "Run 'docker-compose logs -f' to monitor the services"
}

main "$@"
