"""
Migration script to add listing_data column to saved_listings table
Run this once: python backend/migrate_add_listing_data.py
"""
from sqlalchemy import text
from database import engine

def migrate():
    with engine.begin() as conn:
        # Check if column already exists
        if engine.url.get_backend_name() == "sqlite":
            rows = conn.execute(text("PRAGMA table_info(saved_listings)")).mappings().all()
            existing_columns = {row["name"] for row in rows}
            
            if "listing_data" not in existing_columns:
                print("Adding listing_data column to saved_listings table...")
                conn.execute(text("ALTER TABLE saved_listings ADD COLUMN listing_data TEXT"))
                print("✓ Migration completed successfully!")
            else:
                print("✓ Column listing_data already exists, skipping migration.")
        else:
            # For PostgreSQL or other databases
            print("Adding listing_data column to saved_listings table...")
            conn.execute(text("""
                ALTER TABLE saved_listings 
                ADD COLUMN IF NOT EXISTS listing_data TEXT
            """))
            print("✓ Migration completed successfully!")

if __name__ == "__main__":
    migrate()
