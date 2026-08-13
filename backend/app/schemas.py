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


class AskRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=500)
    region_code: Optional[str] = Field(
        None, max_length=10,
        description="Regional-manager scope selected in the UI, e.g. WST. Applied where the "
                    "matched question's underlying data supports a region filter; ignored otherwise.",
    )


class AskResult(BaseModel):
    question: str
    mode: Literal["fast_path", "llm", "llm_not_implemented", "unavailable"]
    answer: str
    data: Optional[list[dict[str, Any]]] = None
    sql: Optional[str] = None
    source: Optional[str] = None
    caveats: list[str] = Field(default_factory=list)
    matched_question_id: Optional[str] = None


class SupportedQuestion(BaseModel):
    id: str
    example: str
    description: str


class HealthResponse(BaseModel):
    status: str
    warehouse_found: bool
    warehouse_path: str
    tables: list[str] = Field(default_factory=list)
    llm_configured: bool
