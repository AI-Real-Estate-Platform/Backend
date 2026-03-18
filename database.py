import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

load_dotenv()

# We will use SQLite for simplicity, but this is easy to swap to PostgreSQL
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    # Safe fallback for Docker/Railway environments
    # Try /app/data first (Docker volume mount), then /tmp (Railway writable), then local
    if os.path.exists("/app/data"):
        DATABASE_URL = "sqlite:////app/data/dari.db"
    elif os.path.exists("/tmp"):
        DATABASE_URL = "sqlite:////tmp/dari.db"
    else:
        # Local development fallback
        _BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
        _DB_PATH = os.path.join(_BACKEND_DIR, "..", "data", "listings.db")
        DATABASE_URL = f"sqlite:///{os.path.normpath(_DB_PATH)}"

# Railway sometimes injects "postgres://" instead of "postgresql://"
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(
    DATABASE_URL, 
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
