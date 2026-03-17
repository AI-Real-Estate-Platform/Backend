.PHONY: help build up down restart logs shell clean test health docker-build docker-push docker-deploy

# Configuration
DOCKER_USERNAME ?= your-dockerhub-username
IMAGE_NAME = dari-backend
VERSION ?= latest

# Default target
help:
	@echo "Dari Backend - Docker Commands"
	@echo "=============================="
	@echo ""
	@echo "Local Development:"
	@echo "  make build    - Build Docker image"
	@echo "  make up       - Start containers"
	@echo "  make down     - Stop containers"
	@echo "  make restart  - Restart containers"
	@echo "  make logs     - View logs"
	@echo "  make shell    - Access container shell"
	@echo "  make clean    - Remove containers and volumes"
	@echo "  make health   - Check API health"
	@echo "  make test     - Run tests"
	@echo ""
	@echo "Docker Hub Deployment:"
	@echo "  make docker-build   - Build image for Docker Hub"
	@echo "  make docker-push    - Push image to Docker Hub"
	@echo "  make docker-deploy  - Build + Push to Docker Hub"
	@echo ""
	@echo "Configuration:"
	@echo "  DOCKER_USERNAME=$(DOCKER_USERNAME)"
	@echo "  IMAGE_NAME=$(IMAGE_NAME)"
	@echo "  VERSION=$(VERSION)"
	@echo ""
	@echo "Example:"
	@echo "  make docker-deploy DOCKER_USERNAME=ielma VERSION=v1.0.0"

# Build the Docker image
build:
	docker-compose build

# Start containers in detached mode
up:
	docker-compose up -d
	@echo "Backend is starting..."
	@echo "API will be available at http://localhost:8000"
	@echo "Run 'make logs' to view logs"

# Stop containers
down:
	docker-compose down

# Restart containers
restart:
	docker-compose restart
	@echo "Containers restarted"

# View logs (follow mode)
logs:
	docker-compose logs -f backend

# Access container shell
shell:
	docker-compose exec backend bash

# Clean up everything (containers, volumes, images)
clean:
	docker-compose down -v
	docker image prune -f
	@echo "Cleaned up containers, volumes, and images"

# Check API health
health:
	@curl -s http://localhost:8000/api/health | python -m json.tool || echo "API is not responding"

# Run tests inside container
test:
	docker-compose exec backend pytest

# Quick start (build and run)
start: build up
	@echo "Backend started successfully!"
	@sleep 3
	@make health

# ============================================================================
# Docker Hub Deployment Commands
# ============================================================================

# Build image for Docker Hub
docker-build:
	@echo "Building Docker image: $(DOCKER_USERNAME)/$(IMAGE_NAME):$(VERSION)"
	docker build -t $(DOCKER_USERNAME)/$(IMAGE_NAME):$(VERSION) .
	docker tag $(DOCKER_USERNAME)/$(IMAGE_NAME):$(VERSION) $(DOCKER_USERNAME)/$(IMAGE_NAME):latest
	@echo "✅ Image built successfully!"
	@echo "   $(DOCKER_USERNAME)/$(IMAGE_NAME):$(VERSION)"
	@echo "   $(DOCKER_USERNAME)/$(IMAGE_NAME):latest"

# Push image to Docker Hub
docker-push:
	@echo "Pushing to Docker Hub..."
	docker push $(DOCKER_USERNAME)/$(IMAGE_NAME):$(VERSION)
	docker push $(DOCKER_USERNAME)/$(IMAGE_NAME):latest
	@echo "✅ Image pushed successfully!"
	@echo "   https://hub.docker.com/r/$(DOCKER_USERNAME)/$(IMAGE_NAME)"

# Build and push to Docker Hub
docker-deploy: docker-build docker-push
	@echo ""
	@echo "🎉 Deployment complete!"
	@echo ""
	@echo "Your image is now available at:"
	@echo "  $(DOCKER_USERNAME)/$(IMAGE_NAME):$(VERSION)"
	@echo "  $(DOCKER_USERNAME)/$(IMAGE_NAME):latest"
	@echo ""
	@echo "To deploy on Railway:"
	@echo "  1. Go to https://railway.app"
	@echo "  2. New Project → Docker Image"
	@echo "  3. Enter: $(DOCKER_USERNAME)/$(IMAGE_NAME):latest"
	@echo "  4. Add environment variables"
	@echo "  5. Deploy!"

# Test Docker Hub image locally
docker-test:
	@echo "Testing Docker Hub image locally..."
	docker run -d -p 8000:8000 \
		-e AUTH_SECRET_KEY=test-secret \
		--name dari-test \
		$(DOCKER_USERNAME)/$(IMAGE_NAME):$(VERSION)
	@echo "Waiting for container to start..."
	@sleep 5
	@echo "Testing health endpoint..."
	@curl -s http://localhost:8000/api/health | python -m json.tool || echo "Health check failed"
	@echo ""
	@echo "To stop test container: docker stop dari-test && docker rm dari-test"

# Login to Docker Hub
docker-login:
	docker login

# View Docker images
docker-images:
	@docker images | grep $(IMAGE_NAME) || echo "No images found"

# Remove local Docker images
docker-clean:
	docker rmi $(DOCKER_USERNAME)/$(IMAGE_NAME):$(VERSION) || true
	docker rmi $(DOCKER_USERNAME)/$(IMAGE_NAME):latest || true
	@echo "Local images removed"
