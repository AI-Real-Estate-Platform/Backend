"""
Real-estate recommendation engine
──────────────────────────────────
Architecture
  1. UserPreferences  – typed container for everything the chatbot collects.
  2. Recommender      – loads listings_clean.csv, runs hard filters then
                        weighted scoring, returns ranked results.
  3. Feedback         – update_from_swipes() adjusts feature weights based on
                        liked / disliked listings (Tinder-style learning).

Scoring pipeline
  Hard filters  →  only listings that match transaction_type, city,
                   property_type (if given) and budget pass through.
  Soft scoring  →  each remaining listing gets a 0-1 score per axis:
                     price · surface · bedrooms · location ·
                     quality (state+standing) · amenities
                   Final score = weighted average of axis scores.
"""

import math
import csv
import unicodedata
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

DATA_PATH = "../data/listings_clean.csv"

# All binary amenity columns (must match listings_clean.csv)
AMENITY_COLS = [
    "has_garage", "has_terrasse", "has_jardin", "has_ascenseur",
    "has_cuisine_equipee", "has_concierge", "has_climatisation",
    "has_securite", "has_salon_europeen", "has_salon_marocain",
    "has_antenne_parabolique", "has_double_vitrage", "has_facade_exterieure",
    "has_cheminee", "has_meuble", "has_machine_laver", "has_porte_blindee",
    "has_chauffage_central", "has_chambre_rangement", "has_piscine",
    "has_vue_mer", "has_entre_seul", "has_four", "has_micro_ondes",
    "has_refrigerateur",
]

# Default scoring weights — sum to 1.0
DEFAULT_WEIGHTS = {
    "price":     0.23,
    "surface":   0.15,
    "bedrooms":  0.10,
    "location":  0.15,
    "quality":   0.10,   # state + standing combined
    "amenities": 0.10,
    "visual":    0.12,
    "text":      0.05,   # description-based matching
}

# ─────────────────────────────────────────────────────────────────────────────
# Geographic Data (Casablanca)
# ─────────────────────────────────────────────────────────────────────────────

# Rough central coordinates for Casablanca neighborhoods (Latitude, Longitude)
NEIGHBORHOOD_COORDS = {
    'Ain Diab': (33.5936, -7.6698),
    'Ain Diab Extension': (33.5855, -7.6749),
    'Ain Chock': (33.5433, -7.6042),
    'Almaz': (33.5435, -7.6625),
    'Al Qods': (33.5415, -7.5318),
    'Al Hadika': (33.6015, -7.5702),
    'Alsace Lorraine': (33.5888, -7.6143),
    'Anassi': (33.5540, -7.5132),
    'Anfa': (33.5911, -7.6534),
    'Anfa Supérieur': (33.5835, -7.6621),
    'Aïn Sebaâ': (33.6067, -7.5342),
    'Beauséjour': (33.5670, -7.6534),
    'Belvédère': (33.5931, -7.5954),
    'Benjdia': (33.5822, -7.6083),
    'Bernoussi': (33.6125, -7.5167),
    'Bourgogne Est': (33.5979, -7.6331),
    'Bourgogne Ouest': (33.5937, -7.6436),
    'Californie': (33.5513, -7.6253),
    'Casablanca Finance City': (33.5544, -7.6644),
    'Casablanca Marina': (33.6063, -7.6225),
    'CIL (Hay Salam)': (33.5683, -7.6475),
    'Derb Ghalef': (33.5826, -7.6252),
    'El Manar - El Hank': (33.6033, -7.6542),
    'Ferme Bretonne (Hay Arraha)': (33.5654, -7.6521),
    'Foncière': (33.5841, -7.6163),
    'Franceville': (33.5786, -7.6358),
    'Gauthier': (33.5906, -7.6256),
    'Habbous': (33.5721, -7.6030),
    'Hay Alfarah': (33.5601, -7.5857),
    'Hay Hakam': (33.5573, -7.5741),
    'Hay Moulay Abdellah': (33.5539, -7.6186),
    'Hay Zobir': (33.5524, -7.6881),
    'Hermitage': (33.5708, -7.6111),
    'Inconnu': (33.5731, -7.5898), # City center fallback
    'La Floride': (33.5583, -7.6200),
    'Laymoune': (33.5562, -7.6713),
    'Les Crêtes': (33.5560, -7.6231),
    'Les Hôpitaux': (33.5756, -7.6191),
    'Les princesses': (33.5786, -7.6416),
    'Liberté': (33.5861, -7.6175),
    'Longchamps (Hay Al Hanâa)': (33.5734, -7.6524),
    'Mandarona': (33.5411, -7.6277),
    'Maârif': (33.5857, -7.6369),
    'Maârif Extension': (33.5818, -7.6401),
    'Mers Sultan': (33.5802, -7.6110),
    'Nassim 1': (33.5283, -7.6468),
    'Oasis': (33.5630, -7.6288),
    'Oasis sud': (33.5567, -7.6288),
    'Oulfa': (33.5550, -7.6749),
    'Palmier': (33.5833, -7.6258),
    'Plateau (Al Batha)': (33.5881, -7.6174),
    'Polo': (33.5615, -7.6133),
    'Quartier Bachkou': (33.5641, -7.6146),
    'Racine': (33.5901, -7.6404),
    'Racine Extension': (33.5864, -7.6431),
    'Riviera': (33.5702, -7.6358),
    'Roches Noires': (33.5947, -7.5831),
    'Sidi Maarouf': (33.5350, -7.6450),
    'Tantonville': (33.5851, -7.6365),
    'Triangle d\'Or': (33.5898, -7.6321),
    'Val Fleury': (33.5768, -7.6419),
}

def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance in km between two lat/lon coordinates."""
    R = 6371.0 # Earth radius in km
    lat1_rad, lon1_rad = math.radians(lat1), math.radians(lon1)
    lat2_rad, lon2_rad = math.radians(lat2), math.radians(lon2)
    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad
    a = math.sin(dlat / 2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def _normalize_name(name: str) -> str:
    if not name:
        return ""
    nfkd = unicodedata.normalize('NFKD', str(name))
    return "".join([c for c in nfkd if not unicodedata.combining(c)]).lower().strip()

_NORMALIZED_NEIGHS = {
    _normalize_name(k): k for k in NEIGHBORHOOD_COORDS.keys()
}

def _map_neighborhood(name: str) -> str:
    norm = _normalize_name(name)
    if norm in _NORMALIZED_NEIGHS:
        return _NORMALIZED_NEIGHS[norm]
    for norm_k, actual_k in _NORMALIZED_NEIGHS.items():
        if norm in norm_k or norm_k in norm:
            return actual_k
    return name


# ─────────────────────────────────────────────────────────────────────────────
# User Preferences
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class UserPreferences:
    """
    All fields are optional except transaction_type and city.
    Amenity preferences: set to True to request, False to exclude, None to ignore.
    """
    # ── Hard filters (must-match) ────────────────────────────────────────────
    transaction_type: str = "Vente"          # "Vente" or "Location"
    city: str = "Casablanca"

    # ── Budget ───────────────────────────────────────────────────────────────
    price_min: Optional[int] = None          # MAD
    price_max: Optional[int] = None          # MAD

    # ── Property specs ───────────────────────────────────────────────────────
    property_type: Optional[str | list[str]] = None      # Appartement | Villa | Studio | …
    surface_min: Optional[float] = None      # m² minimum
    surface_max: Optional[float] = None      # m² maximum (optional upper bound)
    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None

    # ── Location ─────────────────────────────────────────────────────────────
    neighborhoods: Optional[list[str]] = None  # preferred neighborhoods

    # ── Quality preferences ───────────────────────────────────────────────────
    state: Optional[str] = None              # Neuf|Prêt|Habitable|À rénover|…
    standing: Optional[str] = None          # Haut standing|Moyen standing|…

    # ── Amenities ────────────────────────────────────────────────────────────
    has_garage: Optional[bool] = None
    has_terrasse: Optional[bool] = None
    has_jardin: Optional[bool] = None
    has_ascenseur: Optional[bool] = None
    has_cuisine_equipee: Optional[bool] = None
    has_concierge: Optional[bool] = None
    has_climatisation: Optional[bool] = None
    has_securite: Optional[bool] = None
    has_salon_europeen: Optional[bool] = None
    has_salon_marocain: Optional[bool] = None
    has_antenne_parabolique: Optional[bool] = None
    has_double_vitrage: Optional[bool] = None
    has_facade_exterieure: Optional[bool] = None
    has_cheminee: Optional[bool] = None
    has_meuble: Optional[bool] = None
    has_machine_laver: Optional[bool] = None
    has_porte_blindee: Optional[bool] = None
    has_chauffage_central: Optional[bool] = None
    has_chambre_rangement: Optional[bool] = None
    has_piscine: Optional[bool] = None
    has_vue_mer: Optional[bool] = None
    has_entre_seul: Optional[bool] = None
    has_four: Optional[bool] = None
    has_micro_ondes: Optional[bool] = None
    has_refrigerateur: Optional[bool] = None

    # ── Visual Preferences ───────────────────────────────────────────────────
    visual_style: Optional[str] = None       # e.g., 'Modern', 'Traditional'
    visual_condition: Optional[str] = None   # e.g., 'New/Renovated', 'Good'
    natural_light: Optional[str] = None      # e.g., 'High', 'Medium', 'Low'
    furnishing_status: Optional[str] = None  # e.g., 'Fully Furnished', 'Empty'
    floor_material: Optional[str] = None     # e.g., 'Tile/Marble', 'Parquet/Wood'
    dominant_view: Optional[str] = None      # e.g., 'Nature/Greenery', 'Water/Sea'
    architectural_vibe: Optional[str] = None # e.g., 'Beldi/Moroccan', 'Industrial/Loft'
    color_palette: Optional[str] = None      # e.g., 'Warm Tones', 'Cool/Neutral Tones'


# ─────────────────────────────────────────────────────────────────────────────
# Scoring helpers
# ─────────────────────────────────────────────────────────────────────────────

def _gaussian(value: float, target: float, sigma: float) -> float:
    """
    Returns 1.0 when value == target, decreasing symmetrically.
    sigma controls how quickly the score drops (tolerance width).
    """
    return math.exp(-0.5 * ((value - target) / sigma) ** 2)


def _price_score(price: float, prefs: UserPreferences) -> float:
    """
    1.0  → price is exactly at the midpoint of the user's budget range.
    0.0  → price is far outside the stated range.
    Listings inside the range are always scored ≥ 0.8.
    """
    lo = prefs.price_min or 0
    hi = prefs.price_max or float("inf")

    if lo <= price <= hi:
        # Perfect if the budget range is tight; cap at 1.0
        mid = (lo + hi) / 2 if hi < float("inf") else lo * 1.5
        sigma = max((hi - lo) / 4, mid * 0.15)
        return max(0.80, _gaussian(price, mid, sigma))

    # Outside range — penalise proportionally
    if price < lo:
        gap = (lo - price) / lo
    else:
        gap = (price - hi) / hi
    return max(0.0, 1.0 - gap * 2)


def _surface_score(surface: float, prefs: UserPreferences) -> float:
    """
    Penalise below surface_min linearly.
    Soft penalty for far above surface_max (if given).
    """
    lo = prefs.surface_min or 0.0
    hi = prefs.surface_max or float("inf")

    if surface < lo:
        return max(0.0, surface / lo)                 # linear below min

    if hi < float("inf") and surface > hi:
        overshoot = (surface - hi) / hi
        return max(0.5, 1.0 - overshoot * 0.5)       # soft penalty above max

    # Inside range → slight preference toward the centre
    if lo > 0 and hi < float("inf"):
        mid = (lo + hi) / 2
        sigma = (hi - lo) / 3
        return max(0.80, _gaussian(surface, mid, sigma))

    # Only min given — favour surfaces just above min
    sigma = max(lo * 0.3, 20.0)
    return max(0.80, _gaussian(surface, lo * 1.2, sigma))


def _int_score(value: int, target: int, tolerance: int = 1) -> float:
    """
    1.0 for exact match, decreasing by 0.25 per unit difference up to tolerance.
    """
    diff = abs(value - target)
    if diff == 0:
        return 1.0
    if diff <= tolerance:
        return max(0.5, 1.0 - diff * 0.25)
    return max(0.0, 1.0 - diff * 0.15)


def _location_score(row: pd.Series, prefs: UserPreferences) -> float:
    if not prefs.neighborhoods:
        return 1.0   # no preference → all neighbourhoods are equal
        
    mapped_prefs = [_map_neighborhood(n) for n in prefs.neighborhoods]
    
    if row["neighborhood"] in mapped_prefs:
        return 1.0

    # Calculate shortest distance to any of the preferred neighborhoods
    row_coord = NEIGHBORHOOD_COORDS.get(row["neighborhood"])
    if not row_coord:
        return 0.0

    distances = []
    for pref_neigh in mapped_prefs:
        pref_coord = NEIGHBORHOOD_COORDS.get(pref_neigh)
        if pref_coord:
            distances.append(_haversine(*row_coord, *pref_coord))

    if not distances:
        return 0.0

        
    min_dist = min(distances)
    # Use a MUCH sharper Gaussian curve so score drops very fast with distance (sigma=0.8 km)
    # This ensures nearby neighborhoods get decent scores, but distant ones are heavily penalized
    # A property 0.5 km away gets ~0.70, 1.0 km gets ~0.45, 2 km gets ~0.08, 3+ km gets ~0.0
    return max(0.0, _gaussian(min_dist, 0, 0.8))


def _quality_score(row: pd.Series, prefs: UserPreferences) -> float:
    scores = []
    if prefs.state:
        scores.append(1.0 if row["state"] == prefs.state else 0.2)
    if prefs.standing:
        scores.append(1.0 if row["standing"] == prefs.standing else 0.2)
    return float(np.mean(scores)) if scores else 1.0


def _amenity_score(row: pd.Series, prefs: UserPreferences) -> float:
    wanted  = [c for c in AMENITY_COLS if getattr(prefs, c) is True]
    blocked = [c for c in AMENITY_COLS if getattr(prefs, c) is False]

    if not wanted and not blocked:
        return 1.0

    # Hard exclusion: listing has something user explicitly doesn't want
    for c in blocked:
        if row[c] == 1:
            return 0.0

    if not wanted:
        return 1.0

    matches = sum(1 for c in wanted if row[c] == 1)
    return matches / len(wanted)


def _normalize_text(value: str) -> str:
    if not value:
        return ""
    s = str(value).lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    for ch in [",", ".", ";", ":", "!", "?", "(", ")", "[", "]", "{", "}", "-", "_", "/", "\\", "\n", "\t"]:
        s = s.replace(ch, " ")
    return " ".join(s.split())


def _contains_any(text: str, keywords: list[str]) -> bool:
    return any(k in text for k in keywords)


_PREF_CANON_MAP: dict[str, dict[str, list[str]]] = {
    "natural_light": {
        "high": ["high", "elevee", "tres", "bright", "lumineux", "ensoleille", "bien expose", "bonne exposition", "plein soleil", "مضوي", "منور"],
        "low": ["low", "faible", "dark", "sombre", "peu lumineux", "na9is", "ناقص"],
    },
    "visual_style": {
        "modern": ["modern", "moderne", "contemporain", "عصري"],
        "traditional": ["traditional", "traditionnel", "classic", "classique", "تقليدي"],
        "minimalist": ["minimalist", "minimaliste", "epure", "simple", "سامپل"],
    },
    "architectural_vibe": {
        "moroccan": ["beldi", "marocain", "moroccan", "بلدي", "مغربي"],
        "classic": ["europeen", "european", "classique", "classic", "haussmann", "أوروبي", "كلاسيكي"],
        "industrial": ["industrial", "industriel", "loft", "atelier", "brique", "صناعي", "لوفت"],
    },
    "floor_material": {
        "wood": ["parquet", "bois", "wood", "خشب", "پاركي"],
        "tile": ["carrelage", "marbre", "marble", "tile", "zellige", "zellij", "zlige", "رخام", "زليج"],
        "carpet": ["moquette", "tapis", "carpet", "rug", "موكيت"],
    },
    "furnishing_status": {
        "furnished": ["meuble", "meublee", "furnished", "amenage", "مفروش"],
        "empty": ["vide", "non meuble", "empty", "unfurnished", "خاوي"],
    },
    "dominant_view": {
        "nature": ["nature", "verdure", "jardin", "parc", "arbres", "greenery", "garden", "الطبيعة", "خضورة"],
        "sea": ["mer", "ocean", "plage", "sea", "water", "marina", "vue mer", "البحر", "الما"],
        "city": ["ville", "urbain", "urban", "city", "centre ville", "downtown", "المدينة"],
        "interior": ["interieur", "sans vue", "cour interieur", "no view", "blocked", "داخل", "الداخل"],
    },
}

_TEXT_KEYWORDS: dict[tuple[str, str], list[str]] = {
    ("natural_light", "high"): ["lumineux", "ensoleille", "bien expose", "bonne exposition", "plein soleil", "bright", "sunny", "light filled", "well lit", "مضوي", "منور"],
    ("natural_light", "low"): ["sombre", "peu lumineux", "faible luminosite", "dark", "low light", "ناقص"],
    ("visual_style", "modern"): ["moderne", "modern", "contemporain", "design moderne", "عصري"],
    ("visual_style", "traditional"): ["traditionnel", "traditional", "classique", "classic", "تقليدي"],
    ("visual_style", "minimalist"): ["minimaliste", "minimalist", "epure", "simple", "سامپل"],
    ("architectural_vibe", "moroccan"): ["beldi", "marocain", "moroccan", "zellige", "platre", "tadelakt", "بلدي", "مغربي"],
    ("architectural_vibe", "classic"): ["europeen", "classique", "classic", "haussmann", "أوروبي", "كلاسيكي"],
    ("architectural_vibe", "industrial"): ["industriel", "industrial", "loft", "brique", "metal", "صناعي", "لوفت"],
    ("floor_material", "wood"): ["parquet", "bois", "wood", "خشب", "پاركي"],
    ("floor_material", "tile"): ["carrelage", "marbre", "marble", "tile", "zellige", "zellij", "زليج", "رخام"],
    ("floor_material", "carpet"): ["moquette", "tapis", "carpet", "rug", "موكيت"],
    ("furnishing_status", "furnished"): ["meuble", "meublee", "furnished", "amenage", "مفروش"],
    ("furnishing_status", "empty"): ["vide", "non meuble", "empty", "unfurnished", "خاوي"],
    ("dominant_view", "nature"): ["vue jardin", "verdure", "nature", "greenery", "park", "parc", "الطبيعة", "خضورة"],
    ("dominant_view", "sea"): ["vue mer", "mer", "ocean", "plage", "sea view", "water", "البحر", "الما"],
    ("dominant_view", "city"): ["vue ville", "urbain", "city view", "centre ville", "downtown", "المدينة"],
    ("dominant_view", "interior"): ["sans vue", "interieur", "cour interieur", "no view", "blocked", "الداخل"],
}

_TEXT_LABELS: dict[tuple[str, str], str] = {
    ("natural_light", "high"): "💡 Lumineux (desc)",
    ("natural_light", "low"): "🌘 Peu lumineux (desc)",
    ("visual_style", "modern"): "✨ Moderne (desc)",
    ("visual_style", "traditional"): "🏛️ Traditionnel (desc)",
    ("visual_style", "minimalist"): "🧼 Minimaliste (desc)",
    ("architectural_vibe", "moroccan"): "🕌 Beldi (desc)",
    ("architectural_vibe", "classic"): "🏛️ Classique (desc)",
    ("architectural_vibe", "industrial"): "🏭 Industriel (desc)",
    ("floor_material", "wood"): "🪵 Parquet (desc)",
    ("floor_material", "tile"): "🧱 Marbre/Carrelage (desc)",
    ("floor_material", "carpet"): "🧶 Moquette (desc)",
    ("furnishing_status", "furnished"): "🛋️ Meublé (desc)",
    ("furnishing_status", "empty"): "📭 Vide (desc)",
    ("dominant_view", "nature"): "🌿 Vue nature (desc)",
    ("dominant_view", "sea"): "🌊 Vue mer (desc)",
    ("dominant_view", "city"): "🏙️ Vue ville (desc)",
    ("dominant_view", "interior"): "🪟 Sans vue (desc)",
}


def _canonical_pref(kind: str, pref_val: Optional[str]) -> Optional[str]:
    if not pref_val:
        return None
    p = _normalize_text(str(pref_val))
    if not p:
        return None
    if any(k in p for k in ["doesnt matter", "peu importe", "doesn't matter", "ma kayhmch", "ma kayhemch", "ma kayhimch", "ماكيهمش"]):
        return None
    if any(k in p for k in ["standard", "std", "عادي"]):
        return None
    for canon, keys in _PREF_CANON_MAP.get(kind, {}).items():
        if _contains_any(p, keys):
            return canon
    return None


def _text_matches(desc: str, prefs: UserPreferences) -> list[str]:
    matches: list[str] = []
    if not desc or str(desc).strip().lower() == "inconnu":
        return matches
    normalized = _normalize_text(desc)
    for kind in ["natural_light", "dominant_view", "furnishing_status", "visual_style", "floor_material", "architectural_vibe"]:
        canon = _canonical_pref(kind, getattr(prefs, kind))
        if not canon:
            continue
        keywords = _TEXT_KEYWORDS.get((kind, canon), [])
        if keywords and _contains_any(normalized, keywords):
            label = _TEXT_LABELS.get((kind, canon))
            if label:
                matches.append(label)
    return matches


def _text_score(row: pd.Series, prefs: UserPreferences) -> float:
    desc = row.get("description")
    if not desc or str(desc).strip().lower() == "inconnu":
        return 0.5
    normalized = _normalize_text(desc)
    total = 0
    matched = 0
    for kind in ["natural_light", "dominant_view", "furnishing_status", "visual_style", "floor_material", "architectural_vibe"]:
        canon = _canonical_pref(kind, getattr(prefs, kind))
        if not canon:
            continue
        total += 1
        keywords = _TEXT_KEYWORDS.get((kind, canon), [])
        if keywords and _contains_any(normalized, keywords):
            matched += 1
    if total == 0:
        return 1.0
    base = 0.4
    return base + (matched / total) * (1.0 - base)


def _has_text_prefs(prefs: UserPreferences) -> bool:
    for kind in ["natural_light", "dominant_view", "furnishing_status", "visual_style", "floor_material", "architectural_vibe"]:
        if _canonical_pref(kind, getattr(prefs, kind)):
            return True
    return False


def _visual_score(row: pd.Series, prefs: UserPreferences) -> float:
    scores = []
    
    # helper for clean lookups
    def check_score(pref_val, row_key):
        if pref_val and row_key in row and pd.notna(row.get(row_key)):
            scores.append(1.0 if str(row[row_key]).lower() == pref_val.lower() else 0.2)
            
    check_score(prefs.visual_style, "visual_style")
    check_score(prefs.visual_condition, "visual_condition")
    check_score(prefs.natural_light, "natural_light")
    check_score(prefs.furnishing_status, "furnishing_status")
    check_score(prefs.floor_material, "floor_material")
    check_score(prefs.dominant_view, "dominant_view")
    check_score(prefs.architectural_vibe, "architectural_vibe")
    check_score(prefs.color_palette, "color_palette")
    
    return float(np.mean(scores)) if scores else 1.0


# ─────────────────────────────────────────────────────────────────────────────
# Score explanation
# ─────────────────────────────────────────────────────────────────────────────

def _build_match_tags(row: pd.Series, prefs: UserPreferences) -> list[str]:
    """Return a short list of human-readable match reasons."""
    tags = []
    if prefs.neighborhoods:
        mapped_prefs = [_map_neighborhood(n) for n in prefs.neighborhoods]
        if row["neighborhood"] in mapped_prefs:
            tags.append(f"📍 {row['neighborhood']}")
        else:
            # Check proximity to requested neighborhoods
            row_coord = NEIGHBORHOOD_COORDS.get(row["neighborhood"])
            if row_coord:
                distances = []
                best_match = None
                for pref_neigh in mapped_prefs:
                    pref_coord = NEIGHBORHOOD_COORDS.get(pref_neigh)
                    if pref_coord:
                        d = _haversine(*row_coord, *pref_coord)
                        if not distances or d < min(distances):
                            distances.append(d)
                            best_match = pref_neigh
                
                if distances:
                    min_dist = min(distances)
                    if min_dist <= 6.0:  # Present if it's within a reasonable radius
                        tags.append(f"📍 à {min_dist:.1f} km de {best_match}")
    if prefs.has_piscine and row["has_piscine"]:
        tags.append("🏊 Piscine")
    if prefs.has_garage and row["has_garage"]:
        tags.append("🚗 Garage")
    if prefs.has_terrasse and row["has_terrasse"]:
        tags.append("🌿 Terrasse")
    if prefs.has_jardin and row["has_jardin"]:
        tags.append("🌳 Jardin")
    if prefs.has_meuble and row["has_meuble"]:
        tags.append("🛋️ Meublé")
    if prefs.has_vue_mer and row["has_vue_mer"]:
        tags.append("🌊 Vue sur mer")
    if prefs.standing and row["standing"] == prefs.standing:
        tags.append(f"⭐ {row['standing']}")
    if prefs.state and row["state"] == prefs.state:
        tags.append(f"🏗️ {row['state']}")

    # Extra visual tags
    if prefs.visual_style and row.get("visual_style") == prefs.visual_style:
        tags.append(f"✨ {row['visual_style']}")
    if prefs.natural_light and row.get("natural_light") == prefs.natural_light:
        tags.append(f"☀️ Lumineux")
    if prefs.architectural_vibe and row.get("architectural_vibe") == prefs.architectural_vibe:
        tags.append(f"🏛️ {row['architectural_vibe']}")
    if prefs.dominant_view and row.get("dominant_view") == prefs.dominant_view:
        tags.append(f"🖼️ Vue: {row['dominant_view'].split('/')[0]}")
    if prefs.floor_material and row.get("floor_material") == prefs.floor_material:
        tags.append(f"🪵 {row['floor_material'].split('/')[0]}")

    # Description-based matches (limit to 2 to keep tags concise)
    text_matches = _text_matches(row.get("description", ""), prefs)
    if text_matches:
        tags.extend(text_matches[:2])

    return tags


# ─────────────────────────────────────────────────────────────────────────────
# Recommender
# ─────────────────────────────────────────────────────────────────────────────

class Recommender:
    """
    Usage
    ─────
        rec = Recommender("listings_clean.csv")
        prefs = UserPreferences(
            transaction_type="Vente",
            city="Casablanca",
            price_max=3_000_000,
            property_type="Appartement",
            bedrooms=3,
            has_piscine=True,
        )
        results = rec.recommend(prefs, top_n=10)
        for r in results:
            print(r["score"], r["neighborhood"], r["price"], r["url"])

        # After user swipes:
        rec.update_from_swipes(liked_urls=[...], disliked_urls=[...])
    """

    def __init__(self, csv_path: str = "../data/listings_with_vision.csv"):
        self.df = self._load(csv_path)
        self.weights = deepcopy(DEFAULT_WEIGHTS)
        print(f"Recommender ready — {len(self.df):,} listings loaded.")

    # ── Data loading ──────────────────────────────────────────────────────────

    @staticmethod
    def _load(path: str) -> pd.DataFrame:
        df = pd.read_csv(path, encoding="utf-8-sig")

        # Deduplicate by URL — keep first occurrence
        before = len(df)
        df = df.drop_duplicates(subset="url", keep="first").reset_index(drop=True)
        if len(df) < before:
            print(f"  (removed {before - len(df):,} duplicate rows)")

        # Coerce numeric columns
        df["price"]     = pd.to_numeric(df["price"],     errors="coerce")
        df["surface"]   = pd.to_numeric(df["surface"],   errors="coerce")
        df["rooms"]     = pd.to_numeric(df["rooms"],     errors="coerce").astype("Int64")
        df["bedrooms"]  = pd.to_numeric(df["bedrooms"],  errors="coerce").astype("Int64")
        df["bathrooms"] = pd.to_numeric(df["bathrooms"], errors="coerce").astype("Int64")
        for col in AMENITY_COLS + ["equipped"]:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

        # Fill string columns
        for col in ["city", "neighborhood", "property_type", "state", "standing",
                    "transaction_type", "description"]:
            df[col] = df[col].fillna("Inconnu").astype(str)

        return df

    # ── Hard filters ──────────────────────────────────────────────────────────

    def _apply_hard_filters(self, prefs: UserPreferences, strict: bool = False) -> pd.DataFrame:
        df = self.df.copy()

        # Transaction type
        if prefs.transaction_type != "Inconnu":
            df = df[df["transaction_type"] == prefs.transaction_type]

        # City
        df = df[df["city"].str.lower() == prefs.city.lower()]

        # Property type (if specified)
        if prefs.property_type:
            if isinstance(prefs.property_type, list):
                df = df[df["property_type"].isin(prefs.property_type)]
            else:
                df = df[df["property_type"] == prefs.property_type]

        # Budget — hard ceiling (soft buffer unless strict)
        if prefs.price_max:
            max_price = prefs.price_max if strict else prefs.price_max * 1.2
            df = df[df["price"] <= max_price]
        if prefs.price_min:
            min_price = prefs.price_min if strict else prefs.price_min * 0.8
            df = df[df["price"] >= min_price]

        # Minimum surface
        if prefs.surface_min:
            min_surface = prefs.surface_min if strict else prefs.surface_min * 0.8
            df = df[df["surface"].isna() | (df["surface"] >= min_surface)]

        # Bedrooms: only enforce as hard constraint in strict mode
        if strict and prefs.bedrooms is not None:
            df = df[df["bedrooms"].isna() | (df["bedrooms"] >= prefs.bedrooms)]

        return df

    # ── Score a single row ───────────────────────────────────────────────────

    def _score_row(self, row: pd.Series, prefs: UserPreferences) -> float:
        w = self.weights.copy()
        
        # If the user strictly asked for a neighborhood, boost the location weight significantly
        # to prioritize actual geographic matches over slightly better-matching distant properties
        if prefs.neighborhoods:
            w["location"] = 0.45  # Increased from 0.15 to make location more important

        axes: dict[str, float] = {}

        # Price
        if not pd.isna(row["price"]) and (prefs.price_min or prefs.price_max):
            axes["price"] = _price_score(row["price"], prefs)

        # Surface
        if not pd.isna(row["surface"]) and (prefs.surface_min or prefs.surface_max):
            axes["surface"] = _surface_score(row["surface"], prefs)

        # Bedrooms
        if not pd.isna(row["bedrooms"]) and prefs.bedrooms is not None:
            axes["bedrooms"] = _int_score(int(row["bedrooms"]), prefs.bedrooms)

        # Location
        axes["location"] = _location_score(row, prefs)

        # Quality (state + standing)
        if prefs.state or prefs.standing:
            axes["quality"] = _quality_score(row, prefs)

        # Visual Preferences
        if any([prefs.visual_style, prefs.visual_condition, prefs.natural_light, 
                prefs.furnishing_status, prefs.floor_material, prefs.dominant_view, 
                prefs.architectural_vibe, prefs.color_palette]):
            axes["visual"] = _visual_score(row, prefs)

        # Description-based matching (text)
        if _has_text_prefs(prefs):
            axes["text"] = _text_score(row, prefs)

        # Amenities
        if any(getattr(prefs, c) is not None for c in AMENITY_COLS):
            axes["amenities"] = _amenity_score(row, prefs)

        if not axes:
            return 0.5  # no scoreable preferences → neutral

        # Weighted average over active axes
        total_w = sum(w.get(ax, 0.05) for ax in axes)
        score = sum(w.get(ax, 0.05) * v for ax, v in axes.items())
        return score / total_w if total_w > 0 else 0.0

    # ── Public API ────────────────────────────────────────────────────────────

    def recommend(
        self,
        prefs: UserPreferences,
        top_n: int = 10,
        min_score: float = 0.0,
        exclude_urls: list[str] = None,
    ) -> list[dict]:
        """
        Return up to top_n listings ranked by match score.

        Each result dict contains all listing fields plus:
          "score"      – float 0-1
          "match_tags" – list of human-readable match reasons
        """
        candidates = self._apply_hard_filters(prefs, strict=True)
        if candidates.empty:
            candidates = self._apply_hard_filters(prefs, strict=False)

        if exclude_urls:
            candidates = candidates[~candidates["url"].isin(exclude_urls)]

        if candidates.empty:
            return []

        candidates = candidates.copy()
        candidates["score"] = candidates.apply(
            lambda row: self._score_row(row, prefs), axis=1
        )
        candidates = candidates[candidates["score"] >= min_score]
        candidates = candidates.sort_values("score", ascending=False).head(top_n)

        results = []
        for _, row in candidates.iterrows():
            d = row.to_dict()
            d["score"] = round(d["score"], 4)
            d["match_tags"] = _build_match_tags(row, prefs)
            results.append(d)

        return results

    def available_neighborhoods(self, city: str = "Casablanca") -> list[str]:
        """Return all distinct neighborhoods for a city, sorted by listing count."""
        sub = self.df[self.df["city"].str.lower() == city.lower()]
        return (
            sub["neighborhood"]
            .value_counts()
            .index.tolist()
        )

    def price_range(self, prefs: UserPreferences) -> dict:
        """Return min/median/max price for the filtered dataset."""
        candidates = self._apply_hard_filters(prefs, strict=True)
        if candidates.empty:
            candidates = self._apply_hard_filters(prefs, strict=False)
        prices = candidates["price"].dropna()
        if prices.empty:
            return {}
        return {
            "min":    int(prices.min()),
            "median": int(prices.median()),
            "max":    int(prices.max()),
            "count":  len(prices),
        }

    # ── Tinder feedback / weight update ──────────────────────────────────────

    def update_from_swipes(
        self,
        liked_urls: list[str],
        disliked_urls: list[str],
        learning_rate: float = 0.1,
    ) -> None:
        """
        Adjust scoring weights based on what the user liked vs disliked.

        Strategy: compare average feature values of liked vs disliked listings.
        Axes where liked listings score higher get their weight boosted;
        axes where disliked listings score higher get their weight reduced.
        """
        liked    = self.df[self.df["url"].isin(liked_urls)]
        disliked = self.df[self.df["url"].isin(disliked_urls)]

        if liked.empty or disliked.empty:
            return

        # Numeric feature comparison
        feature_map = {
            "price":    "price",
            "surface":  "surface",
            "bedrooms": "bedrooms",
        }
        # Amenity density
        liked_amenity_density    = liked[AMENITY_COLS].mean(axis=1).mean()
        disliked_amenity_density = disliked[AMENITY_COLS].mean(axis=1).mean()

        adjustments: dict[str, float] = {}

        for axis, col in feature_map.items():
            l_mean = liked[col].dropna().mean()
            d_mean = disliked[col].dropna().mean()
            if pd.isna(l_mean) or pd.isna(d_mean) or d_mean == 0:
                continue
            # Coefficient of variation between liked and disliked
            diff = abs(l_mean - d_mean) / max(abs(l_mean), abs(d_mean))
            adjustments[axis] = diff   # larger diff → axis matters more

        if liked_amenity_density != disliked_amenity_density:
            adjustments["amenities"] = abs(liked_amenity_density - disliked_amenity_density)

        if not adjustments:
            return

        # Apply adjustments: boost weight of discriminating axes
        for axis, boost in adjustments.items():
            self.weights[axis] = self.weights.get(axis, 0.05) * (1 + learning_rate * boost)

        # Re-normalise so weights sum to 1.0
        total = sum(self.weights.values())
        self.weights = {k: v / total for k, v in self.weights.items()}

        print("Updated weights:", {k: round(v, 3) for k, v in self.weights.items()})


# ─────────────────────────────────────────────────────────────────────────────
# Quick CLI demo
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_price(p) -> str:
    try:
        return f"{int(p):,} MAD"
    except (ValueError, TypeError):
        return str(p)


def demo():
    rec = Recommender()

    print("\n" + "=" * 60)
    print("DEMO 1 — 3-bedroom apartment, budget 2M, pool")
    print("=" * 60)
    prefs = UserPreferences(
        transaction_type="Vente",
        city="Casablanca",
        price_max=2_000_000,
        property_type="Appartement",
        bedrooms=3,
        has_piscine=True,
    )
    results = rec.recommend(prefs, top_n=5)
    print(f"Candidates after hard filter: calling recommend()…")
    for i, r in enumerate(results, 1):
        tags = "  ".join(r["match_tags"]) or "—"
        print(f"  {i}. score={r['score']:.3f}  {_fmt_price(r['price']):<18}"
              f"  {r['property_type']:<13}  {r['neighborhood']:<22}  {tags}")
        print(f"     {r['url']}")

    print("\n" + "=" * 60)
    print("DEMO 2 — Luxury villa, Californie or Ain Diab, > 300m²")
    print("=" * 60)
    prefs2 = UserPreferences(
        transaction_type="Vente",
        city="Casablanca",
        price_min=3_000_000,
        property_type="Villa",
        surface_min=300.0,
        neighborhoods=["Californie", "Ain Diab", "Ain Diab Extension"],
        standing="Haut standing",
        has_piscine=True,
        has_garage=True,
    )
    results2 = rec.recommend(prefs2, top_n=5)
    for i, r in enumerate(results2, 1):
        tags = "  ".join(r["match_tags"]) or "—"
        print(f"  {i}. score={r['score']:.3f}  {_fmt_price(r['price']):<18}"
              f"  {r['surface']} m²  {r['neighborhood']:<22}  {tags}")
        print(f"     {r['url']}")

    print("\n" + "=" * 60)
    print("DEMO 3 — Price range for studios in Bourgogne Ouest")
    print("=" * 60)
    prefs3 = UserPreferences(property_type="Studio", city="Casablanca",
                              neighborhoods=["Bourgogne Ouest"])
    print("  ", rec.price_range(prefs3))


if __name__ == "__main__":
    demo()
