# Kestrel Provisions: Supply Chain Control Tower

A working control tower over Kestrel's operational data: service (fill rate,
OTIF), money (freight cost, returns leakage), cold chain (temperature
excursions, near-expiry stock), competitor price position, and a
plain-English "ask anything" query box.

Read [`DECISIONS.md`](DECISIONS.md) for what was built, what was
deliberately cut, and the judgement calls made along the way — it's a
one-page summary and worth reading before the code.

## Prerequisites

- Docker and Docker Compose (Compose v2, i.e. `docker compose`, not the old
  standalone `docker-compose`)
- The assignment pack, unzipped somewhere on disk. This repo does **not**
  contain `data/kestrel_ops.db`, `bazaarpulse_site/`, or `partner_api/` —
  per the assignment brief, that data is referenced by path, not committed.

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

To run it with the AI-powered free-form question path enabled, set
`ANTHROPIC_API_KEY` in `.env` before starting. Everything else — including
all 8 of the illustrative questions from the assignment brief — works
identically with or without a key.

## What you'll see

- **Overview** — answers "where are we losing service" and "where are we
  losing money" immediately, plus the two systemic findings (see below).
- **Service** — fill rate (eaches only — see DECISIONS.md) and OTIF, by
  outlet/region/warehouse/route.
- **Money** — freight cost per delivered case, returns as leakage.
- **Ask Anything** — type a question, or click one of the 8 suggested
  ones from the assignment brief.
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
| `ASSIGNMENT_PACK_DIR` | `.env`, Docker only | — (required) | Path to the unzipped assignment pack |
| `ANTHROPIC_API_KEY` | `.env` / shell env | empty | Enables the free-form Ask Anything path. Optional. |
| `KESTREL_DB_PATH` | ETL env | `/data/kestrel_ops.db` | Path to the SQLite operational DB |
| `WAREHOUSE_DB_PATH` | ETL + backend env | `warehouse/warehouse.duckdb` | The cleaned DuckDB store both read/write |
| `VITE_API_BASE_URL` | frontend build arg | `http://localhost:8000` | Where the browser sends API calls |

## Project structure

```
etl/        Scraper, freight-invoice puller, weather puller, warehouse builder
backend/    FastAPI app: routers/ (health, service, money, cold_chain, price_position, ask)
frontend/   React/Vite dashboard
docker/     Dockerfiles for the two mock-service wrappers
```

## Troubleshooting

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
