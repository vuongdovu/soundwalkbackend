#!/bin/bash

# =============================================================================
# Spot Social - Production Deployment Script
# =============================================================================
# Simplified script to deploy to production environment
# Usage: ./scripts/docker/docker-prod-deploy.sh [--build] [--stop-systemd]

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Script configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

echo -e "${BLUE}🚀 Deploying Spot Social to Production${NC}"
echo "======================================"

cd "$PROJECT_ROOT"

# Parse arguments
BUILD_FLAG=""
STOP_SYSTEMD=false

for arg in "$@"; do
    case $arg in
        --build)
            BUILD_FLAG="--build"
            echo -e "${YELLOW}📦 Building production images...${NC}"
            ;;
        --stop-systemd)
            STOP_SYSTEMD=true
            echo -e "${YELLOW}🛑 Will stop systemd services...${NC}"
            ;;
        --help|-h)
            echo "Usage: $0 [--build] [--stop-systemd]"
            echo "  --build         Rebuild Docker images"
            echo "  --stop-systemd  Stop existing systemd services (gunicorn, nginx)"
            exit 0
            ;;
        *)
            echo -e "${RED}❌ Unknown argument: $arg${NC}"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Check for required files
echo -e "${BLUE}🔍 Pre-deployment checks...${NC}"

if [ ! -f ".env.prod" ]; then
    echo -e "${RED}❌ Missing .env.prod file${NC}"
    echo "Create it from .env.prod.example and configure with production values"
    echo "  cp .env.prod.example .env.prod"
    echo "  # Edit .env.prod with actual production settings"
    exit 1
fi

# Note: .env.development is NOT required for production deployment

if [ ! -f "docker-compose.yaml" ] || [ ! -f "docker-compose.prod.yml" ]; then
    echo -e "${RED}❌ Missing Docker Compose files${NC}"
    exit 1
fi

echo -e "${GREEN}✅ All required files present${NC}"

# Stop systemd services if requested
if [ "$STOP_SYSTEMD" = true ]; then
    echo -e "${YELLOW}🛑 Stopping systemd services...${NC}"
    
    if systemctl is-active --quiet gunicorn.service; then
        echo "  Stopping gunicorn..."
        sudo systemctl stop gunicorn.service || true
    fi
    
    if systemctl is-active --quiet nginx; then
        echo "  Stopping nginx..."
        sudo systemctl stop nginx || true
    fi
    
    echo -e "${GREEN}✅ Systemd services stopped${NC}"
fi

# Check for port conflicts
echo -e "${BLUE}🔍 Checking for port conflicts...${NC}"
if lsof -i :8080 > /dev/null 2>&1; then
    echo -e "${YELLOW}⚠️  Port 8080 is in use${NC}"
    echo "Running processes on port 8080:"
    lsof -i :8080
    echo ""
    read -p "Continue anyway? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo -e "${RED}❌ Deployment cancelled${NC}"
        exit 1
    fi
fi

# Stop existing production containers if running
echo -e "${YELLOW}🛑 Stopping any existing production containers...${NC}"
docker-compose -f docker-compose.yaml -f docker-compose.prod.yml down --remove-orphans 2>/dev/null || true

# Additional cleanup when building
if [ "$BUILD_FLAG" = "--build" ]; then
    echo -e "${YELLOW}🧹 Performing additional cleanup for rebuild...${NC}"
    
    # Stop all running containers (not just production)
    echo -e "${YELLOW}🛑 Stopping all running containers...${NC}"
    docker stop $(docker ps -q) 2>/dev/null || true
    
    # Remove existing images to force rebuild
    echo -e "${YELLOW}🗑️  Removing existing images for clean rebuild...${NC}"
    docker-compose -f docker-compose.yaml -f docker-compose.prod.yml down --rmi all --volumes --remove-orphans 2>/dev/null || true
    
    # Clean up dangling images and build cache
    echo -e "${YELLOW}🧹 Cleaning up Docker build cache...${NC}"
    docker builder prune -f 2>/dev/null || true
    docker image prune -f 2>/dev/null || true
fi

# Deploy production stack (only required services)
echo -e "${GREEN}🐳 Deploying production stack...${NC}"
echo -e "${YELLOW}📋 Starting services in order: redis -> celery-worker -> celery-beat -> web -> nginx${NC}"

# Start Redis first (no dependencies)
echo -e "${BLUE}1/8 Starting Redis...${NC}"
docker-compose -f docker-compose.yaml -f docker-compose.prod.yml up -d --no-deps $BUILD_FLAG redis

# Start Celery Worker (depends on Redis)
echo -e "${BLUE}2/8 Starting Celery Worker...${NC}"
docker-compose -f docker-compose.yaml -f docker-compose.prod.yml up -d --no-deps $BUILD_FLAG celery-worker

# Start Celery Beat (depends on Redis)
echo -e "${BLUE}3/8 Starting Celery Beat...${NC}"
docker-compose -f docker-compose.yaml -f docker-compose.prod.yml up -d --no-deps $BUILD_FLAG celery-beat

# Start Web (will connect to external RDS, Redis already running)
echo -e "${BLUE}4/8 Starting Web application...${NC}"
docker-compose -f docker-compose.yaml -f docker-compose.prod.yml up -d --no-deps web

# Start Nginx (depends on web, but we'll start it manually)
echo -e "${BLUE}5/8 Starting Nginx proxy...${NC}"
docker-compose -f docker-compose.yaml -f docker-compose.prod.yml up -d --no-deps nginx

# Start Prometheus
echo -e "${BLUE}6/8 Starting Prometheus...${NC}"
docker-compose -f docker-compose.yaml -f docker-compose.prod.yml up -d --no-deps prometheus

# Start Node Exporter
echo -e "${BLUE}7/8 Starting Node Exporter...${NC}"
docker-compose -f docker-compose.yaml -f docker-compose.prod.yml up -d --no-deps node-exporter

# Start Nginx Exporter
echo -e "${BLUE}8/8 Starting Nginx Exporter...${NC}"
docker-compose -f docker-compose.yaml -f docker-compose.prod.yml up -d --no-deps nginx-exporter

# Wait for services to start
echo -e "${BLUE}⏳ Waiting for services to start...${NC}"
sleep 10

# Run migrations
echo -e "\n${BLUE}🔄 Running database migrations...${NC}"
echo -e "${YELLOW}📝 Making migrations...${NC}"
docker exec spot-web python spot/manage.py makemigrations

echo -e "${YELLOW}🚀 Applying migrations...${NC}"
docker exec spot-web python spot/manage.py migrate

echo -e "${GREEN}✅ Migrations completed${NC}"

# Load fixtures (essential data for production)
echo -e "\n${BLUE}📦 Loading production fixtures...${NC}"
echo -e "${YELLOW}📋 Loading configuration data only (no test users, profiles, or events)${NC}"

# Install dependencies for fixtures if needed
docker exec spot-web pip install faker --quiet 2>/dev/null || true

# Load production-safe fixtures using the management command
if docker exec spot-web python spot/manage.py load_all_fixtures --skip-missing --production-only; then
    echo -e "${GREEN}✅ Production fixtures loaded successfully${NC}"
else
    echo -e "${YELLOW}⚠️  Some fixtures could not be loaded (this may be normal for production)${NC}"
fi

# Show status
echo -e "\n${GREEN}📊 Production Service Status:${NC}"
docker-compose -f docker-compose.yaml -f docker-compose.prod.yml ps

# Health checks
echo -e "\n${YELLOW}🏥 Production Health Checks:${NC}"

# Wait a bit more for full startup
sleep 5

# Check health endpoint
echo -n "Checking health endpoint... "
if docker exec spot-web curl -s -f http://localhost:8080/health/ | grep -q "prod" 2>/dev/null; then
    echo -e "${GREEN}✅ Health check passed${NC}"
else
    echo -e "${RED}❌ Health check failed${NC}"
    echo "Check logs with: docker-compose -f docker-compose.yaml -f docker-compose.prod.yml logs"
fi

# Check individual services
echo -n "Checking web service... "
if docker-compose -f docker-compose.yaml -f docker-compose.prod.yml ps web | grep -q "Up"; then
    echo -e "${GREEN}✅ Running${NC}"
else
    echo -e "${RED}❌ Not running${NC}"
fi

echo -n "Checking nginx service... "
if docker-compose -f docker-compose.yaml -f docker-compose.prod.yml ps nginx | grep -q "Up"; then
    echo -e "${GREEN}✅ Running${NC}"
else
    echo -e "${RED}❌ Not running${NC}"
fi

echo -n "Checking redis service... "
if docker-compose -f docker-compose.yaml -f docker-compose.prod.yml ps redis | grep -q "Up"; then
    echo -e "${GREEN}✅ Running${NC}"
else
    echo -e "${RED}❌ Not running${NC}"
fi

echo -n "Checking prometheus service... "
if docker-compose -f docker-compose.yaml -f docker-compose.prod.yml ps prometheus | grep -q "Up"; then
    echo -e "${GREEN}✅ Running${NC}"
else
    echo -e "${RED}❌ Not running${NC}"
fi

echo -n "Checking nginx-exporter service... "
if docker-compose -f docker-compose.yaml -f docker-compose.prod.yml ps nginx-exporter | grep -q "Up"; then
    echo -e "${GREEN}✅ Running${NC}"
else
    echo -e "${RED}❌ Not running${NC}"
fi

echo -n "Checking node-exporter service... "
if docker-compose -f docker-compose.yaml -f docker-compose.prod.yml ps node-exporter | grep -q "Up"; then
    echo -e "${GREEN}✅ Running${NC}"
else
    echo -e "${RED}❌ Not running${NC}"
fi

echo -n "Checking celery-worker service... "
if docker-compose -f docker-compose.yaml -f docker-compose.prod.yml ps celery-worker | grep -q "Up"; then
    echo -e "${GREEN}✅ Running${NC}"
else
    echo -e "${RED}❌ Not running${NC}"
fi

echo -n "Checking celery-beat service... "
if docker-compose -f docker-compose.yaml -f docker-compose.prod.yml ps celery-beat | grep -q "Up"; then
    echo -e "${GREEN}✅ Running${NC}"
else
    echo -e "${RED}❌ Not running${NC}"
fi

# Show useful information
echo -e "\n${BLUE}🌐 Production URLs:${NC}"
echo "  • Application: https://prod.spotsocial.app/"
echo "  • Health Check: https://prod.spotsocial.app/health/"
echo "  • Internal web service: http://web:8080 (container-to-container)"

echo -e "\n${YELLOW}⚠️  Production Notes:${NC}"
echo "  • Running services: web, nginx, redis, celery-worker, celery-beat, prometheus, node_exporter, nginx_exporter"
echo "  • Disabled services: postgres (using external RDS)"
echo "  • External PostgreSQL database should be configured in .env.prod"

echo -e "\n${BLUE}🔧 Management Commands:${NC}"
echo "  • View logs: ./scripts/docker/docker-logs.sh"
echo "  • Stop production: ./scripts/docker/docker-prod-stop.sh"
echo "  • Django shell: docker exec -it spot-web python spot/manage.py shell"

echo -e "\n${BLUE}🔍 WebSocket Endpoints:${NC}"
echo "  • Chat: wss://prod.spotsocial.app/ws/chat/{uuid}/"
echo "  • Events: wss://prod.spotsocial.app/ws/events/{uuid}/"

echo -e "\n${GREEN}🎉 Production deployment completed!${NC}"
echo -e "${YELLOW}💡 Monitor logs with: ./scripts/docker/docker-logs.sh${NC}"