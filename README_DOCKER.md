# Dari Backend - Docker Setup

Complete Docker containerization for the Dari real estate recommendation backend.

## 📦 What's Included

- **Dockerfile**: Production-ready Python 3.11 image
- **docker-compose.yml**: Service orchestration
- **Makefile**: Convenient command shortcuts
- **start.sh**: Automated setup script
- **Health checks**: Automatic monitoring
- **Volume persistence**: Database and data files

## 🚀 Quick Start

### Option 1: Using the Start Script (Recommended)

```bash
./start.sh
```

This will:
1. Check Docker installation
2. Create `.env` file from template
3. Build the Docker image
4. Start the container
5. Verify the API is running

### Option 2: Using Make Commands

```bash
make start    # Build and start
make logs     # View logs
make health   # Check API health
```

### Option 3: Using Docker Compose Directly

```bash
# Setup
cp .env.example .env

# Build and start
docker-compose build
docker-compose up -d

# Check logs
docker-compose logs -f backend
```

## 📋 Available Commands

### Make Commands (Easiest)

```bash
make help      # Show all commands
make build     # Build Docker image
make up        # Start containers
make down      # Stop containers
make restart   # Restart containers
make logs      # View logs (follow mode)
make shell     # Access container shell
make clean     # Remove everything
make health    # Check API health
make start     # Build + start + health check
```

### Docker Compose Commands

```bash
docker-compose up -d              # Start in background
docker-compose down               # Stop containers
docker-compose logs -f backend    # View logs
docker-compose restart            # Restart
docker-compose exec backend bash  # Shell access
docker-compose ps                 # List containers
```

## 🔧 Configuration

### Environment Variables

Edit `.env` file:

```bash
# Required: Change this in production!
AUTH_SECRET_KEY=your-super-secret-key-here

# Optional: For AI chat features
GEMINI_API_KEY=your-gemini-api-key

# Database (default: SQLite)
DATABASE_URL=sqlite:///./data/dari.db

# CORS (adjust for your frontend)
CORS_ORIGINS=http://localhost:3000,http://localhost:5173
```

### Port Configuration

Default port is `8000`. To change:

```yaml
# docker-compose.yml
ports:
  - "9000:8000"  # Host:Container
```

## 📊 Data Persistence

### Database
- SQLite database stored in `./data/dari.db`
- Persisted via Docker volume
- Survives container restarts

### CSV Data Files
- Mounted from `../data` directory
- Read-only access
- Contains property listings

## 🏥 Health Monitoring

### Automatic Health Checks

Docker automatically checks `/api/health` every 30 seconds:

```bash
# View health status
docker ps
# Look for "healthy" in STATUS column
```

### Manual Health Check

```bash
# Using Make
make health

# Using curl
curl http://localhost:8000/api/health

# Expected response:
{
  "status": "ok",
  "listings_loaded": 449,
  "data_file": "..."
}
```

## 🔍 Troubleshooting

### Container Won't Start

```bash
# Check logs
docker-compose logs backend

# Rebuild from scratch
docker-compose down -v
docker-compose build --no-cache
docker-compose up -d
```

### Port Already in Use

```bash
# Find process using port 8000
lsof -i :8000

# Kill the process or change port in docker-compose.yml
```

### Database Issues

```bash
# Reset database
docker-compose down
rm -rf ./data/dari.db
docker-compose up -d
```

### Permission Errors

```bash
# Fix data directory permissions
sudo chown -R $USER:$USER ./data
chmod -R 755 ./data
```

## 🚀 Production Deployment

### 1. Security

```bash
# Generate strong secret key
python -c "import secrets; print(secrets.token_urlsafe(32))"

# Update .env
AUTH_SECRET_KEY=<generated-key>
CORS_ORIGINS=https://yourdomain.com
```

### 2. Use PostgreSQL (Recommended)

Uncomment PostgreSQL service in `docker-compose.yml`:

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

### 3. Add Resource Limits

```yaml
services:
  backend:
    deploy:
      resources:
        limits:
          cpus: '2'
          memory: 2G
```

### 4. Enable HTTPS

Use Nginx or Traefik as reverse proxy:

```yaml
services:
  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
      - ./ssl:/etc/nginx/ssl:ro
```

## 📦 Backup & Restore

### Backup Database

```bash
# Create backup
docker-compose exec backend cp /app/data/dari.db /app/data/dari.db.backup

# Copy to host
docker cp dari-backend:/app/data/dari.db.backup ./backup-$(date +%Y%m%d).db
```

### Restore Database

```bash
# Copy backup to container
docker cp ./backup.db dari-backend:/app/data/dari.db

# Restart
docker-compose restart backend
```

## 🔄 Updates & Maintenance

### Update Code

```bash
# Pull latest code
git pull

# Rebuild and restart
docker-compose down
docker-compose build
docker-compose up -d
```

### Update Dependencies

```bash
# Edit requirements.txt
# Then rebuild
docker-compose build --no-cache
docker-compose up -d
```

### View Logs

```bash
# Real-time logs
make logs

# Last 100 lines
docker-compose logs --tail=100 backend

# Export to file
docker-compose logs backend > backend.log
```

## 📈 Monitoring

### Resource Usage

```bash
# View stats
docker stats dari-backend

# Detailed info
docker inspect dari-backend
```

### Container Status

```bash
# List containers
docker ps

# View all (including stopped)
docker ps -a
```

## 🧪 Development Mode

For development with hot reload:

```yaml
# docker-compose.yml
services:
  backend:
    volumes:
      - .:/app
    command: uvicorn api:app --host 0.0.0.0 --port 8000 --reload
```

## 📚 Additional Resources

- [Docker Documentation](https://docs.docker.com/)
- [Docker Compose Documentation](https://docs.docker.com/compose/)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [Full Deployment Guide](./DOCKER_DEPLOYMENT.md)

## 🆘 Support

If you encounter issues:

1. Check logs: `make logs`
2. Verify health: `make health`
3. Review [DOCKER_DEPLOYMENT.md](./DOCKER_DEPLOYMENT.md)
4. Check Docker status: `docker ps`

## 📝 License

Same as the main project.
