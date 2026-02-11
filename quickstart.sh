#!/bin/bash

# Canvas Quiz Bot - Quick Commands

case "$1" in
    build)
        echo "Building Docker image..."
        docker build -t canvas-quiz-bot .
        ;;
    
    start)
        echo "Starting Canvas Quiz Bot..."
        docker-compose up -d
        echo ""
        echo "Services started!"
        echo "Main UI: http://localhost:5000"
        echo "noVNC:   http://localhost:6080/vnc.html"
        echo ""
        echo "View logs: ./quickstart.sh logs"
        ;;
    
    stop)
        echo "Stopping Canvas Quiz Bot..."
        docker-compose down
        ;;
    
    restart)
        echo "Restarting Canvas Quiz Bot..."
        docker-compose restart
        ;;
    
    logs)
        docker-compose logs -f
        ;;
    
    clean)
        echo "Cleaning up..."
        docker-compose down -v
        docker rmi canvas-quiz-bot 2>/dev/null || true
        echo "Cleanup complete"
        ;;
    
    status)
        echo "Checking service status..."
        docker-compose ps
        echo ""
        curl -s http://localhost:5000/api/health | python3 -m json.tool 2>/dev/null || echo "Service not responding"
        ;;
    
    shell)
        echo "Opening shell in container..."
        docker-compose exec canvas-quiz-bot /bin/bash
        ;;
    
    update)
        echo "Updating and rebuilding..."
        git pull
        docker-compose down
        docker-compose build --no-cache
        docker-compose up -d
        echo "Update complete!"
        ;;
    
    *)
        echo "Canvas Quiz Bot - Quick Commands"
        echo ""
        echo "Usage: ./quickstart.sh [command]"
        echo ""
        echo "Commands:"
        echo "  build    - Build Docker image"
        echo "  start    - Start services"
        echo "  stop     - Stop services"
        echo "  restart  - Restart services"
        echo "  logs     - View logs (Ctrl+C to exit)"
        echo "  status   - Check service status"
        echo "  shell    - Open shell in container"
        echo "  clean    - Remove containers and images"
        echo "  update   - Pull latest code and rebuild"
        echo ""
        echo "Examples:"
        echo "  ./quickstart.sh build"
        echo "  ./quickstart.sh start"
        echo "  ./quickstart.sh logs"
        ;;
esac
