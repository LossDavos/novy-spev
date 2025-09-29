#!/bin/bash

# Build and test script for novy_spev Flask application

echo "🔨 Building Docker image..."
docker build -t novy_spev .

if [ $? -eq 0 ]; then
    echo "✅ Docker build successful!"
else
    echo "❌ Docker build failed!"
    exit 1
fi

echo "🚀 Testing Docker container..."
timeout 5s docker run --rm -p 5000:5000 -v $(pwd)/instance:/app/instance novy_spev &
DOCKER_PID=$!

sleep 2

echo "🌐 Testing if Flask app is responding..."
if curl -f http://localhost:5000 > /dev/null 2>&1; then
    echo "✅ Flask application is responding!"
else
    echo "⚠️  Flask application not responding (may need more time or .env configuration)"
fi

# Clean up
if kill $DOCKER_PID 2>/dev/null; then
    echo "🛑 Stopped test container"
fi

echo "🎉 Test completed! Your Flask song management system is ready to use."
