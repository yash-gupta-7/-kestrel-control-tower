# Kestrel Provisions: Supply Chain Control Tower

A control tower over Kestrel's operational data: service (fill rate, OTIF),
money (freight cost, returns leakage), cold chain (temperature excursions,
near-expiry stock), competitor price position, and a plain-English
"ask anything" query interface.

See [`DECISIONS.md`](DECISIONS.md) for the running log of build decisions,
scope calls, and data-quality findings.

## Prerequisites

- Docker and Docker Compose (Compose v2, i.e. `docker compose`, not the
  old standalone `docker-compose`)
- The assignment pack, unzipped somewhere on disk. This repo does **not**
  contain `data/kestrel_ops.db`, `bazaarpulse_site/`, or `partner_api/` --
  per the assignment brief, that data is referenced by path, not committed.

## Quick start

\`\`\`bash
cp .env.example .env
# edit .env: set ASSIGNMENT_PACK_DIR to wherever you unzipped the pack
# (the folder that directly contains data/, bazaarpulse_site/, partner_api/)

docker compose up --build
\`\`\`

Open **http://localhost:3000**.

One command starts five services in order: the two mock external services
from the assignment pack (BazaarPulse static site, partner freight API), a
one-shot \`etl\` job that scrapes/pulls/cleans everything into a DuckDB
warehouse, then the backend API and the frontend, each waiting on the
previous step to actually succeed (not just start) before coming up.

## Project structure

\`\`\`
etl/        Scraper, freight-invoice puller, weather puller, warehouse builder
backend/    FastAPI app: routers/ (health, service, money, cold_chain, price_position, ask)
frontend/   React/Vite dashboard
docker/     Dockerfile for the mock partner-API wrapper
\`\`\`

Full run-without-Docker instructions, configuration reference, and
troubleshooting will land here once the deployment pass is complete.
