#!/bin/bash

# =============================================================================
# Spot Social - Production Stop Script
# =============================================================================
# Simplified script to stop production environment
# Usage: ./scripts/docker/docker-prod-stop.sh [--remove-volumes]

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

echo -e "${BLUE}🛑 Stopping Spot Social Production Environment${NC}"
echo "=============================================="

cd "$PROJECT_ROOT"

# Parse arguments
REMOVE_VOLUMES=false

for arg in "$@"; do
    case $arg in
        --remove-volumes)
            REMOVE_VOLUMES=true
            echo -e "${YELLOW}🗑️  Will remove production volumes...${NC}"
            ;;
        --help|-h)
            echo "Usage: $0 [--remove-volumes]"
            echo "  --remove-volumes  Remove production data volumes (WARNING: Data loss!)"
            exit 0
            ;;
        *)
            echo -e "${RED}❌ Unknown argument: $arg${NC}"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Check if production is running
if ! docker-compose -f docker-compose.yaml -f docker-compose.prod.yml ps | grep -q "Up"; then
    echo -e "${YELLOW}⚠️  No production containers appear to be running${NC}"
    echo "Continuing anyway..."
fi

# Stop production containers
echo -e "${YELLOW}🛑 Stopping production containers...${NC}"
docker-compose -f docker-compose.yaml -f docker-compose.prod.yml down --remove-orphans

# Remove volumes if requested
if [ "$REMOVE_VOLUMES" = true ]; then
    echo -e "${RED}⚠️  WARNING: This will delete all production data!${NC}"
    read -p "Are you sure you want to remove production volumes? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo -e "${YELLOW}🗑️  Removing production volumes...${NC}"
        docker volume rm spot-backend_redis_prod_data 2>/dev/null || true
        docker volume rm spot-backend_celery_beat_prod_data 2>/dev/null || true
        echo -e "${GREEN}✅ Production volumes removed${NC}"
    else
        echo -e "${BLUE}ℹ️  Volume removal cancelled${NC}"
    fi
fi

# Show remaining containers/volumes
echo -e "\n${BLUE}📊 Current Docker Status:${NC}"
echo "Containers:"
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | grep spot || echo "  No Spot containers running"

echo -e "\nVolumes:"
docker volume ls | grep spot || echo "  No Spot volumes found"

echo -e "\n${GREEN}✅ Production environment stopped successfully${NC}"

# Suggest next steps
echo -e "\n${BLUE}💡 Next Steps:${NC}"
echo "  • Start development: ./scripts/docker/docker-dev-start.sh"
echo "  • Deploy production: ./scripts/docker/docker-prod-deploy.sh"
echo "  • View logs: ./scripts/docker/docker-logs.sh"