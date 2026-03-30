"""
Migration: add agent_id column to bookings table.
Run once: python backend/migrate_add_agent_booking.py
"""
from sqlalchemy import text
from database import engine


def migrate():
    with engine.begin() as conn:
        if engine.url.get_backend_name() == "sqlite":
            rows = conn.execute(text("PRAGMA table_info(bookings)")).mappings().all()
            existing = {r["name"] for r in rows}
            if "agent_id" not in existing:
                print("Adding agent_id column to bookings table…")
                conn.execute(text("ALTER TABLE bookings ADD COLUMN agent_id INTEGER REFERENCES users(id)"))
                print("✓ Done.")
            else:
                print("✓ agent_id already exists, skipping.")
        else:
            conn.execute(text(
                "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS agent_id INTEGER REFERENCES users(id)"
            ))
            print("✓ Done.")


if __name__ == "__main__":
    migrate()
