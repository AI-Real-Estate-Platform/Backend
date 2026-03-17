#!/bin/bash

# Dari Backend - Docker Hub Deployment Script
# This script builds and pushes your image to Docker Hub

set -e

echo "🐳 Dari Backend - Docker Hub Deployment"
echo "========================================"
echo ""

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo "❌ Docker is not installed. Please install Docker first."
    exit 1
fi

# Get Docker Hub username
read -p "Enter your Docker Hub username: " DOCKER_USERNAME

if [ -z "$DOCKER_USERNAME" ]; then
    echo "❌ Docker Hub username is required"
    exit 1
fi

# Get version tag (optional)
read -p "Enter version tag (default: latest): " VERSION
VERSION=${VERSION:-latest}

IMAGE_NAME="dari-backend"
FULL_IMAGE="$DOCKER_USERNAME/$IMAGE_NAME"

echo ""
echo "Configuration:"
echo "  Docker Hub Username: $DOCKER_USERNAME"
echo "  Image Name: $IMAGE_NAME"
echo "  Version: $VERSION"
echo "  Full Image: $FULL_IMAGE:$VERSION"
echo ""

# Confirm
read -p "Continue with deployment? (y/n): " CONFIRM
if [ "$CONFIRM" != "y" ]; then
    echo "Deployment cancelled"
    exit 0
fi

echo ""
echo "📦 Step 1: Building Docker image..."
docker build -t $FULL_IMAGE:$VERSION .

if [ "$VERSION" != "latest" ]; then
    echo "📦 Tagging as latest..."
    docker tag $FULL_IMAGE:$VERSION $FULL_IMAGE:latest
fi

echo ""
echo "✅ Build complete!"
echo ""

# Test locally (optional)
read -p "Test image locally before pushing? (y/n): " TEST
if [ "$TEST" = "y" ]; then
    echo ""
    echo "🧪 Testing image locally..."
    docker run -d -p 8000:8000 \
        -e AUTH_SECRET_KEY=test-secret \
        --name dari-test \
        $FULL_IMAGE:$VERSION
    
    echo "Waiting for container to start..."
    sleep 5
    
    echo "Testing health endpoint..."
    if curl -s http://localhost:8000/api/health > /dev/null; then
        echo "✅ Health check passed!"
    else
        echo "⚠️  Health check failed, but continuing..."
    fi
    
    echo "Stopping test container..."
    docker stop dari-test > /dev/null 2>&1
    docker rm dari-test > /dev/null 2>&1
    echo ""
fi

# Login to Docker Hub
echo "🔐 Step 2: Logging in to Docker Hub..."
docker login

echo ""
echo "📤 Step 3: Pushing to Docker Hub..."
docker push $FULL_IMAGE:$VERSION

if [ "$VERSION" != "latest" ]; then
    docker push $FULL_IMAGE:latest
fi

echo ""
echo "🎉 Deployment Complete!"
echo ""
echo "Your image is now available at:"
echo "  https://hub.docker.com/r/$DOCKER_USERNAME/$IMAGE_NAME"
echo ""
echo "Image tags:"
echo "  $FULL_IMAGE:$VERSION"
if [ "$VERSION" != "latest" ]; then
    echo "  $FULL_IMAGE:latest"
fi
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📋 Next Steps - Deploy to Railway:"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "1. Go to https://railway.app/dashboard"
echo "2. Click 'New Project'"
echo "3. Select 'Docker Image'"
echo "4. Enter: $FULL_IMAGE:$VERSION"
echo "5. Add environment variables:"
echo "   - AUTH_SECRET_KEY=<generate-secure-key>"
echo "   - GEMINI_API_KEY=<optional>"
echo "   - CORS_ORIGINS=<your-frontend-url>"
echo "6. Click 'Deploy'"
echo ""
echo "Generate secure key with:"
echo "  python -c \"import secrets; print(secrets.token_urlsafe(32))\""
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
