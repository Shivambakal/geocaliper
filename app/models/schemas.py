"""Response shapes for the measurement endpoints."""

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class FileMetadata(BaseModel):
    size_bytes: int
    geometry_count: int
    format: str  # "geojson" | "dxf" | "kml" | "kmz" | "gpx"


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


class JobStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class JobResponse(BaseModel):
    job_id: str
    filename: Optional[str] = None
    status: JobStatus
    created_at: datetime
    # present once the job finishes
    measurement_id: Optional[str] = None
    result: Optional[MeasurementResponse] = None
    error: Optional[str] = None
    hint: Optional[str] = None


class BatchFileResult(BaseModel):
    filename: str
    ok: bool
    measurement: Optional[MeasurementResponse] = None
    error: Optional[str] = None


class BatchSummary(BaseModel):
    file_count: int
    succeeded: int
    failed: int
    total_area_m2: float = 0.0
    total_length_m: float = 0.0
    total_points: int = 0


class BatchResponse(BaseModel):
    batch_id: str
    summary: BatchSummary
    files: list[BatchFileResult]
