#!/bin/bash

# =============================================================================
# Spot Social - Production Hotfix Script
# =============================================================================
# Fast deployment script for urgent production code fixes
# Only rebuilds web container, skips makemigrations, intelligently loads fixtures
#
# Usage: ./scripts/docker/production/docker-prod-hotfix.sh [OPTIONS]
#
# Options:
#   --force-fixtures    Force reload fixtures even if already present
#   --skip-fixtures     Skip fixture loading entirely
#   --no-confirm        Skip confirmation prompt (for CI/CD)
#   --help              Show this help message
#
# =============================================================================

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Script configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# Parse arguments
FORCE_FIXTURES=false
SKIP_FIXTURES=false
NO_CONFIRM=false

for arg in "$@"; do
    case $arg in
        --force-fixtures)
            FORCE_FIXTURES=true
            ;;
        --skip-fixtures)
            SKIP_FIXTURES=true
            ;;
        --no-confirm)
            NO_CONFIRM=true
            ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Fast production hotfix deployment - rebuilds only web container"
            echo ""
            echo "Options:"
            echo "  --force-fixtures    Force reload fixtures even if already present"
            echo "  --skip-fixtures     Skip fixture loading entirely"
            echo "  --no-confirm        Skip confirmation prompt (for CI/CD)"
            echo "  --help, -h          Show this help message"
            echo ""
            echo "What this script does:"
            echo "  ✓ Rebuilds ONLY the web container (keeps nginx, redis, postgres running)"
            echo "  ✓ Skips makemigrations (assumes migrations already in code)"
            echo "  ✓ Runs migrate to apply any new migrations"
            echo "  ✓ Intelligently checks and loads production fixtures"
            echo "  ✓ Performs health checks"
            echo ""
            echo "Timing: ~1-2 minutes (vs 5-10 minutes for full deployment)"
            exit 0
            ;;
        *)
            echo -e "${RED}❌ Unknown argument: $arg${NC}"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

cd "$PROJECT_ROOT"

# Record start time
START_TIME=$(date +%s)

# Header
echo -e "${MAGENTA}⚡════════════════════════════════════════════════════════════⚡${NC}"
echo -e "${MAGENTA}⚡                PRODUCTION HOTFIX DEPLOYMENT                ⚡${NC}"
echo -e "${MAGENTA}⚡════════════════════════════════════════════════════════════⚡${NC}"
echo ""

# Pre-deployment checks
echo -e "${BLUE}🔍 Pre-deployment checks...${NC}"

if [ ! -f ".env.prod" ]; then
    echo -e "${RED}❌ Missing .env.prod file${NC}"
    echo "Create it from .env.prod.example and configure with production values"
    exit 1
fi

if [ ! -f "docker-compose.yaml" ] || [ ! -f "docker-compose.prod.yml" ]; then
    echo -e "${RED}❌ Missing Docker Compose files${NC}"
    exit 1
fi

echo -e "${GREEN}✅ All required files present${NC}"
echo ""

# Show what will happen
echo -e "${CYAN}📋 Hotfix Deployment Plan:${NC}"
echo "  1. Stop and rebuild ONLY the web container"
echo "  2. Skip makemigrations (assumes migrations in code)"
echo "  3. Run migrate to apply any new migrations"
if [ "$SKIP_FIXTURES" = true ]; then
    echo "  4. Skip fixture loading (--skip-fixtures)"
elif [ "$FORCE_FIXTURES" = true ]; then
    echo "  4. Force reload production fixtures (--force-fixtures)"
else
    echo "  4. Check and load production fixtures if needed"
fi
echo "  5. Perform health checks"
echo ""
echo -e "${YELLOW}⚠️  Services that will REMAIN RUNNING:${NC}"
echo "  • nginx (reverse proxy)"
echo "  • redis (cache)"
echo "  • prometheus, node-exporter, nginx-exporter (monitoring)"
echo ""
echo -e "${YELLOW}⚠️  Service that will be REBUILT:${NC}"
echo "  • web (Django application) - ~30-60 seconds downtime"
echo ""

# Confirmation prompt
if [ "$NO_CONFIRM" = false ]; then
    echo -e "${YELLOW}⚠️  This will deploy code changes to PRODUCTION${NC}"
    read -p "Continue with hotfix deployment? (Y/n): " -r
    echo
    if [[ $REPLY =~ ^[Nn]$ ]]; then
        echo -e "${RED}❌ Hotfix cancelled${NC}"
        exit 1
    fi
    echo ""
fi

# Step 1: Rebuild web container
echo -e "${BLUE}🔧 Step 1/5: Rebuilding web container...${NC}"
echo -e "${YELLOW}   (This will cause brief downtime for the web service)${NC}"

docker-compose -f docker-compose.yaml -f docker-compose.prod.yml up -d --build --no-deps web

echo -e "${GREEN}✅ Web container rebuilt${NC}"
echo ""

# Step 2: Wait for web container to start
echo -e "${BLUE}⏳ Step 2/5: Waiting for web container to start...${NC}"
sleep 10
echo -e "${GREEN}✅ Web container started${NC}"
echo ""

# Step 3: Run migrations (NOT makemigrations)
echo -e "${BLUE}🔄 Step 3/5: Running database migrations...${NC}"
echo -e "${YELLOW}   Note: Skipping makemigrations (assumes migrations already in code)${NC}"
echo -e "${CYAN}   Connecting to database and analyzing migration graph (may take 5-10s)...${NC}"

if docker exec spot-web python spot/manage.py migrate; then
    echo -e "${GREEN}✅ Migrations applied successfully${NC}"
else
    echo -e "${RED}❌ Migration failed!${NC}"
    echo "Check logs with: docker-compose -f docker-compose.yaml -f docker-compose.prod.yml logs web"
    exit 1
fi
echo ""

# Step 4: Load fixtures (intelligently)
echo -e "${BLUE}📦 Step 4/5: Checking production fixtures...${NC}"

if [ "$SKIP_FIXTURES" = true ]; then
    echo -e "${YELLOW}⏭️  Skipping fixture loading (--skip-fixtures flag)${NC}"
else
    # Check if fixtures already exist (unless --force-fixtures)
    LOAD_FIXTURES=false

    if [ "$FORCE_FIXTURES" = true ]; then
        echo -e "${YELLOW}🔁 Forcing fixture reload (--force-fixtures flag)${NC}"
        LOAD_FIXTURES=true
    else
        echo -e "${CYAN}   Checking if fixtures are already loaded...${NC}"
        if docker exec spot-web python spot/manage.py check_production_fixtures; then
            echo -e "${GREEN}✅ Production fixtures already loaded, skipping${NC}"
            LOAD_FIXTURES=false
        else
            echo -e "${YELLOW}⚠️  Some fixtures missing or check failed, will load them${NC}"
            echo -e "${CYAN}   Run with --debug to see why: docker exec spot-web python spot/manage.py check_production_fixtures --debug${NC}"
            LOAD_FIXTURES=true
        fi
    fi

    # Load fixtures if needed
    if [ "$LOAD_FIXTURES" = true ]; then
        echo -e "${BLUE}   Loading production fixtures...${NC}"

        # Install faker if needed
        docker exec spot-web pip install faker --quiet 2>/dev/null || true

        # Load production-safe fixtures
        if docker exec spot-web python spot/manage.py load_all_fixtures --skip-missing --production-only; then
            echo -e "${GREEN}✅ Production fixtures loaded successfully${NC}"
        else
            echo -e "${YELLOW}⚠️  Some fixtures could not be loaded (may be normal)${NC}"
        fi
    fi
fi
echo ""

# Step 5: Health checks
echo -e "${BLUE}🏥 Step 5/5: Running health checks...${NC}"

# Wait a bit for full startup
sleep 5

# Check web service status
echo -n "   Checking web service... "
if docker-compose -f docker-compose.yaml -f docker-compose.prod.yml ps web | grep -q "Up"; then
    echo -e "${GREEN}✅ Running${NC}"
else
    echo -e "${RED}❌ Not running${NC}"
fi

# Check health endpoint
echo -n "   Checking health endpoint... "
if docker exec spot-web curl -s -f http://localhost:8080/health/ | grep -q "prod" 2>/dev/null; then
    echo -e "${GREEN}✅ Health check passed${NC}"
else
    echo -e "${YELLOW}⚠️  Health check failed (may need more time)${NC}"
fi
echo ""

# Calculate and display execution time
END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))
MINUTES=$((DURATION / 60))
SECONDS=$((DURATION % 60))

# Summary
echo -e "${MAGENTA}═══════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}🎉 Production hotfix deployment completed!${NC}"
echo -e "${MAGENTA}═══════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "${CYAN}⏱️  Execution time: ${MINUTES}m ${SECONDS}s${NC}"
echo ""
echo -e "${BLUE}🌐 Production URLs:${NC}"
echo "  • Application: https://prod.spotsocial.app/"
echo "  • Health Check: https://prod.spotsocial.app/health/"
echo ""
echo -e "${BLUE}🔧 Useful Commands:${NC}"
echo "  • View logs: ./scripts/docker/docker-logs.sh --prod --follow"
echo "  • Check status: docker-compose -f docker-compose.yaml -f docker-compose.prod.yml ps"
echo "  • Django shell: docker exec -it spot-web python spot/manage.py shell"
echo ""
echo -e "${YELLOW}💡 Monitor application logs to verify deployment${NC}"
