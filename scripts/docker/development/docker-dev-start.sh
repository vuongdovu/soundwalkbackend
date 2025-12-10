#!/bin/bash

# =============================================================================
# Spot Social - Development Environment Startup Script
# =============================================================================
# Simple script to start the development environment
# Usage: ./scripts/docker/docker-dev-start.sh [--build] [--fresh]

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Script configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo -e "${BLUE}🚀 Starting Spot Social Development Environment${NC}"
echo "=============================================="

cd "$PROJECT_ROOT"

# Parse arguments
BUILD_FLAG=""
FRESH_START=false
LOAD_FIXTURES=false

for arg in "$@"; do
    case $arg in
        --build)
            BUILD_FLAG="--build"
            echo -e "${YELLOW}📦 Building images...${NC}"
            ;;
        --fresh)
            FRESH_START=true
            echo -e "${YELLOW}🧹 Fresh start requested - cleaning up...${NC}"
            ;;
        --fixtures)
            LOAD_FIXTURES=true
            echo -e "${YELLOW}📋 Will load fixture data...${NC}"
            ;;
        --help|-h)
            echo "Usage: $0 [--build] [--fresh] [--fixtures]"
            echo "  --build    Rebuild Docker images"
            echo "  --fresh    Stop and remove containers before starting"
            echo "  --fixtures Load fixture data (auth, profiles, orgs, drinks, etc.)"
            exit 0
            ;;
        *)
            echo -e "${RED}❌ Unknown argument: $arg${NC}"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Fresh start - stop and remove containers
if [ "$FRESH_START" = true ]; then
    echo -e "${YELLOW}🛑 Stopping existing containers...${NC}"
    docker-compose down --remove-orphans 2>/dev/null || true
    
    echo -e "${YELLOW}🧹 Removing development volumes...${NC}"
    docker volume rm spot-backend_redis_data 2>/dev/null || true
    docker volume rm spot-backend_celery_beat_data 2>/dev/null || true
    
    # Auto-enable fixtures on fresh start
    LOAD_FIXTURES=true
    echo -e "${BLUE}📋 Fresh start: fixtures will be loaded${NC}"
fi

# Check if containers are already running
if docker-compose ps | grep -q "Up"; then
    echo -e "${YELLOW}⚠️  Some containers are already running${NC}"
    echo "Use --fresh to restart everything"
fi

# Start development environment
echo -e "${GREEN}🐳 Starting development containers...${NC}"
docker-compose up -d $BUILD_FLAG

# Wait a moment for services to start
sleep 3

# Install dependencies for fixtures if needed
if [ "$LOAD_FIXTURES" = true ]; then
    echo -e "\n${BLUE}📚 Installing dependencies for fixtures...${NC}"
    docker exec spot-web pip install faker --quiet
    echo -e "${GREEN}✅ Dependencies installed${NC}"
fi

# Run migrations
echo -e "\n${BLUE}🔄 Running database migrations...${NC}"
echo -e "${YELLOW}📝 Making migrations...${NC}"
docker exec spot-web python spot/manage.py makemigrations

echo -e "${YELLOW}🚀 Applying migrations...${NC}"
docker exec spot-web python spot/manage.py migrate

echo -e "${GREEN}✅ Migrations completed${NC}"

# Load fixtures if requested
if [ "$LOAD_FIXTURES" = true ]; then
    echo -e "\n${BLUE}📦 Loading fixture data...${NC}"
    if docker exec spot-web python spot/manage.py load_all_fixtures --skip-missing; then
        echo -e "${GREEN}✅ Fixtures loaded successfully${NC}"
    else
        echo -e "${YELLOW}⚠️  Some fixtures could not be loaded (this is normal for missing fixtures)${NC}"
    fi
fi

# Show status
echo -e "\n${GREEN}📊 Service Status:${NC}"
docker-compose ps

# Show useful information
echo -e "\n${BLUE}🌐 Development URLs:${NC}"
echo "  • Application: http://localhost:8080"
echo "  • Nginx Proxy: http://localhost:80"
echo "  • Redis: localhost:6379"

echo -e "\n${BLUE}🔧 Useful Commands:${NC}"
echo "  • View logs: docker-compose logs -f [service]"
echo "  • Django shell: docker exec -it spot-web python spot/manage.py shell"
echo "  • Stop services: docker-compose down"

# Health check
echo -e "\n${YELLOW}🏥 Health Check:${NC}"
sleep 2
if curl -s http://localhost:8080 > /dev/null 2>&1; then
    echo -e "${GREEN}✅ Django is responding${NC}"
else
    echo -e "${RED}❌ Django not responding yet (may need more time)${NC}"
fi

if curl -s http://localhost > /dev/null 2>&1; then
    echo -e "${GREEN}✅ Nginx proxy is responding${NC}"
else
    echo -e "${RED}❌ Nginx proxy not responding yet${NC}"
fi

echo -e "\n${GREEN}🎉 Development environment started successfully!${NC}"

if [ "$LOAD_FIXTURES" = true ]; then
    echo -e "${BLUE}📋 Fixture data loaded (users, orgs, drinks, etc.)${NC}"
else
    echo -e "${YELLOW}💡 To load fixture data, restart with: $0 --fixtures${NC}"
fi

echo "View logs with: docker-compose logs -f"