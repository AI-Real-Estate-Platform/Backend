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

import os
import re
import secrets
import sys
from datetime import datetime, timedelta
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
from pydantic import BaseModel, EmailStr, constr
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

from recommender import Recommender, UserPreferences  # noqa: E402
from database import Base, engine, get_db  # noqa: E402
from models import User, SavedListing, Booking, ViewHistory  # noqa: E402

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

# CORS configuration - allow specific origins from environment variable
cors_origins = os.getenv("CORS_ORIGINS", "*")
if cors_origins == "*":
    allowed_origins = ["*"]
else:
    allowed_origins = [origin.strip() for origin in cors_origins.split(",")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
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
ACCESS_TOKEN_EXPIRE_MINUTES = 60

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


class RegisterRequest(BaseModel):
    email: EmailStr
    password: constr(min_length=8)
    full_name: Optional[str] = None


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


class UserOut(BaseModel):
    id: int
    email: EmailStr
    full_name: Optional[str] = None
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
    token = secrets.token_urlsafe(32)
    user.reset_token = token
    user.reset_token_expires = datetime.utcnow() + timedelta(minutes=30)
    db.add(user)
    db.commit()
    db.refresh(user)
    return token


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
    user = User(
        email=payload.email,
        full_name=payload.full_name,
        hashed_password=_hash_password(payload.password),
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
    access_token = _create_access_token({"sub": user.email})
    return {"access_token": access_token, "token_type": "bearer"}


@app.post("/api/auth/reset/request")
def request_password_reset(payload: PasswordResetRequest, db: Session = Depends(get_db)):
    user = _get_user_by_email(db, payload.email)
    if user:
        token = _set_password_reset_token(db, user)
        return {"ok": True, "reset_token": token, "expires_in_minutes": 30}
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


@app.post("/api/user/bookings")
def create_booking(payload: BookingRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Create a property viewing/booking"""
    booking = Booking(
        user_id=current_user.id,
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
    "appartement":       "Appartement",
    "maison/villa":      "Villa",
    "maison":            "Villa",
    "villa":             "Villa",
    "riad":              "Riad",
    "bureau":            "Bureau",
    "local commercial":  "Local commercial",
    "terrain":           "Terrain",
    "studio":            "Studio",
    # English
    "apartment":         "Appartement",
    "house/villa":       "Villa",
    "house":             "Villa",
    "office":            "Bureau",
    "commercial":        "Local commercial",
    "land":              "Terrain",
    # Voice mode (Gemini may return these)
    "appartment":        "Appartement",
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
_AMENITY_MAP: dict[str, str] = {
    # French
    "parking":          "has_garage",
    "ascenseur":        "has_ascenseur",
    "terrasse":         "has_terrasse",
    "terrasse/balcon":  "has_terrasse",
    "meublé":           "has_meuble",
    "climatisation":    "has_climatisation",
    "sécurité":         "has_securite",
    "sécurité 24/7":    "has_securite",
    "cuisine équipée":  "has_cuisine_equipee",
    "piscine":          "has_piscine",
    "jardin":           "has_jardin",
    "chauffage central":"has_chauffage_central",
    # English
    "parking":          "has_garage",
    "elevator":         "has_ascenseur",
    "terrace":          "has_terrasse",
    "terrace/balcony":  "has_terrasse",
    "furnished":        "has_meuble",
    "ac":               "has_climatisation",
    "air conditioning": "has_climatisation",
    "security":         "has_securite",
    "24/7 security":    "has_securite",
    "equipped kitchen": "has_cuisine_equipee",
    "pool":             "has_piscine",
    "garden":           "has_jardin",
    "central heating":  "has_chauffage_central",
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
            prefs.property_type = [_PROPERTY_TYPE_MAP.get(t.lower(), t.strip()) for t in raw_type if isinstance(t, str)]
        else:
            prefs.property_type = _PROPERTY_TYPE_MAP.get(raw_type.lower(), raw_type.strip())

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
    if "visual_style" in filters:
        prefs.visual_style = filters["visual_style"]
    if "visual_condition" in filters:
        prefs.visual_condition = filters["visual_condition"]
    if "natural_light" in filters:
        prefs.natural_light = filters["natural_light"]
    if "furnishing_status" in filters:
        prefs.furnishing_status = filters["furnishing_status"]
    if "floor_material" in filters:
        prefs.floor_material = filters["floor_material"]
    if "dominant_view" in filters:
        prefs.dominant_view = filters["dominant_view"]
    if "architectural_vibe" in filters:
        prefs.architectural_vibe = filters["architectural_vibe"]
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
        "description":   str(listing.get("description") or ""),
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

class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    filters: dict = {}

# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/recommend")
def recommend(req: RecommendRequest, current_user: User = Depends(get_current_user)):
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

    results = _recommender.recommend(
        prefs, 
        top_n=req.top_n, 
        min_score=req.min_score, 
        exclude_urls=req.exclude_urls
    )
    print(f"[RECOMMEND] → {len(results)} results returned\n")
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
def swipe(req: SwipeRequest, current_user: User = Depends(get_current_user)):
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


@app.post("/api/chat")
def chat(req: ChatRequest, current_user: User = Depends(get_current_user)):
    """
    Sends conversational messages to Gemini to act as Dari, the assistant.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return {"reply": "Désolé, la clé de l'API Gemini n'est pas configurée sur ce serveur."}

    try:
        client = genai.Client(api_key=api_key)
        
        system_instruction = (
            "Tu es Dari, un assistant immobilier marocain chaleureux, professionnel et expert. "
            "Tu aides les utilisateurs à affiner leurs critères de recherche et à répondre à leurs questions. "
            "RÈGLES IMPORTANTES: Ne pose qu'une seule question à la fois. Garde tes réponses très courtes, directes et concises. "
            "Adopte une approche conversationnelle étape par étape, comme un véritable assistant. "
            "Ne fais jamais de longues listes de questions. Parle toujours en français de manière conviviale. "
            "N'utilise JAMAIS de formatage Markdown comme les astérisques (**) pour le texte en gras ou en italique. Formate tout en texte brut simple. "
        )
        if req.filters:
            system_instruction += f"Voici les préférences actuelles de l'utilisateur : {req.filters}."
            
        # Format the history for GenAI
        contents = []
        for msg in req.messages:
            role = "user" if msg.type == "user" else "model"
            contents.append(types.Content(role=role, parts=[types.Part.from_text(text=msg.text)]))

        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.7,
            ),
        )

        return {"reply": response.text}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"reply": "Désolé, je rencontre des difficultés techniques à me connecter à mon réseau intelligent."}


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
