"""
Recommender Database Integration
"""
import pandas as pd
from typing import Optional
from recommender import Recommender, UserPreferences, AMENITY_COLS, DEFAULT_WEIGHTS
from database import SessionLocal, engine

class RecommenderDB(Recommender):
    def __init__(self):
        # Overwrite __init__ to read from DB instead of CSV
        try:
            self.df = self._load_from_db()
            self.weights = DEFAULT_WEIGHTS.copy()
            print(f"DB Recommender ready — {len(self.df):,} listings loaded from Database.")
        except Exception as e:
            print(f"Warning: Could not load from database: {e}")
            print("Creating empty recommender")
            self.df = pd.DataFrame()
            self.weights = DEFAULT_WEIGHTS.copy()
            print("DB Recommender ready — 0 listings loaded (empty database).")

    @staticmethod
    def _load_from_db() -> pd.DataFrame:
        try:
            query = "SELECT * FROM listings"
            df = pd.read_sql(query, engine)
            
            if df.empty:
                print("Database table 'listings' is empty")
                return df
            
            # Preprocess DataFrame similarly to how the CSV was handled
            df["price"]     = pd.to_numeric(df["price"],     errors="coerce")
            df["surface"]   = pd.to_numeric(df["surface"],   errors="coerce")
            df["rooms"]     = pd.to_numeric(df["rooms"],     errors="coerce").astype("Int64")
            df["bedrooms"]  = pd.to_numeric(df["bedrooms"],  errors="coerce").astype("Int64")
            df["bathrooms"] = pd.to_numeric(df["bathrooms"], errors="coerce").astype("Int64")
            for col in AMENITY_COLS + ["equipped"]:  # Match Recommender._load() behavior
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

            for col in ["city", "neighborhood", "property_type", "state", "standing", "transaction_type",
                        "description", "phone_number", "map_link", "address", "images"]:
                if col in df.columns:
                    df[col] = df[col].fillna("").astype(str)

            return df
        except Exception as e:
            print(f"Error loading from database: {e}")
            # Return empty DataFrame with expected columns
            return pd.DataFrame()

