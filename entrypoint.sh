#!/bin/bash
set -e

echo "🚀 Starting Dari Backend..."

# Run database migrations/seeding if CSV data exists
if [ -f "../data/test_bulk.csv" ] || [ -f "../data/listings_with_vision.csv" ]; then
    echo "📊 Seeding database with listings..."
    python seed.py || echo "⚠️  Seeding failed or skipped"
else
    echo "ℹ️  No CSV data found, skipping seed"
fi

# Start the application
echo "🌐 Starting uvicorn server on port ${PORT:-8000}..."
exec uvicorn api:app --host 0.0.0.0 --port ${PORT:-8000}
