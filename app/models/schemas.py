"""Response shapes for the measurement endpoints."""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class FileMetadata(BaseModel):
    size_bytes: int
    geometry_count: int
    format: str  # "geojson" | "dxf"


class MeasurementResult(BaseModel):
    geometry_type: str
    measurements: dict[str, Any]  # e.g. {"length_m": 12.5} or {"area_m2": 256.75, "perimeter_m": 75.2}
    units: str = "meters"
    detected_crs: str
    conversion_details: Optional[str] = None


class MeasurementResponse(BaseModel):
    measurement_id: str
    filename: Optional[str] = None
    uploaded_at: datetime
    status: str = "completed"
    file_metadata: FileMetadata
    result: MeasurementResult
    errors: list[str] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    detail: str
    errors: list[str] = Field(default_factory=list)
