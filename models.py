from sqlalchemy import Column, Integer, String, Float, Boolean, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base

class Listing(Base):
    __tablename__ = "listings"
    
    id = Column(Integer, primary_key=True, index=True)
    url = Column(String, unique=True, index=True)
    property_type = Column(String, index=True)
    transaction_type = Column(String, index=True)
    city = Column(String, index=True)
    neighborhood = Column(String, index=True)
    price = Column(Float, index=True)
    surface = Column(Float)
    rooms = Column(Integer)
    bedrooms = Column(Integer)
    bathrooms = Column(Integer)
    state = Column(String)
    standing = Column(String)
    description = Column(Text)
    images = Column(Text)  # stored as pipe-separated string
    phone_number = Column(String)
    map_link = Column(String)  # OpenStreetMap or Google Maps URL
    address = Column(String)   # raw address / adresse
    
    # Amenities (Booleans)
    has_ascenseur = Column(Boolean, default=False)
    has_terrasse = Column(Boolean, default=False)
    has_piscine = Column(Boolean, default=False)
    has_garage = Column(Boolean, default=False)
    has_meuble = Column(Boolean, default=False)
    has_climatisation = Column(Boolean, default=False)
    has_securite = Column(Boolean, default=False)
    has_jardin = Column(Boolean, default=False)
    has_vue_mer = Column(Boolean, default=False)
    has_cuisine_equipee = Column(Boolean, default=False)
    has_concierge = Column(Boolean, default=False)
    has_salon_europeen = Column(Boolean, default=False)
    has_salon_marocain = Column(Boolean, default=False)
    has_antenne_parabolique = Column(Boolean, default=False)
    has_double_vitrage = Column(Boolean, default=False)
    has_facade_exterieure = Column(Boolean, default=False)
    has_cheminee = Column(Boolean, default=False)
    has_machine_laver = Column(Boolean, default=False)
    has_porte_blindee = Column(Boolean, default=False)
    has_chauffage_central = Column(Boolean, default=False)
    has_chambre_rangement = Column(Boolean, default=False)
    has_entre_seul = Column(Boolean, default=False)
    has_four = Column(Boolean, default=False)
    has_micro_ondes = Column(Boolean, default=False)
    has_refrigerateur = Column(Boolean, default=False)

    # Visual Attributes
    visual_style = Column(String)
    natural_light = Column(String)
    visual_condition = Column(String)
    furnishing_status = Column(String)
    floor_material = Column(String)
    dominant_view = Column(String)
    architectural_vibe = Column(String)
    color_palette = Column(String)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    reset_token = Column(String, index=True, nullable=True)
    reset_token_expires = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    saved_listings = relationship("SavedListing", back_populates="user", cascade="all, delete-orphan")
    bookings = relationship("Booking", back_populates="user", cascade="all, delete-orphan")
    view_history = relationship("ViewHistory", back_populates="user", cascade="all, delete-orphan")


class SavedListing(Base):
    __tablename__ = "saved_listings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    listing_url = Column(String, nullable=False, index=True)
    listing_data = Column(Text, nullable=True)  # JSON string of the full listing data
    saved_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    notes = Column(Text, nullable=True)

    user = relationship("User", back_populates="saved_listings")


class Booking(Base):
    __tablename__ = "bookings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    listing_url = Column(String, nullable=False, index=True)
    booking_date = Column(DateTime(timezone=True), nullable=False)
    status = Column(String, default="pending", nullable=False)  # pending, confirmed, cancelled, completed
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User", back_populates="bookings")


class ViewHistory(Base):
    __tablename__ = "view_history"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    listing_url = Column(String, nullable=False, index=True)
    viewed_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User", back_populates="view_history")
