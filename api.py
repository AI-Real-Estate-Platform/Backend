"""
Dari — Real Estate Recommender API
────────────────────────────────────
FastAPI backend that bridges the Python recommender engine with the React
Prototype frontend.

Endpoints
  POST /api/recommend       Accept interview filters → return ranked listings
  POST /api/swipe           Accept liked/disliked URLs → update scoring weights
  GET  /api/neighborhoods   Return available neighborhoods for a city
  GET  /api/price-range     Return price statistics for current filters

Usage
  source .venv/bin/activate
  uvicorn api:app --reload --port 8000

Dependencies (add to venv)
  pip install fastapi uvicorn[standard]
"""

import json
import os
import pandas as _pd
import random
import re
import secrets
import smtplib
import sys
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

import google.auth
from google import genai        
from google.genai import types

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, constr, field_validator
from sqlalchemy.orm import Session
from sqlalchemy import text

# ── Import recommender (same directory) ──────────────────────────────────────

sys.path.insert(0, os.path.dirname(__file__))

# Prefer the file with the most recent scraped fields
_BASE_DIR = os.path.dirname(__file__)
_DATA_DIR = os.path.join(_BASE_DIR, "..", "data")
_TEST_BULK_CLEAN   = os.path.join(_DATA_DIR, "test_bulk_clean.csv")
_CLEAN_WITH_VISION = os.path.join(_DATA_DIR, "listings_with_vision.csv")
_CLEAN_WITH_IMAGES = os.path.join(_DATA_DIR, "listings_clean_with_images.csv")
_CLEAN             = os.path.join(_DATA_DIR, "listings_clean.csv")

import recommender as _rec_module
if os.path.isfile(_TEST_BULK_CLEAN):
    _rec_module.DATA_PATH = _TEST_BULK_CLEAN
elif os.path.isfile(_CLEAN_WITH_VISION):
    _rec_module.DATA_PATH = _CLEAN_WITH_VISION
elif os.path.isfile(_CLEAN_WITH_IMAGES):
    _rec_module.DATA_PATH = _CLEAN_WITH_IMAGES
else:
    _rec_module.DATA_PATH = _CLEAN

from recommender import Recommender, UserPreferences, AMENITY_COLS  # noqa: E402
from database import Base, engine, get_db  # noqa: E402
from models import User, SavedListing, Booking, ViewHistory, ChatConversation, AgentListing, ListingLike  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# App setup
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(title="Dari Recommender API", version="1.0.0")

# Ensure tables exist (SQLite dev-friendly)
Base.metadata.create_all(bind=engine)


def _ensure_user_columns() -> None:
    if engine.url.get_backend_name() != "sqlite":
        return
    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(users)")).mappings().all()
        if not rows:
            return
        existing = {row["name"] for row in rows}
        if "reset_token" not in existing:
            conn.execute(text("ALTER TABLE users ADD COLUMN reset_token VARCHAR"))
        if "reset_token_expires" not in existing:
            conn.execute(text("ALTER TABLE users ADD COLUMN reset_token_expires DATETIME"))
        if "role" not in existing:
            conn.execute(text("ALTER TABLE users ADD COLUMN role VARCHAR NOT NULL DEFAULT 'client'"))
        if "phone" not in existing:
            conn.execute(text("ALTER TABLE users ADD COLUMN phone VARCHAR"))
        if "agency_name" not in existing:
            conn.execute(text("ALTER TABLE users ADD COLUMN agency_name VARCHAR"))
        # Ensure chat_conversations table exists
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS chat_conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL UNIQUE REFERENCES users(id),
                messages TEXT NOT NULL DEFAULT '[]',
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """))


def _ensure_saved_listings_columns() -> None:
    """Ensure saved_listings table has listing_data column"""
    if engine.url.get_backend_name() != "sqlite":
        return
    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(saved_listings)")).mappings().all()
        if not rows:
            return
        existing = {row["name"] for row in rows}
        if "listing_data" not in existing:
            conn.execute(text("ALTER TABLE saved_listings ADD COLUMN listing_data TEXT"))


_ensure_user_columns()
_ensure_saved_listings_columns()


def _ensure_agent_listing_tables() -> None:
    """Create agent_listings and listing_likes tables if they don't exist."""
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS agent_listings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER NOT NULL REFERENCES users(id),
                listing_url VARCHAR NOT NULL UNIQUE,
                listing_data TEXT NOT NULL,
                intent VARCHAR NOT NULL DEFAULT 'sell',
                published_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                is_active BOOLEAN NOT NULL DEFAULT 1
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS listing_likes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                listing_id INTEGER NOT NULL REFERENCES agent_listings(id),
                user_id INTEGER NOT NULL REFERENCES users(id),
                liked_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(listing_id, user_id)
            )
        """))


_ensure_agent_listing_tables()

# CORS configuration
_cors_env = os.getenv("CORS_ORIGINS", "")
_default_origins = [
    "http://localhost:3000", "http://localhost:5173",
    "http://127.0.0.1:3000", "http://127.0.0.1:5173",
]

if _cors_env == "*":
    # Allow all origins (useful for open dev/staging)
    _allow_origins = ["*"]
    _allow_credentials = False
elif _cors_env:
    # Merge env-supplied origins with localhost defaults
    _allow_origins = list({o.strip() for o in _cors_env.split(",") if o.strip()} | set(_default_origins))
    _allow_credentials = True
else:
    _allow_origins = _default_origins
    _allow_credentials = True

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins,
    allow_credentials=_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Single global recommender (prototype = single user)
from recommender_db import RecommenderDB
try:
    _recommender = RecommenderDB()
    print(f"✓ Recommender initialized with {len(_recommender.df)} listings from database")
except Exception as e:
    print(f"⚠ Warning: Could not initialize database recommender: {e}")
    print("⚠ Falling back to empty recommender")
    # Create a fallback recommender with empty data
    import pandas as pd
    from recommender import Recommender
    _recommender = Recommender()
    _recommender.df = pd.DataFrame()  # Empty dataframe
    print("⚠ Recommender initialized with 0 listings (fallback mode)")


# ─────────────────────────────────────────────────────────────────────────────
# Startup Event - Seed Database
# ─────────────────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def seed_on_startup():
    """Seed listings from bundled DB if table is empty."""
    import sqlite3
    import pandas as pd
    
    print("\n=== STARTUP: Checking database seed ===", flush=True)
    
    try:
        # Check if listings table has data
        existing = pd.read_sql("SELECT COUNT(*) as count FROM listings", engine)
        count = existing['count'].iloc[0]
        if count > 0:
            print(f"✓ Listings already seeded: {count} rows.", flush=True)
            return
    except Exception as e:
        print(f"⚠ Table check failed (probably doesn't exist yet): {e}", flush=True)
    
    # Find source database
    source_candidates = [
        "/tmp/listings_seed.db",
        "/app/listings.db", 
        "listings.db",
        "../data/listings.db"
    ]
    
    print(f"🔍 Looking for source database...", flush=True)
    for candidate in source_candidates:
        exists = os.path.exists(candidate)
        print(f"   {candidate}: {'✓ FOUND' if exists else '✗ not found'}", flush=True)
    
    source_path = next((p for p in source_candidates if os.path.exists(p)), None)
    
    if not source_path:
        print(f"❌ Seed file not found. Checked: {source_candidates}", flush=True)
        return
    
    print(f"→ Seeding listings from {source_path}...", flush=True)
    
    try:
        conn = sqlite3.connect(source_path)
        
        # Check what tables exist
        tables_df = pd.read_sql("SELECT name FROM sqlite_master WHERE type='table'", conn)
        print(f"   Tables in source: {tables_df['name'].tolist()}", flush=True)
        
        df = pd.read_sql("SELECT * FROM listings", conn)
        conn.close()
        
        if df.empty:
            print("❌ Source DB has no listings.", flush=True)
            return
        
        print(f"   Found {len(df)} listings in source", flush=True)
        print(f"   Columns: {list(df.columns)[:10]}...", flush=True)
        
        # Write to target database
        df.to_sql("listings", engine, if_exists="replace", index=False)
        print(f"✅ Seeded {len(df)} listings successfully.", flush=True)
        
        # Reload the recommender with fresh data
        global _recommender
        print("🔄 Reloading recommender with seeded data...", flush=True)
        _recommender = RecommenderDB()
        print(f"✅ Recommender reloaded: {len(_recommender.df)} listings", flush=True)
        
    except Exception as e:
        print(f"❌ Seeding failed: {e}", flush=True)
        import traceback
        traceback.print_exc()


# ─────────────────────────────────────────────────────────────────────────────
# Authentication
# ─────────────────────────────────────────────────────────────────────────────

SECRET_KEY = os.getenv("AUTH_SECRET_KEY", "dev-secret-change-me")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 30  # 30 days

# ── SMTP config (set in .env for real email, otherwise falls back to console) ─
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER) or "noreply@dari.ma"

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


class RegisterRequest(BaseModel):
    email: EmailStr
    password: constr(min_length=8)
    full_name: Optional[str] = None
    role: Optional[str] = "client"  # 'client' | 'agent'
    phone: Optional[str] = None
    agency_name: Optional[str] = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    email: EmailStr
    token: str
    new_password: constr(min_length=8)


class Token(BaseModel):
    access_token: str
    token_type: str
    role: Optional[str] = None


class UserOut(BaseModel):
    id: int
    email: EmailStr
    full_name: Optional[str] = None
    phone: Optional[str] = None
    agency_name: Optional[str] = None
    role: str = "client"
    created_at: datetime

    class Config:
        orm_mode = True


def _hash_password(password: str) -> str:
    # PBKDF2 is built into Python and doesn't have dependency issues
    return pwd_context.hash(password)


def _verify_password(plain_password: str, hashed_password: str) -> bool:
    # PBKDF2 is built into Python and doesn't have dependency issues
    return pwd_context.verify(plain_password, hashed_password)


def _create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def _get_user_by_email(db: Session, email: str) -> Optional[User]:
    return db.query(User).filter(User.email == email).first()


def _set_password_reset_token(db: Session, user: User) -> str:
    code = f"{random.randint(0, 999999):06d}"
    user.reset_token = code
    user.reset_token_expires = datetime.utcnow() + timedelta(minutes=15)
    db.add(user)
    db.commit()
    db.refresh(user)
    return code


def _send_reset_email(to_email: str, code: str) -> None:
    """Send OTP code by email. Falls back to console log if SMTP is not configured."""
    if not SMTP_HOST or not SMTP_USER or not SMTP_PASSWORD:
        print(f"[DARI] Password reset OTP for {to_email}: {code}  (configure SMTP_* env vars to send real emails)")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Dari — Code de réinitialisation"
    msg["From"] = SMTP_FROM
    msg["To"] = to_email

    plain = (
        f"Votre code de réinitialisation Dari : {code}\n\n"
        "Ce code est valable 15 minutes.\n"
        "Si vous n'avez pas demandé cette réinitialisation, ignorez cet email."
    )
    html = f"""
    <div style="font-family:sans-serif;max-width:400px;margin:auto;padding:32px">
      <h2 style="font-size:24px;margin-bottom:8px">Darī</h2>
      <p style="color:#666">Votre code de réinitialisation :</p>
      <div style="font-size:40px;font-weight:bold;letter-spacing:8px;color:#B57329;margin:24px 0">{code}</div>
      <p style="color:#888;font-size:13px">Valable 15 minutes. Si vous n'êtes pas à l'origine de cette demande, ignorez cet email.</p>
    </div>
    """
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.starttls()
        smtp.login(SMTP_USER, SMTP_PASSWORD)
        smtp.sendmail(SMTP_FROM, to_email, msg.as_string())


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email = payload.get("sub")
        if not email:
            raise credentials_exception
    except JWTError as exc:
        raise credentials_exception from exc
    user = _get_user_by_email(db, email)
    if not user or not user.is_active:
        raise credentials_exception
    return user


@app.post("/api/auth/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register_user(payload: RegisterRequest, db: Session = Depends(get_db)):
    existing = _get_user_by_email(db, payload.email)
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")
    role = payload.role if payload.role in ("client", "agent") else "client"
    if role == "agent":
        if not (payload.phone or "").strip() or not (payload.agency_name or "").strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Agents must provide phone and agency_name at registration",
            )
    user = User(
        email=payload.email,
        full_name=payload.full_name,
        hashed_password=_hash_password(payload.password),
        role=role,
        phone=payload.phone,
        agency_name=payload.agency_name,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@app.post("/api/auth/login", response_model=Token)
def login_user(payload: LoginRequest, db: Session = Depends(get_db)):
    user = _get_user_by_email(db, payload.email)
    if not user or not _verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token = _create_access_token({"sub": user.email, "role": user.role})
    return {"access_token": access_token, "token_type": "bearer", "role": user.role}


@app.post("/api/auth/reset/request")
def request_password_reset(payload: PasswordResetRequest, db: Session = Depends(get_db)):
    user = _get_user_by_email(db, payload.email)
    if user:
        code = _set_password_reset_token(db, user)
        try:
            _send_reset_email(user.email, code)
        except Exception as exc:
            print(f"[DARI] Failed to send reset email to {user.email}: {exc}")
    # Always return ok to prevent email enumeration
    return {"ok": True}


@app.post("/api/auth/reset/confirm")
def confirm_password_reset(payload: PasswordResetConfirm, db: Session = Depends(get_db)):
    user = _get_user_by_email(db, payload.email)
    if not user or not user.reset_token or not user.reset_token_expires:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired reset token")
    if user.reset_token != payload.token:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired reset token")
    if user.reset_token_expires < datetime.utcnow():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired reset token")
    user.hashed_password = _hash_password(payload.new_password)
    user.reset_token = None
    user.reset_token_expires = None
    db.add(user)
    db.commit()
    return {"ok": True}


@app.get("/api/auth/me", response_model=UserOut)
def read_current_user(current_user: User = Depends(get_current_user)):
    return current_user


class UpdateProfileRequest(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None
    agency_name: Optional[str] = None
    # role is immutable after registration — removed


@app.patch("/api/auth/me", response_model=UserOut)
def update_current_user(
    payload: UpdateProfileRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if payload.full_name is not None:
        current_user.full_name = payload.full_name
    if payload.phone is not None:
        current_user.phone = payload.phone
    if payload.agency_name is not None:
        current_user.agency_name = payload.agency_name
    db.commit()
    db.refresh(current_user)
    return current_user


@app.post("/api/auth/logout")
def logout_user(current_user: User = Depends(get_current_user)):
    """
    Logout endpoint - in a stateless JWT system, logout is handled client-side
    by removing the token. This endpoint validates the token is still valid.
    """
    return {"ok": True, "message": "Logged out successfully"}


# ─────────────────────────────────────────────────────────────────────────────
# User Data Management (Saved Listings, Bookings, History)
# ─────────────────────────────────────────────────────────────────────────────

class SavedListingRequest(BaseModel):
    listing_url: str
    listing_data: Optional[dict] = None  # Full listing object
    notes: Optional[str] = None


class BookingRequest(BaseModel):
    listing_url: str
    booking_date: datetime
    notes: Optional[str] = None
    agent_id: Optional[int] = None  # ID of the agent who owns the listing


class BookingUpdate(BaseModel):
    status: str  # pending, confirmed, cancelled, completed
    notes: Optional[str] = None


class ViewHistoryRequest(BaseModel):
    listing_url: str


@app.post("/api/user/saved-listings")
def save_listing(payload: SavedListingRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Save a listing to user's favorites"""
    # Check if already saved
    existing = db.query(SavedListing).filter(
        SavedListing.user_id == current_user.id,
        SavedListing.listing_url == payload.listing_url
    ).first()
    
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Listing already saved")
    
    import json
    listing_data_json = json.dumps(payload.listing_data) if payload.listing_data else None
    
    saved = SavedListing(
        user_id=current_user.id,
        listing_url=payload.listing_url,
        listing_data=listing_data_json,
        notes=payload.notes
    )
    db.add(saved)
    db.commit()
    db.refresh(saved)
    return {"ok": True, "id": saved.id, "saved_at": saved.saved_at}


@app.get("/api/user/saved-listings")
def get_saved_listings(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Get all saved listings for current user"""
    import json
    saved = db.query(SavedListing).filter(SavedListing.user_id == current_user.id).order_by(SavedListing.saved_at.desc()).all()
    return {
        "saved_listings": [
            {
                "id": s.id,
                "listing_url": s.listing_url,
                "listing_data": json.loads(s.listing_data) if s.listing_data else None,
                "notes": s.notes,
                "saved_at": s.saved_at
            }
            for s in saved
        ],
        "total": len(saved)
    }


@app.delete("/api/user/saved-listings/{listing_id}")
def remove_saved_listing(listing_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Remove a listing from saved items"""
    saved = db.query(SavedListing).filter(
        SavedListing.id == listing_id,
        SavedListing.user_id == current_user.id
    ).first()
    
    if not saved:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Saved listing not found")
    
    db.delete(saved)
    db.commit()
    return {"ok": True, "message": "Listing removed from saved items"}


# ─────────────────────────────────────────────────────────────────────────────
# Agent Listings — Publish / Manage / Stats
# ─────────────────────────────────────────────────────────────────────────────

class PublishListingRequest(BaseModel):
    listing_data: dict
    intent: Optional[str] = "sell"


@app.post("/api/agent/listings")
def publish_agent_listing(payload: PublishListingRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Publish a new agent listing."""
    listing = AgentListing(
        agent_id=current_user.id,
        listing_url="",  # filled after insert to get the id
        listing_data=json.dumps(payload.listing_data),
        intent=payload.intent or "sell",
    )
    db.add(listing)
    db.flush()  # get the auto-generated id
    listing.listing_url = f"dari://agent-listing/{listing.id}"
    db.commit()
    db.refresh(listing)
    return {"ok": True, "id": listing.id, "listing_url": listing.listing_url}


@app.get("/api/agent/listings")
def get_agent_listings(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Get all listings published by the current agent, with like/save stats."""
    listings = (
        db.query(AgentListing)
        .filter(AgentListing.agent_id == current_user.id, AgentListing.is_active == True)
        .order_by(AgentListing.published_at.desc())
        .all()
    )

    result = []
    for lst in listings:
        # Likes: users who liked this listing
        likes = (
            db.query(ListingLike, User)
            .join(User, User.id == ListingLike.user_id)
            .filter(ListingLike.listing_id == lst.id)
            .all()
        )
        liked_by = [
            {"email": u.email, "full_name": u.full_name or u.email.split("@")[0]}
            for _, u in likes
        ]

        # Saves: count of SavedListing rows referencing this listing_url
        save_count = db.query(SavedListing).filter(SavedListing.listing_url == lst.listing_url).count()

        result.append({
            "id": lst.id,
            "listing_url": lst.listing_url,
            "listing_data": json.loads(lst.listing_data),
            "intent": lst.intent,
            "published_at": lst.published_at,
            "like_count": len(liked_by),
            "liked_by": liked_by,
            "save_count": save_count,
        })

    return {"listings": result, "total": len(result)}


@app.delete("/api/agent/listings/{listing_id}")
def delete_agent_listing(listing_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Soft-delete (deactivate) an agent listing."""
    lst = db.query(AgentListing).filter(
        AgentListing.id == listing_id,
        AgentListing.agent_id == current_user.id,
    ).first()
    if not lst:
        raise HTTPException(status_code=404, detail="Listing not found")
    lst.is_active = False
    db.commit()
    return {"ok": True}


@app.post("/api/agent/listings/{listing_id}/like")
def like_agent_listing(listing_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Record that the current user liked an agent listing (idempotent)."""
    lst = db.query(AgentListing).filter(AgentListing.id == listing_id, AgentListing.is_active == True).first()
    if not lst:
        raise HTTPException(status_code=404, detail="Listing not found")
    existing = db.query(ListingLike).filter_by(listing_id=listing_id, user_id=current_user.id).first()
    if not existing:
        db.add(ListingLike(listing_id=listing_id, user_id=current_user.id))
        db.commit()
    return {"ok": True}


@app.post("/api/user/bookings")
def create_booking(payload: BookingRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Create a property viewing/booking"""
    booking = Booking(
        user_id=current_user.id,
        agent_id=payload.agent_id,
        listing_url=payload.listing_url,
        booking_date=payload.booking_date,
        notes=payload.notes,
        status="pending"
    )
    db.add(booking)
    db.commit()
    db.refresh(booking)
    return {
        "ok": True,
        "booking": {
            "id": booking.id,
            "listing_url": booking.listing_url,
            "booking_date": booking.booking_date,
            "status": booking.status,
            "notes": booking.notes,
            "created_at": booking.created_at
        }
    }


@app.get("/api/user/bookings")
def get_bookings(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Get all bookings for current user"""
    bookings = db.query(Booking).filter(Booking.user_id == current_user.id).order_by(Booking.booking_date.desc()).all()
    return {
        "bookings": [
            {
                "id": b.id,
                "listing_url": b.listing_url,
                "booking_date": b.booking_date,
                "status": b.status,
                "notes": b.notes,
                "created_at": b.created_at
            }
            for b in bookings
        ],
        "total": len(bookings)
    }


@app.get("/api/agent/bookings")
def get_agent_bookings(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Get all bookings made on the current agent's listings (inbound client bookings)"""
    if current_user.role != "agent":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only agents can access this endpoint")
    bookings = (
        db.query(Booking)
        .filter(Booking.agent_id == current_user.id)
        .order_by(Booking.booking_date.desc())
        .all()
    )
    result = []
    for b in bookings:
        client = db.query(User).filter(User.id == b.user_id).first()
        result.append({
            "id": b.id,
            "listing_url": b.listing_url,
            "booking_date": b.booking_date,
            "status": b.status,
            "notes": b.notes,
            "created_at": b.created_at,
            "client": {
                "id": client.id if client else None,
                "full_name": client.full_name if client else None,
                "email": client.email if client else None,
                "phone": client.phone if client else None,
            } if client else None,
        })
    return {"bookings": result, "total": len(result)}


@app.patch("/api/agent/bookings/{booking_id}")
def agent_update_booking(booking_id: int, payload: BookingUpdate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Agent confirms or cancels a client's booking request"""
    if current_user.role != "agent":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only agents can access this endpoint")
    booking = db.query(Booking).filter(
        Booking.id == booking_id,
        Booking.agent_id == current_user.id,
    ).first()
    if not booking:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")
    if payload.status:
        booking.status = payload.status
    if payload.notes is not None:
        booking.notes = payload.notes
    db.add(booking)
    db.commit()
    db.refresh(booking)
    return {"ok": True, "booking": {"id": booking.id, "status": booking.status}}


@app.patch("/api/user/bookings/{booking_id}")
def update_booking(booking_id: int, payload: BookingUpdate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Update booking status or notes"""
    booking = db.query(Booking).filter(
        Booking.id == booking_id,
        Booking.user_id == current_user.id
    ).first()
    
    if not booking:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")
    
    if payload.status:
        booking.status = payload.status
    if payload.notes is not None:
        booking.notes = payload.notes
    
    db.add(booking)
    db.commit()
    db.refresh(booking)
    return {"ok": True, "booking": {
        "id": booking.id,
        "status": booking.status,
        "notes": booking.notes
    }}


@app.delete("/api/user/bookings/{booking_id}")
def cancel_booking(booking_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Cancel/delete a booking"""
    booking = db.query(Booking).filter(
        Booking.id == booking_id,
        Booking.user_id == current_user.id
    ).first()
    
    if not booking:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")
    
    db.delete(booking)
    db.commit()
    return {"ok": True, "message": "Booking cancelled"}


@app.post("/api/user/view-history")
def add_view_history(payload: ViewHistoryRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Track that user viewed a listing"""
    view = ViewHistory(
        user_id=current_user.id,
        listing_url=payload.listing_url
    )
    db.add(view)
    db.commit()
    return {"ok": True}


@app.get("/api/user/view-history")
def get_view_history(limit: int = Query(50, le=200), current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Get user's viewing history"""
    history = db.query(ViewHistory).filter(
        ViewHistory.user_id == current_user.id
    ).order_by(ViewHistory.viewed_at.desc()).limit(limit).all()
    
    return {
        "history": [
            {
                "id": h.id,
                "listing_url": h.listing_url,
                "viewed_at": h.viewed_at
            }
            for h in history
        ],
        "total": len(history)
    }


@app.delete("/api/user/view-history")
def clear_view_history(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Clear all viewing history for user"""
    db.query(ViewHistory).filter(ViewHistory.user_id == current_user.id).delete()
    db.commit()
    return {"ok": True, "message": "View history cleared"}


# ─────────────────────────────────────────────────────────────────────────────
# Filter → UserPreferences mapping
# ─────────────────────────────────────────────────────────────────────────────

# Map interview answer strings → canonical CSV values
_PROPERTY_TYPE_MAP: dict[str, str] = {
    # French interview choices
    "appartement":          "Appartement",
    "maison/villa":         "Villa",
    "maison":               "Villa",
    "villa":                "Villa",
    "riad":                 "Riad",
    "bureau":               "Bureau",
    "local commercial":     "Local commercial",
    "local":                "Local commercial",
    "terrain":              "Terrain",
    "studio":               "Studio",
    # English
    "apartment":            "Appartement",
    "flat":                 "Appartement",
    "penthouse":            "Appartement",
    "house/villa":          "Villa",
    "house":                "Villa",
    "office":               "Bureau",
    "commercial":           "Local commercial",
    "commercial space":     "Local commercial",
    "retail":               "Local commercial",
    "land":                 "Terrain",
    "plot":                 "Terrain",
    # Voice mode — Gemini may return these spellings/capitalizations
    "appartment":           "Appartement",
    "Appartement":          "Appartement",
    "Villa":                "Villa",
    "Studio":               "Studio",
    "Riad":                 "Riad",
    "Bureau":               "Bureau",
    "Terrain":              "Terrain",
}

_CITY_MAP: dict[str, str] = {
    "casablanca":  "Casablanca",
    "casa":        "Casablanca",
    "كازا":        "Casablanca",
    "marrakech":   "Marrakech",
    "marrakesh":   "Marrakech",
    "مراكش":       "Marrakech",
    "rabat":       "Rabat",
    "الرباط":      "Rabat",
    "tangier":     "Tanger",
    "tanger":      "Tanger",
    "طنجة":        "Tanger",
    "agadir":      "Agadir",
    "أكادير":      "Agadir",
    "fes":         "Fes",
    "fès":         "Fes",
    "fez":         "Fes",
    "فاس":         "Fes",
}

# Map interview amenity labels → UserPreferences attribute names
# NOTE: No duplicate keys — Python silently drops the first occurrence of a duplicate.
_AMENITY_MAP: dict[str, str] = {
    # French
    "parking":              "has_garage",
    "garage":               "has_garage",
    "ascenseur":            "has_ascenseur",
    "terrasse":             "has_terrasse",
    "balcon":               "has_terrasse",
    "terrasse/balcon":      "has_terrasse",
    "meublé":               "has_meuble",
    "meuble":               "has_meuble",
    "climatisation":        "has_climatisation",
    "clim":                 "has_climatisation",
    "sécurité":             "has_securite",
    "securite":             "has_securite",
    "sécurité 24/7":        "has_securite",
    "cuisine équipée":      "has_cuisine_equipee",
    "cuisine equipee":      "has_cuisine_equipee",
    "piscine":              "has_piscine",
    "jardin":               "has_jardin",
    "chauffage central":    "has_chauffage_central",
    "chauffage":            "has_chauffage_central",
    "concierge":            "has_concierge",
    "vue mer":              "has_vue_mer",
    # English (separate keys — no duplicates with French)
    "elevator":             "has_ascenseur",
    "lift":                 "has_ascenseur",
    "terrace":              "has_terrasse",
    "balcony":              "has_terrasse",
    "terrace/balcony":      "has_terrasse",
    "furnished":            "has_meuble",
    "ac":                   "has_climatisation",
    "air conditioning":     "has_climatisation",
    "security":             "has_securite",
    "24/7 security":        "has_securite",
    "equipped kitchen":     "has_cuisine_equipee",
    "pool":                 "has_piscine",
    "swimming pool":        "has_piscine",
    "garden":               "has_jardin",
    "central heating":      "has_chauffage_central",
    "heating":              "has_chauffage_central",
    "sea view":             "has_vue_mer",
    "ocean view":           "has_vue_mer",
}


def _parse_surface(value) -> Optional[float]:
    """Parse surface strings like '80m²', '150m²+', or a raw number."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = re.search(r"(\d+(?:\.\d+)?)", str(value))
    return float(m.group(1)) if m else None


def _parse_bedrooms(value) -> Optional[int]:
    """Parse bedroom strings like '3', '5+', '5'."""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    m = re.search(r"(\d+)", str(value))
    return int(m.group(1)) if m else None


def _map_filters(filters: dict, intent: str) -> UserPreferences:
    """
    Convert raw interview answers (from questions.ts IDs or Gemini voice keys)
    into a UserPreferences object.
    """
    prefs = UserPreferences()

    # ── Transaction type ──────────────────────────────────────────────────────
    prefs.transaction_type = "Location" if intent == "rent" else "Vente"

    # ── City parsing — handles plain city, plain neighborhood, or combined ────
    # Voice AI may send: "Casablanca", "Maarif", "Maarif, Casablanca", etc.
    raw_location = filters.get("ville") or filters.get("city") or filters.get("location") or ""
    if raw_location:
        raw_location = str(raw_location).strip()
        low_loc = raw_location.lower()

        # Direct match: known city
        if low_loc in _CITY_MAP:
            prefs.city = _CITY_MAP[low_loc]
        elif low_loc in [v.lower() for v in _CITY_MAP.values()]:
            prefs.city = raw_location.capitalize()
        else:
            # Try splitting by comma — AI may combine "Neighborhood, City"
            city_found = False
            neigh_parts = []
            for part in [p.strip() for p in raw_location.replace(" - ", ",").split(",") if p.strip()]:
                pl = part.lower()
                if pl in _CITY_MAP:
                    prefs.city = _CITY_MAP[pl]
                    city_found = True
                elif pl in [v.lower() for v in _CITY_MAP.values()]:
                    prefs.city = part.capitalize()
                    city_found = True
                else:
                    neigh_parts.append(part)
            if not city_found:
                # Treat entire string as a neighborhood in Casablanca
                prefs.city = "Casablanca"
                neigh_parts = [raw_location]
            if neigh_parts and not prefs.neighborhoods:
                prefs.neighborhoods = [neigh_parts[0]]

    # ── Budget ('budget' text/voice / 'price' voice fallback) ────────────────
    # Explicit None check so a value of 0 is never silently skipped.
    budget_val = (
        filters.get("budget") if filters.get("budget") is not None
        else filters.get("price")
    )
    if budget_val is not None:
        try:
            prefs.price_max = int(float(budget_val))
        except (ValueError, TypeError):
            pass

    # ── Property type ('type_bien' text / 'propertyType' voice) ─────────────
    raw_type = filters.get("type_bien") or filters.get("propertyType") or ""
    if raw_type:
        if isinstance(raw_type, list):
            prefs.property_type = [
                _PROPERTY_TYPE_MAP.get(t.lower(), _PROPERTY_TYPE_MAP.get(t, t.strip()))
                for t in raw_type if isinstance(t, str)
            ]
        else:
            # Try lowercase lookup, then exact-case lookup, then pass through as-is
            mapped = _PROPERTY_TYPE_MAP.get(raw_type.lower()) or _PROPERTY_TYPE_MAP.get(raw_type)
            prefs.property_type = mapped if mapped else raw_type.strip()

    # ── Surface ('surface' in both) ───────────────────────────────────────────
    prefs.surface_min = _parse_surface(filters.get("surface"))

    # ── Bedrooms ('chambres' text / 'rooms' voice) ───────────────────────────
    prefs.bedrooms = _parse_bedrooms(filters.get("chambres") or filters.get("rooms"))

    # ── Neighborhood — multiple sources, in priority order ───────────────────
    # 1. Explicit 'neighborhood' voice field (new — highest priority)
    voice_neigh = str(filters.get("neighborhood") or "").strip()
    # 2. Text-mode 'quartier'
    quartier = str(filters.get("quartier") or "").strip()
    # 3. Direct 'neighborhoods' list
    neighborhoods_list = filters.get("neighborhoods")

    if voice_neigh and not prefs.neighborhoods:
        prefs.neighborhoods = [voice_neigh]
    elif quartier and not prefs.neighborhoods:
        prefs.neighborhoods = [quartier]
    elif neighborhoods_list and not prefs.neighborhoods:
        prefs.neighborhoods = neighborhoods_list if isinstance(neighborhoods_list, list) else [neighborhoods_list]
    elif filters.get("address") and not prefs.neighborhoods:
        addr = str(filters.get("address", "")).strip()
        # Only use short, digit-free strings as neighborhood hints
        if addr and len(addr.split()) <= 4 and not any(c.isdigit() for c in addr):
            prefs.neighborhoods = [addr]

    # ── Amenities ('equipements' text / 'equipment' voice / 'amenities' voice array) ──
    raw_equip = filters.get("equipements") or filters.get("equipment") or filters.get("amenities")
    if raw_equip:
        items: list[str] = []
        if isinstance(raw_equip, list):
            items = raw_equip
        elif isinstance(raw_equip, str):
            items = [e.strip() for e in raw_equip.split(",")]
        for item in items:
            attr = _AMENITY_MAP.get(item.lower())
            if attr:
                setattr(prefs, attr, True)

    # ── Visual & Architectural preferences ────────────────────────────────────
    # Canonicalize visual preference values to match what's in the database
    if "visual_style" in filters:
        raw_val = str(filters["visual_style"]).lower().strip()
        if any(k in raw_val for k in ["modern", "moderne", "contemporain"]):
            prefs.visual_style = "Modern"
        elif any(k in raw_val for k in ["traditional", "traditionnel", "classic", "classique"]):
            prefs.visual_style = "Traditional"
        elif any(k in raw_val for k in ["minimal", "simple", "epure"]):
            prefs.visual_style = "Minimalist"
        elif "standard" not in raw_val and "doesn't matter" not in raw_val:
            prefs.visual_style = filters["visual_style"]  # Pass through if not standard
    
    if "natural_light" in filters:
        raw_val = str(filters["natural_light"]).lower().strip()
        if any(k in raw_val for k in ["high", "elevee", "bright", "lumineux"]):
            prefs.natural_light = "High"
        elif any(k in raw_val for k in ["medium", "moyen", "moderate"]):
            prefs.natural_light = "Medium"
        elif any(k in raw_val for k in ["low", "faible", "dark", "sombre"]):
            prefs.natural_light = "Low"
        elif "standard" not in raw_val and "doesn't matter" not in raw_val:
            prefs.natural_light = filters["natural_light"]
    
    if "architectural_vibe" in filters:
        raw_val = str(filters["architectural_vibe"]).lower().strip()
        if any(k in raw_val for k in ["beldi", "marocain", "moroccan"]):
            prefs.architectural_vibe = "Beldi/Moroccan"
        elif any(k in raw_val for k in ["europeen", "european", "classic", "classique"]):
            prefs.architectural_vibe = "European/Classic"
        elif any(k in raw_val for k in ["industrial", "industriel", "loft"]):
            prefs.architectural_vibe = "Industrial/Loft"
        elif "standard" not in raw_val and "doesn't matter" not in raw_val:
            prefs.architectural_vibe = filters["architectural_vibe"]
    
    if "furnishing_status" in filters:
        raw_val = str(filters["furnishing_status"]).lower().strip()
        if any(k in raw_val for k in ["furnished", "meuble", "meublee"]):
            prefs.furnishing_status = "Fully Furnished"
        elif any(k in raw_val for k in ["empty", "vide", "unfurnished"]):
            prefs.furnishing_status = "Empty"
        elif "standard" not in raw_val and "doesn't matter" not in raw_val:
            prefs.furnishing_status = filters["furnishing_status"]
    
    if "floor_material" in filters:
        raw_val = str(filters["floor_material"]).lower().strip()
        if any(k in raw_val for k in ["parquet", "wood", "bois"]):
            prefs.floor_material = "Parquet/Wood"
        elif any(k in raw_val for k in ["tile", "marble", "carrelage", "marbre"]):
            prefs.floor_material = "Tile/Marble"
        elif any(k in raw_val for k in ["carpet", "rug", "moquette", "tapis"]):
            prefs.floor_material = "Carpet/Rug"
        elif "standard" not in raw_val and "doesn't matter" not in raw_val:
            prefs.floor_material = filters["floor_material"]
    
    if "dominant_view" in filters:
        raw_val = str(filters["dominant_view"]).lower().strip()
        if any(k in raw_val for k in ["nature", "greenery", "verdure", "jardin", "park"]):
            prefs.dominant_view = "Nature/Greenery"
        elif any(k in raw_val for k in ["sea", "water", "mer", "ocean"]):
            prefs.dominant_view = "Water/Sea"
        elif any(k in raw_val for k in ["city", "urban", "ville", "urbain"]):
            prefs.dominant_view = "Urban/City"
        elif any(k in raw_val for k in ["blocked", "interior", "interieur", "no view"]):
            prefs.dominant_view = "Blocked/Interior"
        elif "standard" not in raw_val and "doesn't matter" not in raw_val:
            prefs.dominant_view = filters["dominant_view"]
    
    if "visual_condition" in filters:
        prefs.visual_condition = filters["visual_condition"]
    if "color_palette" in filters:
        prefs.color_palette = filters["color_palette"]

    return prefs


# ─────────────────────────────────────────────────────────────────────────────
# Listing serializer
# ─────────────────────────────────────────────────────────────────────────────

def _format_price(price: int) -> str:
    """Format 1200000 → '1 200 000'."""
    return f"{price:,}".replace(",", " ")


def _make_title(row: dict) -> str:
    """Generate a human-readable listing title."""
    parts = []
    prop = str(row.get("property_type") or "").strip()
    if prop and prop != "Inconnu":
        parts.append(prop)
    beds = row.get("bedrooms")
    if beds and str(beds) not in ("", "None", "<NA>"):
        try:
            parts.append(f"{int(float(str(beds)))} ch")
        except (ValueError, TypeError):
            pass
    state = str(row.get("state") or "").strip()
    if state and state not in ("Inconnu", ""):
        parts.append(state)
    return " – ".join(parts) if parts else "Bien immobilier"


# Maps equipements label (lowercased) → AMENITY_COLS column name
_EQUIP_TO_AMENITY: dict[str, str] = {
    "ascenseur": "has_ascenseur", "elevator": "has_ascenseur",
    "terrasse": "has_terrasse", "terrasse/balcon": "has_terrasse", "terrace/balcony": "has_terrasse", "balcon": "has_terrasse",
    "piscine": "has_piscine", "pool": "has_piscine",
    "parking": "has_garage", "garage": "has_garage",
    "meublé": "has_meuble", "furnished": "has_meuble", "مفروش": "has_meuble",
    "clim": "has_climatisation", "climatisation": "has_climatisation", "ac": "has_climatisation",
    "sécurité": "has_securite", "sécurité 24/7": "has_securite", "security": "has_securite", "24/7 security": "has_securite",
    "jardin": "has_jardin", "garden": "has_jardin",
    "vue mer": "has_vue_mer",
    "cuisine équipée": "has_cuisine_equipee", "equipped kitchen": "has_cuisine_equipee",
    "concierge": "has_concierge",
    "salon européen": "has_salon_europeen",
    "salon marocain": "has_salon_marocain",
    "antenne parabolique": "has_antenne_parabolique",
    "double vitrage": "has_double_vitrage",
    "façade extérieure": "has_facade_exterieure",
    "cheminée": "has_cheminee",
    "machine à laver": "has_machine_laver",
    "porte blindée": "has_porte_blindee",
    "chauffage central": "has_chauffage_central",
    "chambre rangement": "has_chambre_rangement",
    "entre-seul": "has_entre_seul",
    "four": "has_four",
    "micro-ondes": "has_micro_ondes",
    "réfrigérateur": "has_refrigerateur",
}


def _agent_listing_to_df_row(agent_lst: "AgentListing") -> dict:
    """Convert an AgentListing ORM object to a dict matching the recommender DataFrame schema."""
    data = json.loads(agent_lst.listing_data) if agent_lst.listing_data else {}

    def _safe_num(val, cast=float):
        try:
            return cast(float(str(val)))
        except (ValueError, TypeError):
            return None

    city         = str(data.get("ville") or data.get("city") or "").strip()
    neighborhood = str(data.get("quartier") or data.get("neighborhood") or "").strip()
    prop_type    = str(data.get("type_bien") or data.get("property_type") or "").strip()
    intent       = agent_lst.intent or "sell"
    transaction  = "Vente" if intent == "sell" else "Location"

    # Images joined as pipe-separated string (same as scraped listings)
    media = data.get("media") or []
    images_list = []
    if isinstance(media, list):
        for item in media:
            u = item.get("url", "") if isinstance(item, dict) else str(item)
            if u:
                images_list.append(u)
    images_str = " | ".join(images_list)

    # Build amenity flags from equipements labels
    amenity_row = {col: 0 for col in AMENITY_COLS}
    for label in (data.get("equipements") or data.get("tags") or []):
        col = _EQUIP_TO_AMENITY.get(str(label).lower().strip())
        if col:
            amenity_row[col] = 1

    row = {
        "url":              agent_lst.listing_url,
        "price":            _safe_num(data.get("prix") or data.get("price"), float) or 0.0,
        "surface":          _safe_num(data.get("surface"), float),
        "rooms":            _safe_num(data.get("pieces") or data.get("rooms"), int),
        "bedrooms":         _safe_num(data.get("chambres") or data.get("bedrooms") or data.get("beds"), int),
        "bathrooms":        _safe_num(data.get("salles_bain") or data.get("bathrooms"), int),
        "city":             city,
        "neighborhood":     neighborhood,
        "property_type":    prop_type,
        "transaction_type": transaction,
        "state":            str(data.get("etat") or data.get("state") or ""),
        "standing":         "",
        "description":      str(data.get("description") or ""),
        "phone_number":     str(data.get("phone_number") or ""),
        "map_link":         "",
        "address":          str(data.get("adresse") or data.get("address") or ""),
        "images":           images_str,
        # Visual attributes — not available from agent interview
        "visual_style": "", "natural_light": "", "visual_condition": "",
        "furnishing_status": "", "floor_material": "", "dominant_view": "",
        "architectural_vibe": "", "color_palette": "",
        **amenity_row,
    }
    return row


def _serialize_agent_listing(agent_lst: "AgentListing") -> dict:
    """Convert an AgentListing ORM object to the same shape as _serialize output."""
    data = json.loads(agent_lst.listing_data) if agent_lst.listing_data else {}

    def _safe_int(val):
        try:
            v = int(float(str(val)))
            return v if v > 0 else None
        except (ValueError, TypeError):
            return None

    # Core fields
    try:
        price = int(float(str(data.get("prix") or data.get("price") or 0)))
    except (ValueError, TypeError):
        price = 0

    city          = str(data.get("ville") or data.get("city") or "").strip()
    neighborhood  = str(data.get("quartier") or data.get("neighborhood") or "").strip()
    property_type = str(data.get("type_bien") or data.get("property_type") or "").strip()
    rooms         = _safe_int(data.get("pieces") or data.get("rooms"))
    beds          = _safe_int(data.get("chambres") or data.get("bedrooms") or data.get("beds"))
    baths         = _safe_int(data.get("salles_bain") or data.get("bathrooms"))

    surface_raw = data.get("surface", "")
    area = ""
    try:
        sv = float(str(surface_raw)) if surface_raw not in ("", None) else float("nan")
        if sv == sv:  # NaN check
            area = f"{int(sv)} m²"
    except (ValueError, TypeError):
        area = ""

    # Media → images list (may be base64 data URLs or http URLs)
    media = data.get("media") or []
    images = []
    if isinstance(media, list):
        for item in media:
            url = item.get("url", "") if isinstance(item, dict) else str(item)
            if url:
                images.append(url)

    # Equipements → tags
    equipements = data.get("equipements") or data.get("tags") or []
    tags = equipements if isinstance(equipements, list) else []

    # Transaction type from agent intent
    intent = agent_lst.intent or "sell"
    transaction_type = "Vente" if intent == "sell" else "Location"

    # Title: use agent-provided or auto-generate
    title = (
        str(data.get("titre") or data.get("title") or "").strip()
        or _make_title({"property_type": property_type, "bedrooms": beds, "state": data.get("etat", "")})
    )

    url = agent_lst.listing_url
    return {
        "id":               url,
        "url":              url,
        "title":            title,
        "location":         f"{city}, {neighborhood}" if neighborhood else city,
        "city":             city,
        "neighborhood":     neighborhood,
        "price":            price,
        "price_formatted":  _format_price(price),
        "images":           images,
        "image":            images[0] if images else "",
        "beds":             beds,
        "baths":            baths,
        "rooms":            rooms,
        "area":             area,
        "property_type":    property_type,
        "state":            str(data.get("etat") or data.get("state") or ""),
        "standing":         "",
        "transaction_type": transaction_type,
        "visual_style":     "",
        "natural_light":    "",
        "visual_condition": "",
        "score":            1.0,
        "tags":             tags,
        "match_tags":       tags[:3],
        "description":      str(data.get("description") or ""),
        "phone_number":     str(data.get("phone_number") or ""),
        "map_link":         "",
        "address":          str(data.get("adresse") or data.get("address") or ""),
        "is_agent_listing": True,
    }


def _serialize(listing: dict) -> dict:
    """Convert a recommender result dict to the API response shape."""
    url = listing.get("url", "")
    price = listing.get("price", 0)
    try:
        price = int(float(str(price)))
    except (ValueError, TypeError):
        price = 0

    # Images: stored as "| "-separated string (may also be "; "-separated from old imports)
    raw_images = str(listing.get("images", "") or "")
    images = [u.strip() for u in raw_images.replace(" | ", "|").replace(";", "|").split("|") if u.strip()]

    neighborhood = str(listing.get("neighborhood") or "").strip()
    city = str(listing.get("city") or "").strip()

    # Beds / baths
    def _safe_int(val):
        try:
            v = int(float(str(val)))
            return v if v > 0 else None
        except (ValueError, TypeError):
            return None

    beds   = _safe_int(listing.get("bedrooms"))
    baths  = _safe_int(listing.get("bathrooms"))
    rooms  = _safe_int(listing.get("rooms"))

    raw_surface = listing.get("surface", "")
    area = ""
    try:
        sv = float(str(raw_surface)) if raw_surface not in ("", None) else float("nan")
        if sv == sv:  # NaN check (NaN != NaN)
            area = f"{int(sv)} m²"
    except (ValueError, TypeError):
        area = ""

    # Amenity tags (for quick display and details)
    amenity_labels = {
        "has_ascenseur":          "Ascenseur",
        "has_terrasse":           "Terrasse",
        "has_piscine":            "Piscine",
        "has_garage":             "Parking",
        "has_meuble":             "Meublé",
        "has_climatisation":      "Clim",
        "has_securite":           "Sécurité",
        "has_jardin":             "Jardin",
        "has_vue_mer":            "Vue mer",
        "has_cuisine_equipee":    "Cuisine équipée",
        "has_concierge":          "Concierge",
        "has_salon_europeen":     "Salon européen",
        "has_salon_marocain":     "Salon marocain",
        "has_antenne_parabolique":"Antenne parabolique",
        "has_double_vitrage":     "Double vitrage",
        "has_facade_exterieure":  "Façade extérieure",
        "has_cheminee":           "Cheminée",
        "has_machine_laver":      "Machine à laver",
        "has_porte_blindee":      "Porte blindée",
        "has_chauffage_central":  "Chauffage central",
        "has_chambre_rangement":  "Chambre rangement",
        "has_entre_seul":         "Entre-seul",
        "has_four":               "Four",
        "has_micro_ondes":        "Micro-ondes",
        "has_refrigerateur":      "Réfrigérateur",
    }
    tags = [label for col, label in amenity_labels.items() if listing.get(col) == 1]

    return {
        "id":           url,                      # unique ID for React keying
        "url":          url,
        "title":        _make_title(listing),
        "location":     f"{city}, {neighborhood}" if neighborhood else city,
        "city":         city,
        "neighborhood": neighborhood,
        "price":        price,
        "price_formatted": _format_price(price),
        "images":       images,
        # Keep a single 'image' field for backwards-compat with current Card component
        "image":        images[0] if images else "",
        "beds":         beds,
        "baths":        baths,
        "rooms":        rooms,
        "area":         area,
        "property_type": str(listing.get("property_type") or ""),
        "state":         str(listing.get("state") or ""),
        "standing":      str(listing.get("standing") or ""),
        "transaction_type": str(listing.get("transaction_type") or ""),
        "visual_style":  str(listing.get("visual_style") or ""),
        "natural_light": str(listing.get("natural_light") or ""),
        "visual_condition": str(listing.get("visual_condition") or ""),
        "score":         round(float(listing.get("score", 0)), 3),
        "tags":          tags, # Return all matched amenities
        "match_tags":    listing.get("match_tags", []),
        "description":            str(listing.get("description") or ""),
        "description_normalized": str(listing.get("description_normalized") or ""),
        "phone_number":  str(listing.get("phone_number") or ""),
        "map_link":      str(listing.get("map_link") or ""),
        "address":       str(listing.get("address") or ""),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Request / Response schemas
# ─────────────────────────────────────────────────────────────────────────────

class RecommendRequest(BaseModel):
    filters: dict            # raw interview answers (question IDs as keys)
    intent: str = "buy"      # "buy" | "rent" | "sell"
    top_n: int = 30
    min_score: float = 0.0
    exclude_urls: list[str] = []


class SwipeRequest(BaseModel):
    liked_urls: list[str] = []
    disliked_urls: list[str] = []
    learning_rate: float = 0.08

class ChatMessage(BaseModel):
    id: str
    type: str
    text: str
    image: Optional[str] = None  # Base64 encoded image data

class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    filters: dict = {}
    conversation_id: Optional[int] = None

class ConversationCreate(BaseModel):
    lang: str = "EN"

    @field_validator("lang")
    @classmethod
    def validate_lang(cls, v: str) -> str:
        if v not in {"EN", "FR", "AR"}:
            raise ValueError("lang must be one of: EN, FR, AR")
        return v

# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/recommend")
def recommend(req: RecommendRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Accept interview filters, run the recommender, return ranked listings.

    Request body:
      {
        "filters": { "ville": "Casablanca", "budget": 1500000, "chambres": "3", ... },
        "intent": "buy",
        "top_n": 20
      }
    """
    # ── Debug: log raw filters + mapped prefs (helps trace voice agent issues) ─
    print(f"\n[RECOMMEND] intent={req.intent!r}  raw_filters={req.filters}")
    prefs = _map_filters(req.filters, req.intent)
    print(f"[RECOMMEND] mapped → city={prefs.city!r}  price_max={prefs.price_max}"
          f"  type={prefs.property_type!r}  surface_min={prefs.surface_min}"
          f"  bedrooms={prefs.bedrooms}  neighborhoods={prefs.neighborhoods}")
    
    # Log visual/architectural preferences if present
    visual_prefs = []
    if prefs.visual_style: visual_prefs.append(f"style={prefs.visual_style}")
    if prefs.natural_light: visual_prefs.append(f"light={prefs.natural_light}")
    if prefs.architectural_vibe: visual_prefs.append(f"vibe={prefs.architectural_vibe}")
    if prefs.furnishing_status: visual_prefs.append(f"furnished={prefs.furnishing_status}")
    if prefs.dominant_view: visual_prefs.append(f"view={prefs.dominant_view}")
    if prefs.floor_material: visual_prefs.append(f"floor={prefs.floor_material}")
    if visual_prefs:
        print(f"[RECOMMEND] visual prefs → {', '.join(visual_prefs)}")
    
    # Log amenity preferences if present
    amenity_prefs = [col.replace("has_", "") for col in AMENITY_COLS if getattr(prefs, col) is True]
    if amenity_prefs:
        print(f"[RECOMMEND] amenities → {', '.join(amenity_prefs)}")

    # Handle empty database case
    if len(_recommender.df) == 0:
        print("[RECOMMEND] No listings available in database")
        return {
            "listings": [],
            "total": 0,
            "message": "No listings available. Database may be empty or not properly initialized.",
            "prefs": {
                "transaction_type": prefs.transaction_type,
                "city": prefs.city,
                "price_max": prefs.price_max,
                "property_type": prefs.property_type,
                "surface_min": prefs.surface_min,
                "bedrooms": prefs.bedrooms,
                "neighborhoods": prefs.neighborhoods,
            },
        }

    # ── Merge active agent listings into the recommender df before scoring ──
    agent_rows = [
        _agent_listing_to_df_row(al)
        for al in db.query(AgentListing).filter(AgentListing.is_active == True).all()
    ]
    if agent_rows:
        agent_df = _pd.DataFrame(agent_rows)
        # Align columns with main df (fill missing cols with 0 / "")
        for col in _recommender.df.columns:
            if col not in agent_df.columns:
                agent_df[col] = 0 if col in AMENITY_COLS else ""
        agent_df = agent_df[_recommender.df.columns]  # same column order
        original_df = _recommender.df
        _recommender.df = _pd.concat([original_df, agent_df], ignore_index=True)
        print(f"[RECOMMEND] merged {len(agent_rows)} agent listing(s) into recommender df")
    else:
        original_df = None

    try:
        results = _recommender.recommend(
            prefs,
            top_n=req.top_n,
            min_score=req.min_score,
            exclude_urls=req.exclude_urls
        )
    finally:
        # Always restore original df so we don't mutate the singleton permanently
        if agent_rows:
            _recommender.df = original_df

    print(f"[RECOMMEND] → {len(results)} results returned")
    if results:
        top_scores = [f"{r['score']:.3f} ({r.get('neighborhood','')})" for r in results[:3]]
        print(f"[RECOMMEND] Top 3 scores: {top_scores}")
    print()
    return {
        "listings": [_serialize(r) for r in results],
        "total": len(results),
        "prefs": {
            "transaction_type": prefs.transaction_type,
            "city": prefs.city,
            "price_max": prefs.price_max,
            "property_type": prefs.property_type,
            "surface_min": prefs.surface_min,
            "bedrooms": prefs.bedrooms,
            "neighborhoods": prefs.neighborhoods,
        },
    }


@app.post("/api/swipe")
def swipe(req: SwipeRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Update recommender weights based on swipe feedback.

    Request body:
      { "liked_urls": [...], "disliked_urls": [...] }
    """
    if req.liked_urls or req.disliked_urls:
        _recommender.update_from_swipes(
            req.liked_urls,
            req.disliked_urls,
            learning_rate=req.learning_rate,
        )
    # Record likes on agent-published listings
    for url in (req.liked_urls or []):
        if url.startswith("dari://agent-listing/"):
            lst = db.query(AgentListing).filter_by(listing_url=url, is_active=True).first()
            if lst:
                existing = db.query(ListingLike).filter_by(listing_id=lst.id, user_id=current_user.id).first()
                if not existing:
                    db.add(ListingLike(listing_id=lst.id, user_id=current_user.id))
    db.commit()
    return {
        "weights": _recommender.weights,
        "ok": True,
    }


@app.get("/api/neighborhoods")
def neighborhoods(city: str = Query("Casablanca")):
    """
    Return sorted list of neighborhoods available in the dataset for a city.

    Query param: city (default: Casablanca)
    """
    result = _recommender.available_neighborhoods(city)
    return {"city": city, "neighborhoods": result, "count": len(result)}


@app.get("/api/price-range")
def price_range(
    city: str = Query("Casablanca"),
    transaction_type: str = Query("Vente"),
    property_type: str = Query(None),
):
    """
    Return p10 / median / p90 price for the given filters — shown in the UI
    as a market context hint after the user sets their budget.
    """
    prefs = UserPreferences(transaction_type=transaction_type, city=city)
    if property_type:
        prefs.property_type = property_type
    stats = _recommender.price_range(prefs)
    return stats


@app.get("/")
def root():
    """Root endpoint - redirects to API documentation"""
    return {
        "message": "Dari Real Estate API",
        "version": "1.0.0",
        "status": "running",
        "endpoints": {
            "health": "/api/health",
            "docs": "/docs",
            "redoc": "/redoc"
        }
    }


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "listings_loaded": len(_recommender.df),
        "database_url": os.getenv("DATABASE_URL", "not set"),
        "data_file": getattr(_rec_module, 'DATA_PATH', 'not set'),
        "recommender_type": type(_recommender).__name__,
        "environment": {
            "AUTH_SECRET_KEY": "set" if os.getenv("AUTH_SECRET_KEY") else "not set",
            "CORS_ORIGINS": os.getenv("CORS_ORIGINS", "not set"),
            "GEMINI_API_KEY": "set" if os.getenv("GEMINI_API_KEY") else "not set",
        }
    }


@app.get("/api/user/chat-history")
def get_chat_history(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Return the saved conversation for the current user."""
    import json
    row = db.query(ChatConversation).filter(ChatConversation.user_id == current_user.id).first()
    if not row:
        return {"messages": [], "updated_at": None}
    return {"messages": json.loads(row.messages), "updated_at": row.updated_at}


@app.post("/api/user/chat-history")
def save_chat_history(payload: dict, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Upsert the full conversation for the current user."""
    import json
    messages = payload.get("messages", [])
    row = db.query(ChatConversation).filter(ChatConversation.user_id == current_user.id).first()
    if row:
        row.messages = json.dumps(messages)
    else:
        row = ChatConversation(user_id=current_user.id, messages=json.dumps(messages))
        db.add(row)
    db.commit()
    return {"saved": True}


@app.delete("/api/user/chat-history")
def clear_chat_history(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Delete saved conversation for the current user."""
    db.query(ChatConversation).filter(ChatConversation.user_id == current_user.id).delete()
    db.commit()
    return {"cleared": True}


@app.get("/api/user/conversations")
def list_conversations(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(ChatConversation)
        .filter(ChatConversation.user_id == current_user.id)
        .order_by(ChatConversation.updated_at.desc())
        .all()
    )
    result = []
    for row in rows:
        try:
            messages = json.loads(row.messages or "[]")
        except (json.JSONDecodeError, TypeError):
            messages = []
        user_messages = [m for m in messages if m.get("type") == "user"]
        preview = user_messages[0]["text"][:60] if user_messages else ""
        result.append({
            "id": row.id,
            "title": row.title,
            "lang": row.lang,
            "created_at": row.updated_at.isoformat() if row.updated_at else None,
            "preview": preview,
        })
    return result


@app.post("/api/user/conversations", status_code=201)
def create_conversation(
    payload: ConversationCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conv = ChatConversation(
        user_id=current_user.id,
        messages="[]",
        lang=payload.lang,
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return {"id": conv.id}


@app.get("/api/user/conversations/{conv_id}")
def get_conversation(
    conv_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.query(ChatConversation).filter(
        ChatConversation.id == conv_id,
        ChatConversation.user_id == current_user.id,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Conversation not found")
    try:
        messages = json.loads(row.messages or "[]")
    except (json.JSONDecodeError, TypeError):
        messages = []
    return {
        "id": row.id,
        "title": row.title,
        "lang": row.lang,
        "messages": messages,
        "created_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@app.delete("/api/user/conversations/{conv_id}")
def delete_conversation(
    conv_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.query(ChatConversation).filter(
        ChatConversation.id == conv_id,
        ChatConversation.user_id == current_user.id,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Conversation not found")
    db.delete(row)
    db.commit()
    return {"deleted": True}


@app.post("/api/user/conversations/{conv_id}/generate-title")
def generate_conversation_title(
    conv_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.query(ChatConversation).filter(
        ChatConversation.id == conv_id,
        ChatConversation.user_id == current_user.id,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Conversation not found")

    try:
        messages = json.loads(row.messages or "[]")
    except (json.JSONDecodeError, TypeError):
        messages = []

    user_msgs = [m for m in messages if m.get("type") == "user"]
    bot_msgs = [m for m in messages if m.get("type") == "bot"]

    if not user_msgs or not bot_msgs:
        return {"title": None}

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return {"title": None}

    lang_names = {"EN": "English", "FR": "French", "AR": "Moroccan Arabic (Darija)"}
    lang_name = lang_names.get(row.lang, "English")

    prompt = (
        f"Summarize the following conversation topic in 5 words or fewer. "
        f"Respond in {lang_name} only. No punctuation, no quotes.\n"
        f"User: {user_msgs[0]['text'][:200]}\n"
        f"Assistant: {bot_msgs[0]['text'][:200]}"
    )

    try:
        client_gemini = genai.Client(api_key=api_key)
        response = client_gemini.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
        )
        title = response.text.strip()[:80]
        row.title = title
        db.commit()
        return {"title": title}
    except Exception:
        return {"title": None}


@app.post("/api/chat")
def chat(req: ChatRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Sends conversational messages to Gemini to act as Dari, the assistant.
    Uses RAG: injects relevant listings from the DB and the user's saved listings
    into the system prompt so Gemini can answer grounded questions.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return {"reply": "Désolé, la clé de l'API Gemini n'est pas configurée sur ce serveur."}

    try:
        import base64, json

        # ── RAG: fetch saved listings for this user ────────────────────────────
        saved_rows = (
            db.query(SavedListing)
            .filter(SavedListing.user_id == current_user.id)
            .order_by(SavedListing.saved_at.desc())
            .limit(10)
            .all()
        )
        saved_listings = []
        for s in saved_rows:
            data = json.loads(s.listing_data) if s.listing_data else {}
            if data:
                saved_listings.append(data)

        # ── RAG: retrieve top relevant listings via recommender ────────────────
        rag_listings = []
        try:
            # Merge static filters with any city/neighborhood keywords from the full conversation
            merged_filters = dict(req.filters or {})
            conv_text = " ".join(m.text for m in req.messages).lower()
            if not merged_filters.get("ville") and not merged_filters.get("city"):
                for city, variants in {
                    "Casablanca": ["casablanca", "casa", "belvédère", "belveder", "maarif", "ain diab", "triangle d'or", "anfa", "bourgogne"],
                    "Rabat": ["rabat", "agdal", "hay riad", "souissi"],
                    "Marrakech": ["marrakech", "guéliz", "hivernage"],
                    "Tanger": ["tanger", "tangier"],
                    "Agadir": ["agadir"],
                    "Fès": ["fès", "fez"],
                }.items():
                    if any(v in conv_text for v in variants):
                        merged_filters["ville"] = city
                        break
            prefs = _map_filters(merged_filters)
            raw_recs = _recommender.recommend(prefs, top_n=10, exclude_urls=[])
            rag_listings = [_serialize(r) for r in raw_recs]
            # If no results with filters, fall back to top listings without filter
            if not rag_listings:
                raw_recs = _recommender.recommend(_map_filters({}), top_n=10, exclude_urls=[])
                rag_listings = [_serialize(r) for r in raw_recs]
            # Last resort: load directly from dataframe
            if not rag_listings and hasattr(_recommender, "df") and _recommender.df is not None:
                rag_listings = [_serialize(r) for r in _recommender.df.head(10).to_dict(orient="records")]
        except Exception as rag_err:
            print(f"[RAG] recommender error: {rag_err}")
            try:
                if hasattr(_recommender, "df") and _recommender.df is not None:
                    rag_listings = [_serialize(r) for r in _recommender.df.head(10).to_dict(orient="records")]
            except Exception:
                pass

        # ── Build context block ────────────────────────────────────────────────
        def _fmt(lst, label):
            if not lst:
                return ""
            lines = [f"\n\n=== {label} ==="]
            for i, l in enumerate(lst, 1):
                price = l.get("price_formatted") or (f"{l.get('price'):,}" if l.get("price") else "N/A")
                tags  = ", ".join((l.get("tags") or l.get("match_tags") or [])[:5])
                lines.append(
                    f"{i}. {l.get('title','Bien')} | {l.get('location','')} | {price} DH"
                    f" | {l.get('area','')} | {l.get('beds','')} ch | Score: {round(float(l.get('score') or 0)*100)}%"
                    + (f" | Équipements: {tags}" if tags else "")
                    + (f"\n   URL: {l.get('url','')}" if l.get('url') else "")
                )
            return "\n".join(lines)

        rag_context = _fmt(rag_listings, "ANNONCES PERTINENTES DU CATALOGUE")
        fav_context = _fmt(saved_listings, "ANNONCES FAVORITES DE L'UTILISATEUR")

        client = genai.Client(api_key=api_key)

        system_instruction = (
            "Tu es Dari, un assistant immobilier marocain chaleureux, professionnel et expert. "
            "Tu aides les utilisateurs à trouver le bien idéal, à affiner leurs critères et à répondre à leurs questions. "
            "Tu peux analyser des images de propriétés et fournir des informations détaillées. "
            "RÈGLES IMPORTANTES: Ne pose qu'une seule question à la fois. Garde tes réponses courtes, directes et concises. "
            "Adopte une approche conversationnelle étape par étape. "
            "Ne fais jamais de longues listes de questions. Parle toujours en français de manière conviviale. "
            "N'utilise JAMAIS de formatage Markdown (pas d'astérisques, pas de tirets, texte brut uniquement). "
            "Quand tu analyses une image de propriété, décris: le style architectural, l'état, les équipements visibles, "
            "l'ambiance, la luminosité, et donne une estimation du standing.\n\n"
            "AFFICHAGE D'UN BIEN: Quand l'utilisateur demande à voir, afficher ou consulter un bien spécifique que tu viens de mentionner, "
            "ajoute EXACTEMENT à la toute fin de ta réponse (sans espaces autour) le marqueur [SHOW:URL] en remplaçant URL par l'URL exacte du bien. "
            "N'explique pas ce marqueur, ne le mentionne pas dans le texte visible. Exemple: [SHOW:https://mubawab.ma/...]\n\n"
            "BASE DE CONNAISSANCES — utilise ces données réelles pour répondre aux questions sur les biens disponibles, "
            "les prix, les quartiers et faire des suggestions personnalisées. "
            "Cite des biens spécifiques (titre, localisation, prix) quand c'est pertinent."
            + rag_context
            + fav_context
        )
        if req.filters:
            system_instruction += f"\n\nPréférences actuelles de l'utilisateur : {req.filters}."
            
        # Format the history for GenAI
        contents = []
        for msg in req.messages:
            role = "user" if msg.type == "user" else "model"
            parts = []
            
            # Add text part if present
            if msg.text:
                parts.append(types.Part.from_text(text=msg.text))
            
            # Add image part if present
            if hasattr(msg, 'image') and msg.image:
                try:
                    # Extract base64 data and mime type from data URL
                    if msg.image.startswith('data:'):
                        # Format: data:image/jpeg;base64,/9j/4AAQ...
                        header, base64_data = msg.image.split(',', 1)
                        mime_type = header.split(':')[1].split(';')[0]
                    else:
                        # Assume it's raw base64 and default to jpeg
                        base64_data = msg.image
                        mime_type = 'image/jpeg'
                    
                    # Decode base64 to bytes
                    image_bytes = base64.b64decode(base64_data)
                    
                    # Add image using from_bytes
                    parts.append(types.Part.from_bytes(
                        data=image_bytes,
                        mime_type=mime_type
                    ))
                except Exception as img_error:
                    print(f"Error processing image: {img_error}")
                    # Continue without the image if there's an error
            
            if parts:  # Only add if there are parts
                contents.append(types.Content(role=role, parts=parts))

        # Use the same model that was working before (gemini-2.5-flash)
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.7,
            ),
        )

        import re
        raw_reply = response.text or ""

        # ── Detect which listing to show ──────────────────────────────────────
        # Strategy 1: Gemini emitted [SHOW:url] marker
        # Strategy 2: User message signals display intent → attach best RAG match
        DISPLAY_KEYWORDS = [
            "affiche", "afficher", "montrer", "montre", "voir", "consulter",
            "show", "display", "voir ce bien", "je veux le voir", "oui", "ok",
        ]
        last_user_text = next(
            (m.text.lower() for m in reversed(req.messages) if m.type == "user"), ""
        )
        user_wants_display = any(kw in last_user_text for kw in DISPLAY_KEYWORDS)

        all_candidates = rag_listings + saved_listings
        show_listing = None

        # Strategy 1: parse Gemini's [SHOW:url] marker (fuzzy URL match)
        show_match = re.search(r'\[SHOW:([^\]]+)\]', raw_reply)
        if show_match:
            show_url = show_match.group(1).strip()
            # Exact match first, then partial
            show_listing = (
                next((l for l in all_candidates if l.get("url") == show_url), None)
                or next((l for l in all_candidates if show_url in (l.get("url") or "") or (l.get("url") or "") in show_url), None)
            )
            raw_reply = re.sub(r'\s*\[SHOW:[^\]]*\]', '', raw_reply).strip()

        # Strategy 2: user asked to display + Gemini's reply mentions a listing title
        if not show_listing and user_wants_display and all_candidates:
            reply_lower = raw_reply.lower()
            # Try to match a candidate whose title/location words appear in the reply
            for candidate in all_candidates:
                title_words = [w for w in (candidate.get("title") or "").lower().split() if len(w) > 3]
                if title_words and sum(1 for w in title_words if w in reply_lower) >= 1:
                    show_listing = candidate
                    break
            # Fallback: return top RAG listing, or fetch raw listings if RAG was empty
            if not show_listing and user_wants_display:
                if all_candidates:
                    show_listing = all_candidates[0]
                else:
                    try:
                        raw_all = _recommender.df.head(1).to_dict(orient="records") if hasattr(_recommender, "df") else []
                        show_listing = _serialize(raw_all[0]) if raw_all else None
                    except Exception:
                        pass

        # ── Auto-save conversation ─────────────────────────────────────────────
        try:
            all_msgs = [{"id": m.id, "type": m.type, "text": m.text} for m in req.messages]
            all_msgs.append({"id": f"bot-{len(all_msgs)}", "type": "bot", "text": raw_reply})
            if req.conversation_id:
                conv = db.query(ChatConversation).filter(
                    ChatConversation.id == req.conversation_id,
                    ChatConversation.user_id == current_user.id,
                ).first()
                if conv:
                    conv.messages = json.dumps(all_msgs)
                    db.commit()
            else:
                # Backward compat: save to most recent conversation, or create one
                conv = (
                    db.query(ChatConversation)
                    .filter(ChatConversation.user_id == current_user.id)
                    .order_by(ChatConversation.updated_at.desc())
                    .first()
                )
                if conv:
                    conv.messages = json.dumps(all_msgs)
                else:
                    conv = ChatConversation(
                        user_id=current_user.id, messages=json.dumps(all_msgs), lang="EN"
                    )
                    db.add(conv)
                db.commit()
        except Exception as save_err:
            print(f"[chat] conversation save error: {save_err}")

        result = {"reply": raw_reply}
        if show_listing:
            result["listing"] = show_listing
        return result
    except Exception as e:
        import traceback
        print("=" * 60)
        print("CHAT ERROR:")
        traceback.print_exc()
        print("=" * 60)
        return {"reply": "Désolé, je rencontre des difficultés techniques à me connecter à mon réseau intelligent."}


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
