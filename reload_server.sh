#!/bin/bash

# Server reload script for SPIEVAJ.TE
# This script gracefully reloads the uWSGI server to apply changes

echo "🔄 Reloading SPIEVAJ.TE server..."

# Get the project directory
PROJECT_DIR="/home/spevnik_admin/novy-spev"
PID_FILE="/tmp/spievaj.me.pid"
LOG_FILE="/var/log/uwsgi/spievaj.me.log"

# Function to check if server is running
check_server() {
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        return 0  # Server is running
    else
        return 1  # Server is not running
    fi
}

# Function to start the server
start_server() {
    echo "🚀 Starting uWSGI server..."
    cd "$PROJECT_DIR"
    
    # Activate virtual environment and start uWSGI
    source venv/bin/activate
    ./venv/bin/uwsgi --daemonize "$LOG_FILE" \
          --socket "$PROJECT_DIR/app.sock" \
          --module wsgi:app \
          --master \
          --processes 4 \
          --chmod-socket=664 \
          --vacuum \
          --die-on-term \
          --pidfile "$PID_FILE"
    
    if [ $? -eq 0 ]; then
        echo "✅ Server started successfully!"
        echo "📝 Logs: $LOG_FILE"
        echo "🔗 Socket: $PROJECT_DIR/app.sock"
    else
        echo "❌ Failed to start server!"
        exit 1
    fi
}

# Function to reload the server
reload_server() {
    echo "🔄 Sending reload signal to uWSGI..."
    if "$PROJECT_DIR/venv/bin/uwsgi" --reload "$PID_FILE"; then
        echo "✅ Server reloaded successfully!"
        echo "📊 Server status:"
        ps aux | grep uwsgi | grep -v grep | head -5
    else
        echo "⚠️  Reload signal failed, attempting restart..."
        stop_server
        sleep 2
        start_server
    fi
}

# Function to stop the server
stop_server() {
    if [ -f "$PID_FILE" ]; then
        echo "🛑 Stopping uWSGI server..."
        "$PROJECT_DIR/venv/bin/uwsgi" --stop "$PID_FILE"
        
        # Wait a bit and check if it's really stopped
        sleep 3
        if check_server; then
            echo "🔨 Force killing server..."
            kill -KILL "$(cat "$PID_FILE")" 2>/dev/null
            rm -f "$PID_FILE"
        fi
        echo "✅ Server stopped!"
    else
        echo "ℹ️  No PID file found, server might not be running"
    fi
}

# Function to show server status
show_status() {
    echo "📊 Server Status:"
    if check_server; then
        echo "✅ Server is running (PID: $(cat "$PID_FILE"))"
        echo "🔍 Processes:"
        ps aux | grep uwsgi | grep -v grep
        echo ""
        echo "📝 Recent logs (last 10 lines):"
        if [ -f "$LOG_FILE" ]; then
            tail -10 "$LOG_FILE"
        else
            echo "No log file found at $LOG_FILE"
        fi
    else
        echo "❌ Server is not running"
    fi
}

# Main script logic
case "${1:-reload}" in
    start)
        if check_server; then
            echo "⚠️  Server is already running!"
            show_status
        else
            start_server
        fi
        ;;
    stop)
        stop_server
        ;;
    restart)
        echo "🔄 Restarting server..."
        stop_server
        sleep 2
        start_server
        ;;
    reload)
        if check_server; then
            reload_server
        else
            echo "❌ Server is not running, starting it..."
            start_server
        fi
        ;;
    status)
        show_status
        ;;
    logs)
        echo "📝 Showing live logs (Ctrl+C to exit):"
        if [ -f "$LOG_FILE" ]; then
            tail -f "$LOG_FILE"
        else
            echo "No log file found at $LOG_FILE"
        fi
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|reload|status|logs}"
        echo ""
        echo "Commands:"
        echo "  start   - Start the server"
        echo "  stop    - Stop the server"
        echo "  restart - Stop and start the server"
        echo "  reload  - Gracefully reload the server (default)"
        echo "  status  - Show server status"
        echo "  logs    - Show live server logs"
        echo ""
        echo "Examples:"
        echo "  $0                # Reload server (default)"
        echo "  $0 reload         # Reload server"
        echo "  $0 restart        # Restart server"
        echo "  $0 status         # Check status"
        echo "  $0 logs           # View live logs"
        ;;
esac

echo "🎵 SPIEVAJ.TE server management complete!"