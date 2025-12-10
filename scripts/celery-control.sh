#!/bin/bash

# Celery Control Script for Spot Backend
# Usage: ./scripts/celery-control.sh [start|stop|status]

COMPOSE_CMD="docker-compose -f docker-compose.yaml -f docker-compose.prod.yml"

case "$1" in
    "stop"|"disable")
        echo "🛑 Stopping Celery services..."
        $COMPOSE_CMD stop celery-worker celery-beat
        echo "✅ Celery services stopped"
        echo "⚠️  Note: Large video uploads (>50MB) and photo verification will not work"
        ;;
    "start"|"enable")
        echo "🚀 Starting Celery services..."
        $COMPOSE_CMD start celery-worker celery-beat
        echo "✅ Celery services started"
        echo "✅ All features restored"
        ;;
    "status")
        echo "📊 Celery service status:"
        $COMPOSE_CMD ps celery-worker celery-beat
        ;;
    "restart")
        echo "🔄 Restarting Celery services..."
        $COMPOSE_CMD restart celery-worker celery-beat
        echo "✅ Celery services restarted"
        ;;
    *)
        echo "Usage: $0 [start|stop|status|restart]"
        echo ""
        echo "Commands:"
        echo "  stop    - Disable Celery (saves ~157MB RAM)"
        echo "  start   - Enable Celery (restores all features)"
        echo "  status  - Show current status"
        echo "  restart - Restart Celery services"
        exit 1
        ;;
esac 