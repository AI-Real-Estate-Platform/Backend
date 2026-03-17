import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

load_dotenv()

# We will use SQLite for simplicity, but this is easy to swap to PostgreSQL
raw_url = os.getenv("DATABASE_URL")
DATABASE_URL = str(raw_url).strip() if raw_url else ""

if not DATABASE_URL:
    # Compute absolute path so it works regardless of CWD
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
