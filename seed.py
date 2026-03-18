"""
seed.py
───────
Seeds the database with listings from CSV files.
Safe to run multiple times - skips existing entries.

Usage:
  python seed.py                    # Auto-detect best CSV file
  python seed.py path/to/file.csv   # Use specific CSV
  
Environment:
  DATABASE_URL - Database connection string (optional, uses default if not set)
"""

import sys
import os
import math
import pandas as pd

# Ensure imports work from any directory
_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_BACKEND_DIR)
for _p in (_PROJECT_DIR, _BACKEND_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from backend.database import engine, Base, SessionLocal
from backend.models import Listing

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

BOOLEAN_COLS = [
    "has_ascenseur", "has_terrasse", "has_piscine", "has_garage",
    "has_meuble", "has_climatisation", "has_securite", "has_jardin",
    "has_vue_mer", "has_cuisine_equipee", "has_concierge", "has_salon_europeen",
    "has_salon_marocain", "has_antenne_parabolique", "has_double_vitrage",
    "has_facade_exterieure", "has_cheminee", "has_machine_laver", "has_porte_blindee",
    "has_chauffage_central", "has_chambre_rangement", "has_entre_seul", "has_four",
    "has_micro_ondes", "has_refrigerateur",
]


def sanitize(val):
    """Return None for NaN/empty, else the raw value."""
    if val is None:
        return None
    if isinstance(val, float) and math.isnan(val):
        return None
    s = str(val).strip()
    return None if s in ("", "nan", "None", "<NA>") else s


def parse_amenities(raw: str) -> dict:
    """Parse 'Jardin | Piscine | ...' into {has_jardin: True, ...}."""
    result = {col: False for col in BOOLEAN_COLS}
    if not raw or str(raw).strip() in ("", "nan", "None"):
        return result
    items = [item.strip().lower() for item in str(raw).split("|")]
    for item in items:
        for keyword, col in AMENITY_KEYWORD_MAP.items():
            if keyword in item:
                result[col] = True
    return result


def determine_transaction_type(row: pd.Series) -> str:
    """Infer transaction type from URL or other fields."""
    url = str(row.get("url", "")).lower()
    if "location" in url or "louer" in url or "rent" in url:
        return "Location"
    return "Vente"


def seed_database(csv_path: str):
    """Seed database from CSV file. Safe to run multiple times."""
    print(f"🌱 Seeding database from: {csv_path}")
    print(f"📊 Database: {engine.url}")
    
    if not os.path.exists(csv_path):
        print(f"❌ Error: CSV file not found: {csv_path}")
        return False
    
    # Create tables if they don't exist
    print("📋 Creating/verifying tables...")
    Base.metadata.create_all(bind=engine)
    
    # Read CSV
    print("📖 Reading CSV...")
    try:
        df = pd.read_csv(csv_path, sep=";", encoding="utf-8-sig")
    except Exception as e:
        print(f"❌ Error reading CSV: {e}")
        return False
    
    print(f"   → {len(df)} rows found")
    
    # Numeric coercions
    df["price"] = pd.to_numeric(df.get("price"), errors="coerce")
    if "surface" in df.columns:
        df["surface"] = (
            df["surface"]
            .astype(str)
            .str.replace(r"\s*m[²2].*", "", regex=True)
            .str.replace(",", ".")
            .str.strip()
        )
        df["surface"] = pd.to_numeric(df["surface"], errors="coerce")
    df["rooms"] = pd.to_numeric(df.get("rooms"), errors="coerce")
    df["bedrooms"] = pd.to_numeric(df.get("bedrooms"), errors="coerce")
    df["bathrooms"] = pd.to_numeric(df.get("bathrooms"), errors="coerce")
    
    # De-duplicate by URL
    df = df.drop_duplicates(subset="url", keep="first").reset_index(drop=True)
    print(f"   → {len(df)} unique rows after de-duplication")
    
    db = SessionLocal()
    
    # Get existing URLs to skip
    existing_urls = {r[0] for r in db.query(Listing.url).all()}
    print(f"   → {len(existing_urls)} listings already in database")
    
    items = []
    skipped = 0
    
    for _, row in df.iterrows():
        url = str(row.get("url", "")).strip()
        if url in existing_urls:
            skipped += 1
            continue
        
        amenity_flags = parse_amenities(row.get("amenities", ""))
        
        raw_images = sanitize(row.get("images", "")) or ""
        images_str = raw_images.replace(" ; ", " | ").replace(";", " | ")
        
        surface_raw = row.get("surface")
        try:
            surface_val = float(surface_raw) if surface_raw == surface_raw else None
        except (ValueError, TypeError):
            surface_val = None
        
        item = Listing(
            url=url,
            property_type=sanitize(row.get("property_type")) or "Inconnu",
            transaction_type=determine_transaction_type(row),
            city=sanitize(row.get("city")) or "Inconnu",
            neighborhood=sanitize(row.get("neighborhood")) or "",
            address=sanitize(row.get("address")) or "",
            price=sanitize(row.get("price")),
            surface=surface_val,
            rooms=int(float(str(row["rooms"]))) if sanitize(row.get("rooms")) else None,
            bedrooms=int(float(str(row["bedrooms"]))) if sanitize(row.get("bedrooms")) else None,
            bathrooms=int(float(str(row["bathrooms"]))) if sanitize(row.get("bathrooms")) else None,
            state=sanitize(row.get("state")) or "",
            standing=sanitize(row.get("standing")) or "",
            description=sanitize(row.get("description")) or "",
            images=images_str,
            phone_number=sanitize(row.get("phone_number")) or "",
            map_link=sanitize(row.get("map_link")) or "",
            visual_style=sanitize(row.get("visual_style")),
            natural_light=sanitize(row.get("natural_light")),
            visual_condition=sanitize(row.get("visual_condition")),
            furnishing_status=sanitize(row.get("furnishing_status")),
            floor_material=sanitize(row.get("floor_material")),
            dominant_view=sanitize(row.get("dominant_view")),
            architectural_vibe=sanitize(row.get("architectural_vibe")),
            color_palette=sanitize(row.get("color_palette")),
            **amenity_flags,
        )
        items.append(item)
        
        # Batch insert
        if len(items) >= 500:
            db.bulk_save_objects(items)
            db.commit()
            print(f"   ✓ Committed batch of {len(items)}...")
            items = []
    
    if items:
        db.bulk_save_objects(items)
        db.commit()
        print(f"   ✓ Committed final batch of {len(items)}...")
    
    db.close()
    
    total_inserted = len(df) - skipped
    print(f"\n✅ Seeding complete!")
    print(f"   📥 {total_inserted} new listings inserted")
    print(f"   ⏭️  {skipped} listings skipped (already existed)")
    print(f"   📊 Total in database: {len(existing_urls) + total_inserted}")
    
    return True


def find_csv_file():
    """Auto-detect best available CSV file."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(base_dir, "..", "data")
    
    candidates = [
        os.path.join(data_dir, "test_bulk.csv"),
        os.path.join(data_dir, "listings_with_vision.csv"),
        os.path.join(data_dir, "listings_clean_with_images.csv"),
        os.path.join(data_dir, "listings_clean.csv"),
    ]
    
    for csv_file in candidates:
        if os.path.isfile(csv_file):
            return csv_file
    
    return None


if __name__ == "__main__":
    if len(sys.argv) > 1:
        csv_path = sys.argv[1]
    else:
        csv_path = find_csv_file()
        if not csv_path:
            print("❌ No CSV file found in data/ directory")
            print("   Please provide a CSV file path as argument:")
            print("   python seed.py path/to/listings.csv")
            sys.exit(1)
    
    success = seed_database(csv_path)
    sys.exit(0 if success else 1)
