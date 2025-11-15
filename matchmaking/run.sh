#!/bin/bash

# Stack Guys Matchmaking Server Startup Script

echo "🚀 Starting Stack Guys Matchmaking Server..."

# Activate virtual environment if exists
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# Install dependencies if needed
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
    source venv/bin/activate
    echo "📦 Installing dependencies..."
    pip install -r requirements.txt
fi

# Check if Redis is running
if ! pgrep -x "redis-server" > /dev/null; then
    echo "⚠️  Redis is not running. Starting Redis..."
    redis-server --daemonize yes
fi

# Start the server
echo "🎮 Starting FastAPI server..."
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
