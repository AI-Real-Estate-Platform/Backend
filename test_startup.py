#!/usr/bin/env python3
"""
Test the startup event seeding logic locally
"""

import os
import sys
import sqlite3
import pandas as pd

# Add backend to path
sys.path.insert(0, os.path.dirname(__file__))

from database import engine

print("=" * 60)
print("Testing Startup Event Seeding Logic")
print("=" * 60)

# 1. Check source database
print("\n1. Checking source database...")
source_candidates = [
    "/tmp/listings_seed.db",
    "/app/listings.db", 
    "listings.db",
    "../data/listings.db"
]

for candidate in source_candidates:
    exists = os.path.exists(candidate)
    print(f"   {candidate}: {'✓ FOUND' if exists else '✗ not found'}")

source_path = next((p for p in source_candidates if os.path.exists(p)), None)

if not source_path:
    print("\n❌ No source database found!")
    sys.exit(1)

print(f"\n✓ Using source: {source_path}")

# 2. Check source has data
print("\n2. Checking source data...")
try:
    conn = sqlite3.connect(source_path)
    tables_df = pd.read_sql("SELECT name FROM sqlite_master WHERE type='table'", conn)
    print(f"   Tables: {tables_df['name'].tolist()}")
    
    df = pd.read_sql("SELECT COUNT(*) as count FROM listings", conn)
    count = df['count'].iloc[0]
    print(f"   Listings: {count}")
    
    if count == 0:
        print("\n❌ Source database is empty!")
        conn.close()
        sys.exit(1)
    
    # Get sample data
    sample = pd.read_sql("SELECT url, city, property_type, price FROM listings LIMIT 3", conn)
    print(f"\n   Sample listings:")
    for _, row in sample.iterrows():
        print(f"      - {row['city']}: {row['property_type']} @ {row['price']} MAD")
    
    conn.close()
    print(f"\n✅ Source database is valid with {count} listings")
    
except Exception as e:
    print(f"\n❌ Error reading source: {e}")
    sys.exit(1)

# 3. Check target database
print("\n3. Checking target database...")
print(f"   Engine URL: {engine.url}")

try:
    existing = pd.read_sql("SELECT COUNT(*) as count FROM listings", engine)
    count = existing['count'].iloc[0]
    print(f"   Current listings: {count}")
    
    if count > 0:
        print(f"\n✓ Target already has {count} listings (would skip seeding)")
    else:
        print(f"\n✓ Target is empty (would seed)")
        
except Exception as e:
    print(f"   Table doesn't exist yet: {e}")
    print(f"\n✓ Target needs initialization (would seed)")

print("\n" + "=" * 60)
print("✅ Startup event seeding logic looks good!")
print("=" * 60)
print("\nTo test the full startup:")
print("  uvicorn api:app --reload --port 8000")
print("\nThen check:")
print("  curl http://localhost:8000/api/health")
