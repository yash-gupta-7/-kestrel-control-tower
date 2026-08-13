"""
Central configuration for all ETL jobs and the backend.

Everything is env-var driven so the same code runs the same way whether it's
invoked from a shell, from Docker Compose, or from a test. No secrets or
absolute paths are hardcoded.
"""
import os
from pathlib import Path

# --- Source data (provided by the assignment pack, never committed) -------
# Path to the Kestrel operational SQLite DB. In Docker this is mounted
# read-only at /data/kestrel_ops.db; locally, point it at wherever the
# assignment pack was unzipped.
KESTREL_DB_PATH = os.environ.get(
    "KESTREL_DB_PATH", "/data/kestrel_ops.db"
)

# --- External surfaces shipped with the assignment -------------------------
BAZAARPULSE_BASE_URL = os.environ.get("BAZAARPULSE_BASE_URL", "http://localhost:8080")
PARTNER_API_BASE_URL = os.environ.get("PARTNER_API_BASE_URL", "http://localhost:8088")
PARTNER_API_KEY = os.environ.get("PARTNER_API_KEY", "kp_live_7f3a9c21")

# --- Public enrichment (optional, non-blocking) -----------------------------
OPEN_METEO_BASE_URL = os.environ.get(
    "OPEN_METEO_BASE_URL", "https://archive-api.open-meteo.com/v1/archive"
)
WEATHER_START_DATE = os.environ.get("WEATHER_START_DATE", "2025-01-01")
WEATHER_END_DATE = os.environ.get("WEATHER_END_DATE", "2026-06-30")

# --- LLM (optional; ask-anything degrades gracefully without it) -----------
# GROQ_API_KEY is the active free-form Ask Anything path (see
# backend/app/routers/ask.py). ANTHROPIC_API_KEY/ANTHROPIC_MODEL are kept for
# backward compatibility with earlier docs/config but are not called by any
# code path today.
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

# --- Local storage -----------------------------------------------------------
APP_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = Path(os.environ.get("CACHE_DIR", APP_ROOT / "cache"))
BAZAARPULSE_CACHE = CACHE_DIR / "bazaarpulse"
FREIGHT_CACHE = CACHE_DIR / "freight"
WEATHER_CACHE = CACHE_DIR / "weather"

WAREHOUSE_DB_PATH = Path(
    os.environ.get("WAREHOUSE_DB_PATH", APP_ROOT / "warehouse" / "warehouse.duckdb")
)

OPERATING_TIMEZONE = "Asia/Kolkata"

# The 8 warehouse cities, with approximate coordinates for weather enrichment.
# The DB does not carry warehouse lat/long, so these are looked up once here
# rather than guessed downstream. Source: public knowledge of city centres.
WAREHOUSE_CITY_COORDS = {
    "Mumbai": (19.076, 72.877),
    "Pune": (18.520, 73.856),
    "Bengaluru": (12.972, 77.594),
    "Chennai": (13.083, 80.270),
    "Delhi": (28.704, 77.102),
    "Kolkata": (22.573, 88.364),
    "Hyderabad": (17.385, 78.487),
    "Nagpur": (21.146, 79.089),
}

# City name canonicalisation for `outlets.city` (KP-2288: free text, several
# spellings in use). Extend this map as new variants are discovered rather
# than normalising fuzzily -- explicit is safer for a client-facing number.
CITY_CANONICAL_MAP = {
    "Bangalore": "Bengaluru",
    "New Delhi": "Delhi",
}

# Kestrel's own brand plus the other brands it distributes, used to key
# BazaarPulse listings back to `products.brand` without fragile full-title
# fuzzy matching (see DECISIONS.md).
KNOWN_BRANDS = ["Kestrel", "Bluepeak", "Hillfare", "Coastline", "Amrit", "Marwar"]

# Outlet name patterns that mark a record as test/migration debris (KP-2377).
# There is no status flag for this -- it is a name-pattern heuristic only,
# and is documented as such.
TEST_OUTLET_PATTERNS = ["%TEST%", "DO NOT USE%", "ZZ%"]
