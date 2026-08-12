from fastapi import APIRouter

from .. import config
from ..db import get_connection
from ..schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health():
    if not config.WAREHOUSE_DB_PATH.exists():
        return HealthResponse(
            status="degraded",
            warehouse_found=False,
            warehouse_path=str(config.WAREHOUSE_DB_PATH),
            tables=[],
            llm_configured=bool(config.ANTHROPIC_API_KEY),
        )
    with get_connection() as con:
        tables = [r[0] for r in con.execute("SHOW TABLES").fetchall()]
    return HealthResponse(
        status="ok",
        warehouse_found=True,
        warehouse_path=str(config.WAREHOUSE_DB_PATH),
        tables=sorted(tables),
        llm_configured=bool(config.ANTHROPIC_API_KEY),
    )
