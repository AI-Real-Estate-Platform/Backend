#!/bin/bash

# Dari Backend - Quick Start Script
# This script sets up and starts the backend using Docker

set -e

echo "🚀 Dari Backend - Docker Setup"
echo "================================"
echo ""

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo "❌ Docker is not installed. Please install Docker first."
    echo "   Visit: https://docs.docker.com/get-docker/"
    exit 1
fi

# Check if Docker Compose is installed
if ! command -v docker-compose &> /dev/null; then
    echo "❌ Docker Compose is not installed. Please install Docker Compose first."
    echo "   Visit: https://docs.docker.com/compose/install/"
    exit 1
fi

echo "✅ Docker and Docker Compose are installed"
echo ""

# Check if .env file exists
if [ ! -f .env ]; then
    echo "📝 Creating .env file from template..."
    cp .env.example .env
    echo "⚠️  Please edit .env and set your AUTH_SECRET_KEY before deploying to production!"
    echo ""
fi

# Create data directory if it doesn't exist
if [ ! -d ./data ]; then
    echo "📁 Creating data directory..."
    mkdir -p ./data
fi

# Build the Docker image
echo "🔨 Building Docker image..."
docker-compose build

echo ""
echo "✅ Build complete!"
echo ""

# Start the containers
echo "🚀 Starting containers..."
docker-compose up -d

echo ""
echo "⏳ Waiting for backend to be ready..."
sleep 5

# Check health
echo ""
echo "🏥 Checking API health..."
if curl -s http://localhost:8000/api/health > /dev/null; then
    echo "✅ Backend is running successfully!"
    echo ""
    echo "📍 API URL: http://localhost:8000"
    echo "📍 Health Check: http://localhost:8000/api/health"
    echo "📍 API Docs: http://localhost:8000/docs"
    echo ""
    echo "📋 Useful commands:"
    echo "   View logs:        docker-compose logs -f backend"
    echo "   Stop backend:     docker-compose down"
    echo "   Restart backend:  docker-compose restart"
    echo "   Access shell:     docker-compose exec backend bash"
    echo ""
    echo "   Or use: make logs, make down, make restart, make shell"
else
    echo "⚠️  Backend started but health check failed."
    echo "   Check logs with: docker-compose logs backend"
fi
