from fastapi import APIRouter

from .. import config
from ..db import get_connection, warehouse_validation_info
from ..schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health():
    # Deliberately does NOT go through db.get_connection() -- that helper
    # raises a 503 for a missing/unvalidated warehouse by design (see
    # db.py), which is correct for every data-serving endpoint but wrong
    # for /health specifically: /health's whole job is to report the real
    # state in its body (status="ok"/"degraded"), the same way it already
    # did for "warehouse file missing" before this pass. The Docker
    # healthcheck (docker-compose.yml) reads this body's `status` field,
    # not just HTTP success, so a "degraded" body still fails the
    # container healthcheck -- see the healthcheck command there.
    validation = warehouse_validation_info()
    if not config.WAREHOUSE_DB_PATH.exists():
        return HealthResponse(
            status="degraded",
            warehouse_found=False,
            warehouse_path=str(config.WAREHOUSE_DB_PATH),
            tables=[],
            llm_configured=bool(config.GROQ_API_KEY),
            **validation_fields(validation),
        )
    if not validation["validated"]:
        return HealthResponse(
            status="degraded",
            warehouse_found=True,
            warehouse_path=str(config.WAREHOUSE_DB_PATH),
            tables=[],
            llm_configured=bool(config.GROQ_API_KEY),
            **validation_fields(validation),
        )
    with get_connection() as con:
        tables = [r[0] for r in con.execute("SHOW TABLES").fetchall()]
    return HealthResponse(
        status="ok",
        warehouse_found=True,
        warehouse_path=str(config.WAREHOUSE_DB_PATH),
        tables=sorted(tables),
        llm_configured=bool(config.GROQ_API_KEY),
        **validation_fields(validation),
    )


def validation_fields(validation: dict) -> dict:
    return {
        "warehouse_validated": validation["validated"],
        "warehouse_validated_at": validation["validated_at"],
        "warehouse_validation_detail": validation["validation_detail"],
    }
