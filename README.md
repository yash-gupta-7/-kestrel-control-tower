# Kestrel Provisions: Supply Chain Control Tower

A control tower over Kestrel's operational data: service (fill rate, OTIF),
money (freight cost, returns leakage), cold chain (temperature excursions,
near-expiry stock), competitor price position, and a plain-English
"ask anything" query interface.

See [`DECISIONS.md`](DECISIONS.md) for the running log of build decisions,
scope calls, and data-quality findings as the project progresses.

## Prerequisites

- Docker and Docker Compose (Compose v2)
- The assignment pack, unzipped somewhere on disk. This repo does **not**
  contain `data/kestrel_ops.db`, `bazaarpulse_site/`, or `partner_api/` --
  per the assignment brief, that data is referenced by path, not committed.

## Project structure

```
etl/        Scraper, freight-invoice puller, weather puller, warehouse builder
backend/    FastAPI analytics service
frontend/   React/Vite dashboard
docker/     Supporting Dockerfiles
```

Full setup/run instructions will land here as each piece is built.
