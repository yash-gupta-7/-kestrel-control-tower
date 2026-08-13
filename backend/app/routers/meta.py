"""
Small reference-data endpoints that don't fit any single metric area.

GET /meta/regions backs the region selector (the "regional-manager view" --
see DECISIONS.md): a plain scoping filter, not authentication. The frontend
fetches the real list from here rather than hardcoding region codes, since
these are actual business dimension values (unlike, say, fiscal-quarter
math, which is pure calendar arithmetic and safe to hardcode).
"""
from pydantic import BaseModel

from fastapi import APIRouter

from ..db import get_connection, run_query

router = APIRouter(prefix="/meta", tags=["meta"])


class RegionOption(BaseModel):
    region_code: str
    region_name: str
    regional_manager: str | None = None


@router.get("/regions", response_model=list[RegionOption])
def regions():
    with get_connection() as con:
        rows = run_query(
            con,
            "SELECT region_code, region_name, regional_manager "
            "FROM dim_region WHERE status = 'ACTIVE' ORDER BY region_name",
        )
    return [RegionOption(**r) for r in rows]
