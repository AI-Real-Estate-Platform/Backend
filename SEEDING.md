# Database Seeding Guide

## Overview

The `seed.py` script populates your database with real estate listings from CSV files. It's safe to run multiple times and will skip existing entries.

## Usage

### Local Development

```bash
cd backend

# Auto-detect and use best available CSV file
python seed.py

# Use specific CSV file
python seed.py ../data/test_bulk.csv
```

### Docker/Railway Deployment

The database is automatically seeded on container startup via `entrypoint.sh`:

1. Container starts
2. `entrypoint.sh` checks for CSV files in `../data/`
3. If found, runs `python seed.py`
4. Starts uvicorn server

## CSV File Priority

The script looks for CSV files in this order:

1. `data/test_bulk.csv` (latest scraped data)
2. `data/listings_with_vision.csv` (enriched with AI vision data)
3. `data/listings_clean_with_images.csv`
4. `data/listings_clean.csv`

## CSV Format

Expected columns (semicolon-separated):

- `url` - Unique listing URL (primary key)
- `price` - Price in MAD
- `city` - City name (Casablanca, Marrakech, etc.)
- `neighborhood` - Neighborhood/district
- `address` - Full address
- `surface` - Surface area (e.g., "120 m²")
- `property_type` - Type (Appartement, Villa, etc.)
- `rooms` - Total rooms
- `bedrooms` - Number of bedrooms
- `bathrooms` - Number of bathrooms
- `state` - Property condition
- `standing` - Quality level
- `amenities` - Pipe-separated list (e.g., "Parking | Piscine | Jardin")
- `images` - Pipe-separated image URLs
- `description` - Property description
- `phone_number` - Contact number
- `map_link` - Google Maps link

### Optional Visual Attributes

- `visual_style` - Modern, Traditional, etc.
- `natural_light` - Bright, Moderate, Dim
- `visual_condition` - Excellent, Good, Fair
- `furnishing_status` - Furnished, Unfurnished
- `floor_material` - Tile, Wood, Marble, etc.
- `dominant_view` - Urban, Garden, Sea, etc.
- `architectural_vibe` - Contemporary, Classic, etc.
- `color_palette` - Neutral, Warm, Cool, etc.

## Amenity Parsing

The script automatically parses the `amenities` column and creates boolean flags:

- `has_ascenseur` - Elevator
- `has_terrasse` - Terrace/Balcony
- `has_piscine` - Pool
- `has_garage` - Parking/Garage
- `has_meuble` - Furnished
- `has_climatisation` - Air conditioning
- `has_securite` - Security
- `has_jardin` - Garden
- `has_vue_mer` - Sea view
- `has_cuisine_equipee` - Equipped kitchen
- And 15+ more amenities...

## Transaction Type Detection

The script automatically determines if a listing is for sale or rent by checking the URL:

- Contains "location", "louer", or "rent" → `Location` (Rent)
- Otherwise → `Vente` (Sale)

## Database Safety

- **Idempotent**: Safe to run multiple times
- **Deduplication**: Skips listings already in database (by URL)
- **Batch inserts**: Commits in batches of 500 for performance
- **Error handling**: Continues on individual row errors

## Troubleshooting

### No CSV file found

```bash
❌ No CSV file found in data/ directory
```

**Solution**: Place a CSV file in the `data/` directory or specify the path:

```bash
python seed.py /path/to/your/listings.csv
```

### Database connection error

Check your `DATABASE_URL` environment variable or ensure the default SQLite path is writable.

### CSV parsing errors

Ensure your CSV uses semicolon (`;`) as separator and UTF-8 encoding.

## Manual Database Reset

To completely reset and re-seed:

```bash
# Delete existing database
rm -f listings.db
rm -f /tmp/dari.db

# Re-seed
python seed.py
```

## Production Deployment

For Railway/production:

1. Ensure CSV files are in the `data/` directory
2. Build and push Docker image
3. Database will auto-seed on first deployment
4. Subsequent deployments skip existing listings

## Monitoring

Check seeding status in logs:

```bash
# Railway
railway logs

# Docker
docker logs <container-id>

# Look for:
🌱 Seeding database from: ...
✅ Seeding complete!
📥 X new listings inserted
```
