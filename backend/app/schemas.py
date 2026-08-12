"""Shared Pydantic response models."""
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class MetricRow(BaseModel):
    """Generic single row of a grouped metric result. `dimension` is the
    group value (an outlet code, region name, warehouse code, etc.);
    everything else is metric-specific and carried in `metrics`."""
    dimension: str
    dimension_label: Optional[str] = None
    metrics: dict[str, Any]


class MetricResponse(BaseModel):
    metric: str
    group_by: str
    period_label: str
    filters_applied: dict[str, Any] = Field(default_factory=dict)
    rows: list[MetricRow]
    caveats: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str
    warehouse_found: bool
    warehouse_path: str
    tables: list[str] = Field(default_factory=list)
    llm_configured: bool
