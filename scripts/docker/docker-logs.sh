#!/bin/bash

# =============================================================================
# Spot Social - Logs Viewing Script
# =============================================================================
# Simplified script to view Docker logs for different environments
# Usage: ./scripts/docker/docker-logs.sh [service] [--prod] [--follow]

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

cd "$PROJECT_ROOT"

# Default values
SERVICE=""
ENVIRONMENT="dev"
FOLLOW_FLAG=""
TAIL_LINES="50"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --prod|--production)
            ENVIRONMENT="prod"
            shift
            ;;
        --follow|-f)
            FOLLOW_FLAG="-f"
            shift
            ;;
        --tail)
            TAIL_LINES="$2"
            shift 2
            ;;
        --help|-h)
            echo "Usage: $0 [service] [options]"
            echo ""
            echo "Services:"
            echo "  web           Django application"
            echo "  nginx         Nginx reverse proxy"
            echo "  redis         Redis cache/broker"
            echo "  celery-worker Background task worker"
            echo "  celery-beat   Scheduled task runner"
            echo "  (no service)  All services"
            echo ""
            echo "Options:"
            echo "  --prod, --production  View production logs"
            echo "  --follow, -f         Follow log output"
            echo "  --tail N             Show last N lines (default: 50)"
            echo ""
            echo "Examples:"
            echo "  $0                    # All development logs"
            echo "  $0 web --follow       # Follow Django logs"
            echo "  $0 --prod             # All production logs"
            echo "  $0 nginx --prod -f    # Follow production nginx logs"
            exit 0
            ;;
        *)
            if [ -z "$SERVICE" ]; then
                SERVICE="$1"
            else
                echo -e "${RED}❌ Unknown argument: $1${NC}"
                exit 1
            fi
            shift
            ;;
    esac
done

# Set compose command based on environment
if [ "$ENVIRONMENT" = "prod" ]; then
    COMPOSE_CMD="docker-compose -f docker-compose.yaml -f docker-compose.prod.yml"
    ENV_NAME="Production"
else
    COMPOSE_CMD="docker-compose"
    ENV_NAME="Development"
fi

# Show header
echo -e "${BLUE}📋 Spot Social - $ENV_NAME Logs${NC}"
echo "=================================="

# Check if any containers are running
if ! $COMPOSE_CMD ps | grep -q "Up"; then
    echo -e "${YELLOW}⚠️  No containers appear to be running in $ENV_NAME environment${NC}"
    echo "Start the environment first:"
    if [ "$ENVIRONMENT" = "prod" ]; then
        echo "  ./scripts/docker/docker-prod-deploy.sh"
    else
        echo "  ./scripts/docker/docker-dev-start.sh"
    fi
    exit 1
fi

# Validate service if specified
if [ -n "$SERVICE" ]; then
    VALID_SERVICES=("web" "nginx" "redis" "celery-worker" "celery-beat")
    if [[ ! " ${VALID_SERVICES[@]} " =~ " ${SERVICE} " ]]; then
        echo -e "${RED}❌ Invalid service: $SERVICE${NC}"
        echo "Valid services: ${VALID_SERVICES[*]}"
        exit 1
    fi
    
    # Check if specific service is running
    if ! $COMPOSE_CMD ps "$SERVICE" | grep -q "Up"; then
        echo -e "${YELLOW}⚠️  Service '$SERVICE' is not running${NC}"
        echo "Available services:"
        $COMPOSE_CMD ps
        exit 1
    fi
fi

# Build log command
LOG_CMD="$COMPOSE_CMD logs --tail=$TAIL_LINES"

if [ -n "$FOLLOW_FLAG" ]; then
    LOG_CMD="$LOG_CMD $FOLLOW_FLAG"
fi

if [ -n "$SERVICE" ]; then
    LOG_CMD="$LOG_CMD $SERVICE"
    echo -e "${GREEN}📄 Showing logs for '$SERVICE' service${NC}"
else
    echo -e "${GREEN}📄 Showing logs for all services${NC}"
fi

if [ -n "$FOLLOW_FLAG" ]; then
    echo -e "${YELLOW}👀 Following logs (Press Ctrl+C to stop)${NC}"
fi

echo ""

# Execute log command
$LOG_CMD