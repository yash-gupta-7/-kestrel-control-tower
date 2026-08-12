"""
Backend configuration. Reuses etl/config.py for anything about where data
lives (warehouse path, API keys) so there is exactly one place that knows
about paths and secrets, whether a script is run from etl/ or served from
backend/. Adds only what's specific to serving the API.
"""
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(APP_ROOT))

from etl import config as etl_config  # noqa: E402

WAREHOUSE_DB_PATH = etl_config.WAREHOUSE_DB_PATH
ANTHROPIC_API_KEY = etl_config.ANTHROPIC_API_KEY
ANTHROPIC_MODEL = etl_config.ANTHROPIC_MODEL

CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "http://localhost:5173,http://localhost:3000").split(",")

# Hard cap on any query result returned over the API, fast-path or LLM.
# Dashboards paginate; nothing here should ever stream the full fact table.
MAX_ROWS_RETURNED = int(os.environ.get("MAX_ROWS_RETURNED", "500"))
