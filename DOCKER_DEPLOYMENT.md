# Docker Deployment Guide - Dari Backend

This guide explains how to deploy the Dari backend using Docker.

## Prerequisites

- Docker installed (version 20.10+)
- Docker Compose installed (version 2.0+)

## Quick Start

### 1. Setup Environment Variables

```bash
# Copy the example environment file
cp .env.example .env

# Edit .env and set your values
nano .env  # or use your preferred editor
```

**Important**: Change `AUTH_SECRET_KEY` to a secure random string in production!

### 2. Build and Run

```bash
# Build the Docker image
docker-compose build

# Start the container
docker-compose up -d

# View logs
docker-compose logs -f backend
```

The API will be available at `http://localhost:8000`

### 3. Verify Deployment

```bash
# Check health endpoint
curl http://localhost:8000/api/health

# Expected response:
# {"status":"ok","listings_loaded":449,"data_file":"..."}
```

## Docker Commands

### Basic Operations

```bash
# Start services
docker-compose up -d

# Stop services
docker-compose down

# Restart services
docker-compose restart

# View logs
docker-compose logs -f backend

# View last 100 lines
docker-compose logs --tail=100 backend
```

### Container Management

```bash
# Access container shell
docker-compose exec backend bash

# Run Python commands
docker-compose exec backend python -c "print('Hello')"

# Check running containers
docker ps

# Remove all containers and volumes
docker-compose down -v
```

### Image Management

```bash
# Rebuild image (after code changes)
docker-compose build --no-cache

# Remove old images
docker image prune -a

# View images
docker images
```

## Production Deployment

### 1. Security Hardening

Edit `docker-compose.yml` for production:

```yaml
environment:
  # Use strong secret key
  - AUTH_SECRET_KEY=${AUTH_SECRET_KEY}
  
  # Restrict CORS
  - CORS_ORIGINS=https://yourdomain.com
  
  # Use PostgreSQL instead of SQLite
  - DATABASE_URL=postgresql://user:pass@postgres:5432/dari_db
```

### 2. Use PostgreSQL (Recommended for Production)

Uncomment the PostgreSQL service in `docker-compose.yml`:

```yaml
services:
  backend:
    environment:
      - DATABASE_URL=postgresql://dari:dari_password@postgres:5432/dari_db
    depends_on:
      - postgres

  postgres:
    image: postgres:15-alpine
    environment:
      - POSTGRES_USER=dari
      - POSTGRES_PASSWORD=dari_password
      - POSTGRES_DB=dari_db
    volumes:
      - postgres_data:/var/lib/postgresql/data

volumes:
  postgres_data:
```

### 3. Enable HTTPS

Use a reverse proxy like Nginx or Traefik:

```yaml
# docker-compose.yml
services:
  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
      - ./ssl:/etc/nginx/ssl:ro
    depends_on:
      - backend
```

### 4. Resource Limits

Add resource constraints:

```yaml
services:
  backend:
    deploy:
      resources:
        limits:
          cpus: '2'
          memory: 2G
        reservations:
          cpus: '1'
          memory: 1G
```

## Data Persistence

### Database

SQLite database is stored in `./data/dari.db` and persisted via Docker volume.

### CSV Data Files

The `../data` directory (containing listings CSV files) is mounted read-only.

### Backup Database

```bash
# Backup SQLite database
docker-compose exec backend cp /app/data/dari.db /app/data/dari.db.backup

# Copy backup to host
docker cp dari-backend:/app/data/dari.db.backup ./backup.db

# Restore from backup
docker cp ./backup.db dari-backend:/app/data/dari.db
docker-compose restart backend
```

## Monitoring

### Health Checks

Docker automatically monitors the `/api/health` endpoint every 30 seconds.

```bash
# Check container health
docker ps
# Look for "healthy" status
```

### Logs

```bash
# Real-time logs
docker-compose logs -f backend

# Export logs to file
docker-compose logs backend > backend.log

# Filter logs by time
docker-compose logs --since 1h backend
```

### Resource Usage

```bash
# View resource usage
docker stats dari-backend

# Detailed container info
docker inspect dari-backend
```

## Troubleshooting

### Container Won't Start

```bash
# Check logs
docker-compose logs backend

# Check if port is already in use
lsof -i :8000

# Rebuild from scratch
docker-compose down -v
docker-compose build --no-cache
docker-compose up -d
```

### Database Issues

```bash
# Reset database
docker-compose down
rm -rf ./data/dari.db
docker-compose up -d
```

### Permission Issues

```bash
# Fix data directory permissions
sudo chown -R $USER:$USER ./data
chmod -R 755 ./data
```

### Out of Memory

```bash
# Increase memory limit in docker-compose.yml
deploy:
  resources:
    limits:
      memory: 4G
```

## Development Mode

For development with hot reload:

```yaml
# docker-compose.yml
services:
  backend:
    volumes:
      - .:/app  # Mount source code
    command: uvicorn api:app --host 0.0.0.0 --port 8000 --reload
```

## CI/CD Integration

### GitHub Actions Example

```yaml
name: Build and Push Docker Image

on:
  push:
    branches: [main]

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Build Docker image
        run: |
          cd backend
          docker build -t dari-backend:latest .
      
      - name: Push to registry
        run: |
          docker tag dari-backend:latest registry.example.com/dari-backend:latest
          docker push registry.example.com/dari-backend:latest
```

## Environment Variables Reference

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `AUTH_SECRET_KEY` | JWT secret key | `dev-secret-change-me` | Yes |
| `GEMINI_API_KEY` | Google Gemini API key | - | No |
| `DATABASE_URL` | Database connection string | `sqlite:///./data/dari.db` | No |
| `CORS_ORIGINS` | Allowed CORS origins | `*` | No |

## Support

For issues or questions:
1. Check logs: `docker-compose logs backend`
2. Verify health: `curl http://localhost:8000/api/health`
3. Review this documentation
4. Check the main project README
