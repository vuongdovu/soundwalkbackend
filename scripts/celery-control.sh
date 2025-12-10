#!/bin/bash

# =============================================================================
# Celery Control Script
# =============================================================================
# Control Celery worker and beat services
# Usage: ./scripts/celery-control.sh [start|stop|status|restart]
# =============================================================================

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

case "$1" in
    "stop"|"disable")
        echo -e "${YELLOW}Stopping Celery services...${NC}"
        docker-compose stop celery-worker celery-beat
        echo -e "${GREEN}Celery services stopped${NC}"
        ;;
    "start"|"enable")
        echo -e "${BLUE}Starting Celery services...${NC}"
        docker-compose start celery-worker celery-beat
        echo -e "${GREEN}Celery services started${NC}"
        ;;
    "status")
        echo -e "${BLUE}Celery service status:${NC}"
        docker-compose ps celery-worker celery-beat
        ;;
    "restart")
        echo -e "${YELLOW}Restarting Celery services...${NC}"
        docker-compose restart celery-worker celery-beat
        echo -e "${GREEN}Celery services restarted${NC}"
        ;;
    "logs")
        echo -e "${BLUE}Celery logs (Ctrl+C to stop):${NC}"
        docker-compose logs -f celery-worker celery-beat
        ;;
    *)
        echo "Celery Control Script"
        echo ""
        echo "Usage: $0 [command]"
        echo ""
        echo "Commands:"
        echo "  start   - Start Celery worker and beat"
        echo "  stop    - Stop Celery services"
        echo "  status  - Show current status"
        echo "  restart - Restart Celery services"
        echo "  logs    - Follow Celery logs"
        exit 1
        ;;
esac
