#!/bin/bash

# docker-utils.sh - Utility functions for Docker container management
# Usage: ./docker-utils.sh [action] [options]

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
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

print_step() {
    echo -e "${PURPLE}[STEP]${NC} $1"
}

# Function to show help
show_help() {
    echo "Usage: $0 [action] [options]"
    echo ""
    echo "Actions:"
    echo "  health              Check health of all services"
    echo "  restart [service]   Restart a service or all services"
    echo "  shell [service]     Open shell in service container"
    echo "  django [command]    Run Django management command"
    echo "  db-backup           Backup database"
    echo "  db-restore [file]   Restore database from backup"
    echo "  redis-cli           Open Redis CLI"
    echo "  redis-flush         Flush Redis cache"
    echo "  cleanup             Clean up unused containers and images"
    echo "  stats               Show container resource usage"
    echo "  network             Show network information"
    echo "  volumes             Show volume information"
    echo "  update              Update all containers"
    echo "  help                Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 health                    # Check all service health"
    echo "  $0 restart web               # Restart web service"
    echo "  $0 shell web                 # Open shell in web container"
    echo "  $0 django migrate            # Run Django migrations"
    echo "  $0 db-backup                 # Backup database"
    echo "  $0 redis-flush               # Clear Redis cache"
    echo "  $0 cleanup                   # Clean up Docker system"
}

# Function to validate environment
validate_environment() {
    if ! docker info >/dev/null 2>&1; then
        print_error "Docker is not running"
        return 1
    fi
    
    if ! command -v docker-compose >/dev/null 2>&1; then
        print_error "docker-compose not found"
        return 1
    fi
    
    if [ ! -f "docker-compose.yaml" ]; then
        print_error "docker-compose.yaml not found"
        return 1
    fi
    
    return 0
}

# Function to check service health
check_health() {
    print_step "Checking service health..."
    
    local services=("redis" "web" "celery-worker" "celery-beat" "nginx")
    local healthy_count=0
    local total_count=0
    
    for service in "${services[@]}"; do
        total_count=$((total_count + 1))
        local container_id=$(docker-compose ps -q "$service" 2>/dev/null)
        
        if [ -n "$container_id" ]; then
            local status=$(docker inspect --format='{{.State.Status}}' "$container_id" 2>/dev/null)
            
            case $status in
                "running")
                    # Additional health checks
                    case $service in
                        "redis")
                            if docker-compose exec -T redis redis-cli ping >/dev/null 2>&1; then
                                print_success "$service: healthy (responding to ping)"
                                healthy_count=$((healthy_count + 1))
                            else
                                print_warning "$service: running but not responding"
                            fi
                            ;;
                        "web")
                            if docker-compose exec -T web python manage.py check >/dev/null 2>&1; then
                                print_success "$service: healthy (Django check passed)"
                                healthy_count=$((healthy_count + 1))
                            else
                                print_warning "$service: running but Django check failed"
                            fi
                            ;;
                        *)
                            print_success "$service: healthy (running)"
                            healthy_count=$((healthy_count + 1))
                            ;;
                    esac
                    ;;
                "exited")
                    print_error "$service: exited"
                    ;;
                "restarting")
                    print_warning "$service: restarting"
                    ;;
                *)
                    print_warning "$service: unknown status ($status)"
                    ;;
            esac
        else
            print_error "$service: not running"
        fi
    done
    
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    print_status "Health Summary: $healthy_count/$total_count services healthy"
    
    if [ $healthy_count -eq $total_count ]; then
        print_success "All services are healthy"
        return 0
    else
        print_warning "Some services are not healthy"
        return 1
    fi
}

# Function to restart services
restart_service() {
    local service="$1"
    
    if [ -z "$service" ]; then
        print_step "Restarting all services..."
        docker-compose restart
        print_success "All services restarted"
    else
        print_step "Restarting service: $service"
        if docker-compose restart "$service"; then
            print_success "Service $service restarted"
        else
            print_error "Failed to restart service $service"
            return 1
        fi
    fi
}

# Function to open shell in container
open_shell() {
    local service="$1"
    
    if [ -z "$service" ]; then
        service="web"
    fi
    
    print_step "Opening shell in $service container..."
    
    if docker-compose exec "$service" /bin/bash; then
        print_success "Shell session ended"
    else
        print_warning "Bash not available, trying sh..."
        if docker-compose exec "$service" /bin/sh; then
            print_success "Shell session ended"
        else
            print_error "Failed to open shell in $service"
            return 1
        fi
    fi
}

# Function to run Django management commands
run_django_command() {
    local command="$*"
    
    if [ -z "$command" ]; then
        print_error "No Django command specified"
        return 1
    fi
    
    print_step "Running Django command: $command"

    if docker-compose exec web python manage.py $command; then
        print_success "Django command completed"
    else
        print_error "Django command failed"
        return 1
    fi
}

# Function to backup database
backup_database() {
    local timestamp=$(date +"%Y%m%d_%H%M%S")
    local backup_dir="backups"
    local backup_file="$backup_dir/database_backup_$timestamp.sql"
    
    print_step "Creating database backup..."
    
    mkdir -p "$backup_dir"
    
    # Check if we're using SQLite or PostgreSQL
    if docker-compose exec -T web python manage.py shell -c "from django.db import connection; print(connection.vendor)" 2>/dev/null | grep -q "sqlite"; then
        # SQLite backup
        print_status "Backing up SQLite database..."
        if docker-compose exec -T web python manage.py dumpdata --natural-foreign --natural-primary > "$backup_file.json"; then
            print_success "Database backup created: $backup_file.json"
        else
            print_error "Failed to create database backup"
            return 1
        fi
    else
        # PostgreSQL backup (if configured)
        print_status "Backing up PostgreSQL database..."
        if docker-compose exec -T web pg_dump -U postgres > "$backup_file"; then
            print_success "Database backup created: $backup_file"
        else
            print_error "Failed to create database backup"
            return 1
        fi
    fi
}

# Function to restore database
restore_database() {
    local backup_file="$1"
    
    if [ -z "$backup_file" ]; then
        print_error "No backup file specified"
        return 1
    fi
    
    if [ ! -f "$backup_file" ]; then
        print_error "Backup file not found: $backup_file"
        return 1
    fi
    
    print_step "Restoring database from: $backup_file"
    print_warning "This will replace all existing data!"
    
    read -p "Are you sure you want to continue? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        print_status "Restore cancelled"
        return 0
    fi
    
    if [[ "$backup_file" == *.json ]]; then
        # Django fixture restore
        if docker-compose exec -T web python manage.py loaddata - < "$backup_file"; then
            print_success "Database restored from $backup_file"
        else
            print_error "Failed to restore database"
            return 1
        fi
    else
        # SQL restore
        if docker-compose exec -T web psql -U postgres < "$backup_file"; then
            print_success "Database restored from $backup_file"
        else
            print_error "Failed to restore database"
            return 1
        fi
    fi
}

# Function to open Redis CLI
open_redis_cli() {
    print_step "Opening Redis CLI..."
    
    if docker-compose exec redis redis-cli; then
        print_success "Redis CLI session ended"
    else
        print_error "Failed to open Redis CLI"
        return 1
    fi
}

# Function to flush Redis cache
flush_redis() {
    print_step "Flushing Redis cache..."
    print_warning "This will clear all cached data!"
    
    read -p "Are you sure you want to continue? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        print_status "Redis flush cancelled"
        return 0
    fi
    
    if docker-compose exec -T redis redis-cli flushall; then
        print_success "Redis cache flushed"
    else
        print_error "Failed to flush Redis cache"
        return 1
    fi
}

# Function to clean up Docker system
cleanup_system() {
    print_step "Cleaning up Docker system..."
    
    # Remove stopped containers
    print_status "Removing stopped containers..."
    docker container prune -f
    
    # Remove unused images
    print_status "Removing unused images..."
    docker image prune -f
    
    # Remove unused volumes
    print_status "Removing unused volumes..."
    docker volume prune -f
    
    # Remove unused networks
    print_status "Removing unused networks..."
    docker network prune -f
    
    # Show disk usage
    print_status "Docker disk usage after cleanup:"
    docker system df
    
    print_success "System cleanup completed"
}

# Function to show container stats
show_stats() {
    print_step "Container Resource Usage:"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    docker stats --no-stream --format "table {{.Container}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.NetIO}}\t{{.BlockIO}}"
    
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

# Function to show network information
show_network() {
    print_step "Network Information:"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    # Show Docker networks
    print_status "Docker Networks:"
    docker network ls
    
    echo ""
    print_status "Container Network Details:"
    docker-compose ps -q | xargs -I {} docker inspect {} --format='{{.Name}} - {{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
    
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

# Function to show volume information
show_volumes() {
    print_step "Volume Information:"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    # Show all volumes
    print_status "All Volumes:"
    docker volume ls
    
    echo ""
    print_status "Project Volumes:"
    docker-compose config --volumes
    
    echo ""
    print_status "Volume Usage:"
    docker system df -v | grep "Local Volumes" -A 20
    
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

# Function to update containers
update_containers() {
    print_step "Updating containers..."
    
    # Pull latest images
    print_status "Pulling latest images..."
    docker-compose pull
    
    # Rebuild and restart
    print_status "Rebuilding and restarting services..."
    docker-compose up -d --build
    
    # Clean up old images
    print_status "Cleaning up old images..."
    docker image prune -f
    
    print_success "Containers updated"
}

# Main execution
main() {
    # Change to project root
    cd "$(dirname "$0")/../.."
    
    # Validate environment
    if ! validate_environment; then
        exit 1
    fi
    
    # Parse action
    local action="$1"
    shift
    
    case $action in
        "health")
            check_health
            ;;
        "restart")
            restart_service "$1"
            ;;
        "shell")
            open_shell "$1"
            ;;
        "django")
            run_django_command "$@"
            ;;
        "db-backup")
            backup_database
            ;;
        "db-restore")
            restore_database "$1"
            ;;
        "redis-cli")
            open_redis_cli
            ;;
        "redis-flush")
            flush_redis
            ;;
        "cleanup")
            cleanup_system
            ;;
        "stats")
            show_stats
            ;;
        "network")
            show_network
            ;;
        "volumes")
            show_volumes
            ;;
        "update")
            update_containers
            ;;
        "help"|"")
            show_help
            ;;
        *)
            print_error "Unknown action: $action"
            show_help
            exit 1
            ;;
    esac
}

# Run main function
main "$@"