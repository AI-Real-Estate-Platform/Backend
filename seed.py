"""
seed.py
───────
Seeds the database with listings from a source SQLite database.
Safe to run multiple times - skips existing entries.

Usage:
  python seed.py
  
Environment:
  DATABASE_URL - Database connection string (optional, uses default if not set)
"""

import sys
import os
import sqlite3
import pandas as pd

print("=== SEED SCRIPT STARTING ===", flush=True)

# Ensure imports work from any directory
_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_BACKEND_DIR)
for _p in (_PROJECT_DIR, _BACKEND_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from database import engine, Base, SessionLocal
from models import Listing

print(f"✓ Imports successful", flush=True)
print(f"✓ Engine URL: {engine.url}", flush=True)

# Amenity keyword → boolean column mapping
AMENITY_KEYWORD_MAP = {
    "ascenseur": "has_ascenseur",
    "terrasse": "has_terrasse",
    "balcon": "has_terrasse",
    "piscine": "has_piscine",
    "parking": "has_garage",
    "garage": "has_garage",
    "meublé": "has_meuble",
    "meuble": "has_meuble",
    "climatisation": "has_climatisation",
    "clim": "has_climatisation",
    "sécurité": "has_securite",
    "securite": "has_securite",
    "security": "has_securite",
    "jardin": "has_jardin",
    "vue mer": "has_vue_mer",
    "cuisine équipée": "has_cuisine_equipee",
    "cuisine equipee": "has_cuisine_equipee",
    "concierge": "has_concierge",
    "salon européen": "has_salon_europeen",
    "salon marocain": "has_salon_marocain",
    "antenne parabolique": "has_antenne_parabolique",
    "double vitrage": "has_double_vitrage",
    "façade extérieure": "has_facade_exterieure",
    "facade exterieure": "has_facade_exterieure",
    "cheminée": "has_cheminee",
    "cheminee": "has_cheminee",
    "machine à laver": "has_machine_laver",
    "machine a laver": "has_machine_laver",
    "porte blindée": "has_porte_blindee",
    "porte blindee": "has_porte_blindee",
    "chauffage central": "has_chauffage_central",
    "chambre rangement": "has_chambre_rangement",
    "entre-seul": "has_entre_seul",
    "entre seul": "has_entre_seul",
    "four": "has_four",
    "micro-ondes": "has_micro_ondes",
    "micro ondes": "has_micro_ondes",
    "réfrigérateur": "has_refrigerateur",
    "refrigerateur": "has_refrigerateur",
}


def seed_database():
    """Seed database from source SQLite file. Safe to run multiple times."""
    print("\n🔍 DEBUGGING FILE SYSTEM:", flush=True)
    
    # Check what files exist
    for path in ["/app", "/tmp", "."]:
        if os.path.exists(path):
            try:
                files = os.listdir(path)
                print(f"   Files in {path}: {files[:10]}", flush=True)  # First 10 files
            except Exception as e:
                print(f"   Cannot list {path}: {e}", flush=True)
    
    print(f"\n📊 Database: {engine.url}", flush=True)
    
    # Create tables if they don't exist
    print("📋 Creating/verifying tables...", flush=True)
    Base.metadata.create_all(bind=engine)
    
    # Check if already seeded
    try:
        db = SessionLocal()
        existing_count = db.query(Listing).count()
        db.close()
        print(f"   → Current listings in database: {existing_count}", flush=True)
        
        if existing_count > 0:
            print(f"✅ Database already seeded with {existing_count} listings. Skipping.", flush=True)
            return True
    except Exception as e:
        print(f"   → Could not check existing count: {e}", flush=True)
    
    # Find source database
    source_candidates = [
        "/tmp/listings_seed.db",
        "/tmp/dari.db",
        "/app/listings.db",
        "listings.db",
        "../data/listings.db",
    ]
    
    source_path = None
    for path in source_candidates:
        exists = os.path.exists(path)
        print(f"   Checking {path}: {'✓ FOUND' if exists else '✗ not found'}", flush=True)
        if exists and not source_path:
            source_path = path
    
    if not source_path:
        print("❌ No source database found! Cannot seed.", flush=True)
        print("   Looked in:", source_candidates, flush=True)
        return False
    
    print(f"\n📖 Reading from source: {source_path}", flush=True)
    
    try:
        # Connect to source database
        source_conn = sqlite3.connect(source_path)
        
        # Check what tables exist
        tables_df = pd.read_sql("SELECT name FROM sqlite_master WHERE type='table'", source_conn)
        tables = tables_df['name'].tolist()
        print(f"   → Tables in source DB: {tables}", flush=True)
        
        if 'listings' not in tables:
            print(f"❌ No 'listings' table in source database!", flush=True)
            source_conn.close()
            return False
        
        # Read listings
        df = pd.read_sql("SELECT * FROM listings", source_conn)
        source_conn.close()
        
        print(f"   → Rows found: {len(df)}", flush=True)
        
        if df.empty:
            print("❌ Source database has no listings.", flush=True)
            return False
        
        # Show sample columns
        print(f"   → Columns: {list(df.columns)[:10]}...", flush=True)
        
        # Write to target database
        print(f"\n💾 Writing {len(df)} listings to database...", flush=True)
        df.to_sql("listings", engine, if_exists="replace", index=False)
        
        print(f"✅ Seeding complete! {len(df)} listings inserted.", flush=True)
        return True
        
    except Exception as e:
        print(f"❌ Error during seeding: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    print("🌱 Starting database seed...", flush=True)
    success = seed_database()
    
    if success:
        print("\n✅ Seed completed successfully!", flush=True)
        sys.exit(0)
    else:
        print("\n⚠️  Seed failed or skipped", flush=True)
        sys.exit(0)  # Don't fail the deployment, just warn
