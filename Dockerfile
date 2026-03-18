# Use Python 3.11 slim image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATABASE_URL=sqlite:////tmp/dari.db

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Copy database file to /tmp if it exists (optional - seed.py can populate it)
RUN if [ -f listings.db ]; then cp listings.db /tmp/dari.db; fi

# Create necessary directories with proper permissions
RUN mkdir -p /app/data /tmp && chmod 777 /app/data /tmp

# Make entrypoint script executable
RUN chmod +x entrypoint.sh

# Expose port (Railway will override with $PORT)
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8000/api/health')" || exit 1

# Run the application - Use Railway's PORT if available, otherwise default to 8000
CMD ["./entrypoint.sh"]
