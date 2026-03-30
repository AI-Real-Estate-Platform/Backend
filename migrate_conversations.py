"""
One-time migration: extend chat_conversations for multi-conversation support.
Run from the backend/ directory:
    python migrate_conversations.py
Safe to run multiple times (idempotent).
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from database import engine
from sqlalchemy import text

def migrate():
    with engine.connect() as conn:
        # 1. Add missing columns if not present
        result = conn.execute(text("PRAGMA table_info(chat_conversations)"))
        existing_cols = {row[1] for row in result}

        if "title" not in existing_cols:
            conn.execute(text("ALTER TABLE chat_conversations ADD COLUMN title VARCHAR"))
            print("Added column: title")

        if "lang" not in existing_cols:
            conn.execute(text(
                "ALTER TABLE chat_conversations ADD COLUMN lang VARCHAR NOT NULL DEFAULT 'EN'"
            ))
            print("Added column: lang")

        # 2. Check for unique index on user_id and remove it by rebuilding the table
        idx_list = conn.execute(text("PRAGMA index_list(chat_conversations)"))
        has_unique = any(
            row[2] == 1 and "user_id" in row[1].lower()
            for row in idx_list
        )

        if has_unique:
            print("Rebuilding table to drop unique constraint on user_id...")
            conn.execute(text("""
                CREATE TABLE chat_conversations_new (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    title VARCHAR,
                    lang VARCHAR NOT NULL DEFAULT 'EN',
                    messages TEXT NOT NULL DEFAULT '[]',
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(user_id) REFERENCES users(id)
                )
            """))
            conn.execute(text("""
                INSERT INTO chat_conversations_new (id, user_id, title, lang, messages, updated_at)
                SELECT id, user_id,
                       title,
                       COALESCE(lang, 'EN'),
                       messages,
                       updated_at
                FROM chat_conversations
            """))
            conn.execute(text("DROP TABLE chat_conversations"))
            conn.execute(text(
                "ALTER TABLE chat_conversations_new RENAME TO chat_conversations"
            ))
            print("Table rebuilt without unique constraint.")
        else:
            print("No unique constraint found — skipping rebuild.")

        conn.commit()
        print("Migration complete.")

if __name__ == "__main__":
    migrate()
