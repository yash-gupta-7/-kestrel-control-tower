from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import config
from .routers import cold_chain, health, money, price_position, service

app = FastAPI(
    title="Kestrel Control Tower API",
    description=(
        "Read-only analytics over a cleaned DuckDB warehouse "
        "(see etl/build_warehouse.py). Never touches raw operational tables."
    ),
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(service.router)
app.include_router(money.router)
app.include_router(cold_chain.router)
app.include_router(price_position.router)
