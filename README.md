# Kestrel Provisions: Supply Chain Control Tower

A working control tower over Kestrel's operational data: service (fill rate,
OTIF), money (freight cost, returns leakage), cold chain (temperature
excursions, near-expiry stock), competitor price position, and a
plain-English "ask anything" query box.

Read [`DECISIONS.md`](DECISIONS.md) for what was built, what was
deliberately cut, and the judgement calls made along the way — it's a
one-page summary and worth reading before the code.

## Prerequisites

- Docker Desktop (or another Docker Compose v2 install — `docker compose`,
  not the old standalone `docker-compose`), with BuildKit enabled (the
  default on any current Docker Desktop).
- The assignment pack, unzipped somewhere on disk. This repo does **not**
  contain `data/kestrel_ops.db`, `data/csv/`, `bazaarpulse_site/`, or
  `partner_api/` — none of that is committed; see "How the assignment
  pack is used" below for exactly how each piece reaches the containers.

## Quick start

```bash
cp .env.example .env
# edit .env: set ASSIGNMENT_PACK_DIR to wherever you unzipped the pack
# (the folder that directly contains data/, bazaarpulse_site/, partner_api/)

docker compose up --build
```

Open **http://localhost:3000**.

That's the whole setup. One command starts five services in the right
order: the two mock external services from the assignment pack
(BazaarPulse static site, partner freight API), a one-shot `etl` job that
scrapes/pulls/cleans everything into a DuckDB warehouse, then the backend
API and the frontend, each waiting on the previous step to actually
succeed (not just start) before coming up. First run takes a few minutes —
the ETL step does a full scrape and a full ~41,500-row freight invoice
walk with real retry/backoff against injected rate limiting. Subsequent
`docker compose up` runs skip work that's already cached in the
`warehouse_data`/`cache_data` volumes; use `docker compose up --build
--force-recreate` if you want a fully fresh pull.

If `ASSIGNMENT_PACK_DIR` is missing or blank in `.env`, `docker compose`
refuses to start and tells you exactly which variable to set, rather than
silently falling back to a bogus path — see "How the assignment pack is
used" below.

To run it with the AI-powered free-form question path enabled, set
`GROQ_API_KEY` in `.env` before starting (get a key at
[console.groq.com/keys](https://console.groq.com/keys) — never commit a real
key; `.env` is git-ignored for exactly this reason). Everything else —
including all 8 of the illustrative questions from the assignment brief —
works identically with or without a key.

## How the assignment pack is used

`ASSIGNMENT_PACK_DIR` (set once, in `.env`) points at your unzipped copy
of the assignment pack. Nothing under it is ever committed to this repo.
It's consumed two different ways, deliberately:

- **`data/kestrel_ops.db` (95MB) and `data/csv/` (82MB)** are read live by
  the `etl` container through a **runtime bind mount** (`volumes:` in
  `docker-compose.yml`). They're too large to reasonably bake into an
  image, and the ETL step needs to read them fresh each run.
- **`bazaarpulse_site/` and `partner_api/`** — the two small, static mock
  services supplied with the assignment — are baked into their images at
  **build time**, via Compose's `additional_contexts` (`docker compose
  build`, not a `volumes:` mount). They're copied into the image once,
  during `docker compose up --build`, and the containers are then
  self-contained; nothing is read from your disk at runtime for these two.

That build-time split exists for a concrete reason, not just tidiness: a
`volumes:` bind mount to an arbitrary host path has to be allow-listed in
Docker Desktop's File Sharing settings, which is exactly what broke on
first real Docker Desktop testing (`the path /partner_api is not shared
from the host` — see DECISIONS.md for the full root cause). A Docker
build context is a one-time file read by the Docker CLI and isn't subject
to that restriction at all, so moving the two static fixtures to
build-time `COPY` removes the File Sharing dependency for them entirely —
no manual Docker Desktop configuration required, on any OS. The one
remaining runtime mount (`data/`) works out of the box on macOS because
Docker Desktop shares the whole `/Users` tree by default; if your
assignment pack lives outside your home directory, you may need to add it
under Docker Desktop → Settings → Resources → File Sharing.

## What you'll see

- **Overview** — answers "where are we losing service" and "where are we
  losing money" immediately, plus the two systemic findings (see below).
- **Service** — fill rate (eaches only — see DECISIONS.md) and OTIF, by
  outlet/region/warehouse/route.
- **Money** — freight cost per delivered case, returns as leakage.
- **Ask Anything** — type a question, or click one of the 8 suggested
  ones from the assignment brief. Those 8 are answered deterministically by
  hand-written, tested queries, with zero external dependencies. Anything
  else is answered by asking an LLM (Groq) to write a SQL query — the
  generated SQL is displayed alongside the answer for auditability, and is
  validated read-only against the same approved analytical views before it
  ever runs (see "SQL guard" in DECISIONS.md). Without `GROQ_API_KEY` set,
  free-form questions get a clear "AI unavailable" response instead of
  failing. Ask Anything applies rule-based privacy filtering before
  sending a question to the LLM. Personal-data fields are excluded from
  the LLM schema and blocked queries are rejected before execution (see
  "Personal data protection" in DECISIONS.md).
- **Cold Chain** — temperature excursions, near-expiry stock, cold-chain
  returns. Deliberately lighter than Service/Money/Ask (see priority
  allocation in DECISIONS.md).
- **Price Position** — Kestrel MRP vs. observed competitor price,
  confidently-matched SKUs only; everything else is shown as "no
  confident match," not guessed.

Two findings are surfaced deliberately as network-wide patterns rather
than routine metrics: **all 140 routes** exceed a 2-hour-late threshold on
more than 1 in 10 deliveries, and **all 724 outlets** have at some point
ordered a discontinued SKU. Both are real, verified against the data —
see DECISIONS.md for how.

## Running without Docker

Each piece is a plain Python/Node process; useful for development or if
Docker isn't available.

```bash
# 1. Mock services (from the assignment pack, two separate terminals)
cd /path/to/assignment_pack/bazaarpulse_site && python3 -m http.server 8080
cd /path/to/assignment_pack/partner_api && pip install fastapi uvicorn && python3 server.py

# 2. ETL (from this repo root)
pip install -r etl/requirements.txt
export KESTREL_DB_PATH=/path/to/assignment_pack/data/kestrel_ops.db
python3 etl/scrape_bazaarpulse.py
python3 etl/pull_freight_invoices.py
python3 etl/pull_weather.py
python3 etl/build_warehouse.py

# 3. Backend
pip install -r backend/requirements.txt
export WAREHOUSE_DB_PATH=$(pwd)/warehouse/warehouse.duckdb
uvicorn backend.app.main:app --port 8000

# 4. Frontend (separate terminal)
cd frontend && npm install && npm run dev
```

Open http://localhost:5173 (Vite's dev server port; the Docker setup
serves the production build on :3000 instead).

## Configuration reference

| Variable | Where | Default | Purpose |
|---|---|---|---|
| `ASSIGNMENT_PACK_DIR` | `.env`, Docker only | — (required, build fails without it) | Path to the unzipped assignment pack. Used as a runtime mount source for `data/`, and as a build-time source for `bazaarpulse_site/`/`partner_api/` |
| `GROQ_API_KEY` | `.env` / shell env | empty | Enables the free-form Ask Anything path (LLM-generated, SQL-guard-validated queries). Optional. |
| `GROQ_MODEL` | `.env` / shell env | `llama-3.3-70b-versatile` | Which Groq model answers free-form questions. |
| `ANTHROPIC_API_KEY` | `.env` / shell env | empty | Legacy/unused — kept for compatibility with earlier docs; no code path calls it. |
| `KESTREL_DB_PATH` | ETL env | `/data/kestrel_ops.db` | Path to the SQLite operational DB |
| `WAREHOUSE_DB_PATH` | ETL + backend env | `warehouse/warehouse.duckdb` | The cleaned DuckDB store both read/write |
| `VITE_API_BASE_URL` | frontend build arg | `http://localhost:8000` | Where the browser sends API calls |

## Project structure

```
etl/        Scraper, freight-invoice puller, weather puller, warehouse builder
backend/    FastAPI app: routers/ (health, service, money, cold_chain, price_position, ask)
frontend/   React/Vite dashboard
docker/     Dockerfiles for the bazaarpulse and partner-api mock-service wrappers
            (both COPY their fixture in from ASSIGNMENT_PACK_DIR at build time)
```

## Troubleshooting

**`docker compose` refuses to start, complaining about `ASSIGNMENT_PACK_DIR`.**
That's intentional — it means the variable is unset or blank in `.env`.
`cp .env.example .env` and set it to your unzipped assignment pack's
absolute path.

**`the path ... is not shared from the host and is not known to Docker`.**
This means Docker Desktop's File Sharing doesn't include wherever
`ASSIGNMENT_PACK_DIR` points. It can only happen for the `data/` mount now
(bazaarpulse_site/partner_api are baked in at build time, not mounted) —
add the assignment pack's parent directory under Docker Desktop →
Settings → Resources → File Sharing, or move the pack under your home
directory, which is shared by default.

**`etl` container fails immediately.** Check `ASSIGNMENT_PACK_DIR` in
`.env` is an absolute path and actually contains `data/kestrel_ops.db`.

**Backend never becomes healthy.** `docker compose logs etl` — the ETL
step must exit 0 before the backend starts; a partial/failed ETL run is
the most common cause.

**Frontend loads but shows "Could not reach the API."** The browser
calls the backend directly at `http://localhost:8000` (baked in at
frontend build time) — confirm nothing else on your machine is using
port 8000, and that `docker compose ps` shows `backend` as healthy.

**Freight pull looks stuck.** It isn't — the mock API deliberately
injects slow first-page latency, ~1-in-9 rate limiting, and ~1-in-25
outages on every request; the puller retries and backs off correctly.
A full run finishes in roughly a minute.

**Changed something in `bazaarpulse_site/` or `partner_api/` and don't see
it reflected.** Both are now baked into their images at build time, not
mounted live — run `docker compose up --build` (not plain `up`) to pick
up changes.
