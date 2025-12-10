#!/bin/bash

# =============================================================================
# Docker Management Menu
# =============================================================================
# Interactive menu for Docker operations
# Usage: ./scripts/docker/docker-menu.sh
# =============================================================================

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

cd "$PROJECT_ROOT"

show_header() {
    clear
    echo -e "${BLUE}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║                    Docker Management Menu                    ║${NC}"
    echo -e "${BLUE}╚══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

show_menu() {
    echo -e "${CYAN}Available Commands:${NC}"
    echo ""
    echo -e "${GREEN}Development:${NC}"
    echo "  1) Start development environment"
    echo "  2) Start development (fresh/rebuild)"
    echo "  3) Stop development environment"
    echo ""
    echo -e "${BLUE}Management:${NC}"
    echo "  4) View logs"
    echo "  5) Show service status"
    echo "  6) Run Django management command"
    echo "  7) Open shell in web container"
    echo ""
    echo -e "${YELLOW}Utilities:${NC}"
    echo "  8) Rebuild images"
    echo "  9) Clean up Docker system"
    echo ""
    echo -e "${RED}Other:${NC}"
    echo "  h) Show help"
    echo "  q) Quit"
    echo ""
}

show_status() {
    echo -e "${BLUE}Current Status:${NC}"
    echo ""

    if docker-compose ps 2>/dev/null | grep -q "Up"; then
        docker-compose ps
    else
        echo "  No containers running"
    fi
    echo ""
}

# Main menu loop
while true; do
    show_header
    show_menu

    read -p "Enter your choice: " choice
    echo ""

    case $choice in
        1)
            echo -e "${GREEN}Starting development environment...${NC}"
            docker-compose up -d
            echo -e "${GREEN}Environment started!${NC}"
            ;;
        2)
            echo -e "${GREEN}Starting development environment (fresh)...${NC}"
            docker-compose down --remove-orphans 2>/dev/null || true
            docker-compose up -d --build
            echo -e "${GREEN}Environment started fresh!${NC}"
            ;;
        3)
            echo -e "${YELLOW}Stopping development environment...${NC}"
            docker-compose down
            echo -e "${GREEN}Environment stopped${NC}"
            ;;
        4)
            echo -e "${BLUE}Viewing logs (Press Ctrl+C to stop)...${NC}"
            sleep 1
            docker-compose logs -f --tail=50
            ;;
        5)
            show_status
            ;;
        6)
            read -p "Enter Django command (e.g., migrate, shell): " django_cmd
            docker-compose exec web python manage.py $django_cmd
            ;;
        7)
            echo -e "${BLUE}Opening shell in web container...${NC}"
            docker-compose exec web /bin/bash || docker-compose exec web /bin/sh
            ;;
        8)
            echo -e "${YELLOW}Rebuilding images...${NC}"
            docker-compose build --no-cache
            echo -e "${GREEN}Images rebuilt${NC}"
            ;;
        9)
            echo -e "${YELLOW}Cleaning up Docker system...${NC}"
            docker system prune -f
            docker volume prune -f
            echo -e "${GREEN}Cleanup complete${NC}"
            ;;
        h|help)
            echo "Individual script locations:"
            echo "  ./scripts/docker/docker-build.sh --help"
            echo "  ./scripts/docker/docker-logs.sh --help"
            echo "  ./scripts/docker/docker-utils.sh help"
            ;;
        q|quit|exit)
            echo -e "${GREEN}Goodbye!${NC}"
            exit 0
            ;;
        *)
            echo -e "${RED}Invalid choice: $choice${NC}"
            ;;
    esac

    echo ""
    read -p "Press Enter to continue..."
done
