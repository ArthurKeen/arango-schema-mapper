from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


EntityMappingStyle = Literal["COLLECTION", "LABEL"]
RelationshipMappingStyle = Literal["DEDICATED_COLLECTION", "GENERIC_WITH_TYPE"]


class AnalysisMetadata(BaseModel):
    confidence: float = Field(ge=0.0, le=1.0)
    timestamp: str
    analyzed_collection_counts: dict[str, int]
    detected_patterns: list[str]
    warnings: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    review_required: bool = False
    # Optional tool/agent observability fields (v0.1+). These are safe because metadata
    # is allowed to have additional properties by the JSON Schema validator.
    provider: str | None = None
    model: str | None = None
    repair_attempts: int = 0
    used_baseline: bool = False


class AnalysisResult(BaseModel):
    conceptual_schema: dict[str, Any]
    physical_mapping: dict[str, Any]
    metadata: AnalysisMetadata


def now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

