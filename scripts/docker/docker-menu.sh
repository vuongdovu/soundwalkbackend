#!/bin/bash

# =============================================================================
# Spot Social - Docker Management Menu
# =============================================================================
# Interactive menu for Docker operations
# Usage: ./scripts/docker/docker-menu.sh

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Script configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

cd "$PROJECT_ROOT"

# Functions
show_header() {
    clear
    echo -e "${BLUE}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║                    🐳 Spot Social Docker Menu                ║${NC}"
    echo -e "${BLUE}╚══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

show_menu() {
    echo -e "${CYAN}📋 Available Commands:${NC}"
    echo ""
    echo -e "${GREEN}Development:${NC}"
    echo "  1) Start development environment"
    echo "  2) Start development (fresh/rebuild)"
    echo "  3) Stop development environment"
    echo ""
    echo -e "${YELLOW}Production:${NC}"
    echo "  4) Deploy to production"
    echo "  5) Deploy to production (with build)"
    echo "  6) Production Hotfix (fast deploy)"
    echo "  7) Stop production environment"
    echo ""
    echo -e "${BLUE}Management:${NC}"
    echo "  8) View logs (development)"
    echo "  9) View logs (production)"
    echo "  10) Show service status"
    echo ""
    echo -e "${RED}Other:${NC}"
    echo "  h) Show help for individual scripts"
    echo "  q) Quit"
    echo ""
}

show_status() {
    echo -e "${BLUE}📊 Current Status:${NC}"
    echo ""
    
    echo -e "${GREEN}Development Environment:${NC}"
    if docker-compose ps 2>/dev/null | grep -q "Up"; then
        docker-compose ps
    else
        echo "  No development containers running"
    fi
    
    echo ""
    echo -e "${YELLOW}Production Environment:${NC}"
    if docker-compose -f docker-compose.yaml -f docker-compose.prod.yml ps 2>/dev/null | grep -q "Up"; then
        docker-compose -f docker-compose.yaml -f docker-compose.prod.yml ps
    else
        echo "  No production containers running"
    fi
    echo ""
}

show_help() {
    echo -e "${BLUE}🔧 Individual Script Help:${NC}"
    echo ""
    echo "Development:"
    if [ -f "./scripts/docker/development/docker-dev-start.sh" ]; then
        echo "  ./scripts/docker/development/docker-dev-start.sh --help"
    else
        echo "  ./scripts/docker/docker-dev-start.sh --help"
    fi
    echo ""
    echo "Production:"
    if [ -f "./scripts/docker/production/docker-prod-deploy.sh" ]; then
        echo "  ./scripts/docker/production/docker-prod-deploy.sh --help"
        echo "  ./scripts/docker/production/docker-prod-hotfix.sh --help"
        echo "  ./scripts/docker/production/docker-prod-stop.sh --help"
    else
        echo "  ./scripts/docker/docker-prod-deploy.sh --help"
        echo "  ./scripts/docker/docker-prod-stop.sh --help"
    fi
    echo ""
    echo "Logs:"
    echo "  ./scripts/docker/docker-logs.sh --help"
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
            echo -e "${GREEN}🚀 Starting development environment...${NC}"
            if [ -f "./scripts/docker/development/docker-dev-start.sh" ]; then
                ./scripts/docker/development/docker-dev-start.sh
            else
                ./scripts/docker/docker-dev-start.sh
            fi
            ;;
        2)
            echo -e "${GREEN}🚀 Starting development environment (fresh)...${NC}"
            if [ -f "./scripts/docker/development/docker-dev-start.sh" ]; then
                ./scripts/docker/development/docker-dev-start.sh --fresh --build
            else
                ./scripts/docker/docker-dev-start.sh --fresh --build
            fi
            ;;
        3)
            echo -e "${YELLOW}🛑 Stopping development environment...${NC}"
            docker-compose down
            echo -e "${GREEN}✅ Development environment stopped${NC}"
            ;;
        4)
            echo -e "${YELLOW}🚀 Deploying to production...${NC}"
            if [ -f "./scripts/docker/production/docker-prod-deploy.sh" ]; then
                ./scripts/docker/production/docker-prod-deploy.sh
            else
                ./scripts/docker/docker-prod-deploy.sh
            fi
            ;;
        5)
            echo -e "${YELLOW}🚀 Deploying to production (with build)...${NC}"
            if [ -f "./scripts/docker/production/docker-prod-deploy.sh" ]; then
                ./scripts/docker/production/docker-prod-deploy.sh --build
            else
                ./scripts/docker/docker-prod-deploy.sh --build
            fi
            ;;
        6)
            echo -e "${MAGENTA}⚡ Production Hotfix Deployment...${NC}"
            if [ -f "./scripts/docker/production/docker-prod-hotfix.sh" ]; then
                ./scripts/docker/production/docker-prod-hotfix.sh
            else
                echo -e "${RED}❌ Hotfix script not found${NC}"
            fi
            ;;
        7)
            echo -e "${RED}🛑 Stopping production environment...${NC}"
            if [ -f "./scripts/docker/production/docker-prod-stop.sh" ]; then
                ./scripts/docker/production/docker-prod-stop.sh
            else
                ./scripts/docker/docker-prod-stop.sh
            fi
            ;;
        8)
            echo -e "${BLUE}📋 Development logs...${NC}"
            echo "Press Ctrl+C to return to menu"
            sleep 2
            ./scripts/docker/docker-logs.sh --follow
            ;;
        9)
            echo -e "${BLUE}📋 Production logs...${NC}"
            echo "Press Ctrl+C to return to menu"
            sleep 2
            ./scripts/docker/docker-logs.sh --prod --follow
            ;;
        10)
            show_status
            ;;
        h|help)
            show_help
            ;;
        q|quit|exit)
            echo -e "${GREEN}👋 Goodbye!${NC}"
            exit 0
            ;;
        *)
            echo -e "${RED}❌ Invalid choice: $choice${NC}"
            ;;
    esac
    
    echo ""
    read -p "Press Enter to continue..."
done