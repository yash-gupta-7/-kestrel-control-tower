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
GROQ_API_KEY = etl_config.GROQ_API_KEY
GROQ_MODEL = etl_config.GROQ_MODEL

CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "http://localhost:5173,http://localhost:3000").split(",")

# Hard cap on any query result returned over the API, fast-path or LLM.
# Dashboards paginate; nothing here should ever stream the full fact table.
MAX_ROWS_RETURNED = int(os.environ.get("MAX_ROWS_RETURNED", "500"))

# Wall-clock cap on executing a single piece of SQL in the Ask Anything LLM
# path (backend/app/routers/ask.py) -- the fast paths are hand-written,
# known-cheap queries and are not subject to this; this exists specifically
# because Groq-generated SQL is unpredictable (an unindexed join across the
# largest fact tables, an accidental cross join, etc.). Enforced via
# DuckDB's Connection.interrupt() from a watchdog thread -- see ask.py.
ASK_QUERY_TIMEOUT_SECONDS = float(os.environ.get("ASK_QUERY_TIMEOUT_SECONDS", "10"))

# Network-level timeout and retry budget for the Groq API call itself
# (distinct from ASK_QUERY_TIMEOUT_SECONDS, which bounds our own DuckDB
# query). The groq SDK is httpx-based and retries transient failures
# (connection errors, timeouts, 429/5xx) internally up to `max_retries`
# times; it does not retry 4xx errors or anything that happened after a
# response was already received (a malformed JSON body, a guard rejection)
# -- those are handled entirely in our own code in ask.py, never retried.
GROQ_TIMEOUT_SECONDS = float(os.environ.get("GROQ_TIMEOUT_SECONDS", "15"))
GROQ_MAX_RETRIES = int(os.environ.get("GROQ_MAX_RETRIES", "1"))
