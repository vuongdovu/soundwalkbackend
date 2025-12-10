#!/bin/bash

# =============================================================================
# Development Environment Startup Script
# =============================================================================
# Start the development environment with optional rebuild
# Usage: ./scripts/docker/development/docker-dev-start.sh [--build] [--fresh]
# =============================================================================

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

echo -e "${BLUE}Starting Development Environment${NC}"
echo "=============================================="

cd "$PROJECT_ROOT"

# Parse arguments
BUILD_FLAG=""
FRESH_START=false

for arg in "$@"; do
    case $arg in
        --build)
            BUILD_FLAG="--build"
            echo -e "${YELLOW}Building images...${NC}"
            ;;
        --fresh)
            FRESH_START=true
            echo -e "${YELLOW}Fresh start requested - cleaning up...${NC}"
            ;;
        --help|-h)
            echo "Usage: $0 [--build] [--fresh]"
            echo "  --build    Rebuild Docker images"
            echo "  --fresh    Stop and remove containers before starting"
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown argument: $arg${NC}"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Fresh start - stop and remove containers
if [ "$FRESH_START" = true ]; then
    echo -e "${YELLOW}Stopping existing containers...${NC}"
    docker-compose down --remove-orphans 2>/dev/null || true

    echo -e "${YELLOW}Removing development volumes...${NC}"
    docker volume rm app-redis-data 2>/dev/null || true
fi

# Check if containers are already running
if docker-compose ps | grep -q "Up"; then
    echo -e "${YELLOW}Some containers are already running${NC}"
    echo "Use --fresh to restart everything"
fi

# Start development environment
echo -e "${GREEN}Starting development containers...${NC}"
docker-compose up -d $BUILD_FLAG

# Wait a moment for services to start
sleep 3

# Run migrations
echo -e "\n${BLUE}Running database migrations...${NC}"
docker-compose exec -T web python manage.py migrate --noinput

echo -e "${GREEN}Migrations completed${NC}"

# Show status
echo -e "\n${GREEN}Service Status:${NC}"
docker-compose ps

# Show useful information
echo -e "\n${BLUE}Development URLs:${NC}"
echo "  Application: http://localhost"
echo "  Django Direct: http://localhost:8080"
echo "  Health Check: http://localhost/health/"
echo "  Admin: http://localhost/admin/"

echo -e "\n${BLUE}Useful Commands:${NC}"
echo "  View logs: docker-compose logs -f [service]"
echo "  Django shell: docker-compose exec web python manage.py shell"
echo "  Create superuser: docker-compose exec web python manage.py createsuperuser"
echo "  Stop services: docker-compose down"

# Health check
echo -e "\n${YELLOW}Health Check:${NC}"
sleep 2
if curl -s http://localhost:8080/health/ > /dev/null 2>&1; then
    echo -e "${GREEN}Django is responding${NC}"
else
    echo -e "${YELLOW}Django may need more time to start${NC}"
fi

if curl -s http://localhost > /dev/null 2>&1; then
    echo -e "${GREEN}Nginx proxy is responding${NC}"
else
    echo -e "${YELLOW}Nginx may need more time to start${NC}"
fi

echo -e "\n${GREEN}Development environment started successfully!${NC}"
echo "View logs with: docker-compose logs -f"
