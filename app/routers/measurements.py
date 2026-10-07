"""Measurement endpoints: upload, batch, export, raw GeoJSON, fetch."""

import csv
import io
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from app import config, store
from app.models.schemas import (
    BatchFileResult,
    BatchResponse,
    BatchSummary,
    ErrorResponse,
    FileMetadata,
    JobResponse,
    JobStatus,
    MeasurementResponse,
    MeasurementResult,
)
from app.services.measure import measure_geojson_geom, measure_planar_m
from app.services.parsers import ParseError, parse_upload

router = APIRouter(prefix="/api/v1", tags=["measurements"])

SUPPORTED_EXTENSIONS = {".geojson", ".json", ".dxf", ".kml", ".kmz", ".gpx"}


def _ext_of(filename: str) -> str:
    return "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


async def _read_upload(upload: UploadFile) -> bytes:
    """Read an upload in chunks so a big file doesn't blow up memory at once."""
    chunks = []
    total = 0
    while True:
        chunk = await upload.read(config.CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > config.MAX_FILE_SIZE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"file too large (limit {config.MAX_FILE_SIZE_BYTES // (1024*1024)} MB)",
            )
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise HTTPException(status_code=422, detail="uploaded file is empty")
    return data


def _build_result(geoms, crs_label: str, conversion: str, planar_scale) -> MeasurementResult:
    measurements: dict = {}
    types = set()
    for geom in geoms:
        if planar_scale is None:
            gtype, m = measure_geojson_geom(geom)
        else:
            gtype, m = measure_planar_m(geom, planar_scale)
        types.add(gtype)
        for k, v in m.items():
            if isinstance(v, (int, float)) and k != "part_count":
                measurements[k] = round(measurements.get(k, 0) + v, 3)
            else:
                measurements[k] = v
    geometry_type = types.pop() if len(types) == 1 else "Mixed"
    return MeasurementResult(
        geometry_type=geometry_type,
        measurements=measurements,
        detected_crs=crs_label,
        conversion_details=conversion,
    )


def _measure_bytes(filename: str, data: bytes) -> MeasurementResponse:
    """Parse + measure one file. Raises ParseError / HTTPException on bad input."""
    ext = _ext_of(filename)
    if ext not in SUPPORTED_EXTENSIONS:
        raise ParseError(
            f"unsupported file type '{ext or '(none)'}' — "
            "use .geojson, .json, .dxf, .kml, .kmz or .gpx"
        )
    geoms, fmt, crs_label, conversion, planar_scale, warnings = parse_upload(ext, data)
    result = _build_result(geoms, crs_label, conversion, planar_scale)

    measurement_id = str(uuid.uuid4())
    response = MeasurementResponse(
        measurement_id=measurement_id,
        filename=filename,
        uploaded_at=datetime.now(timezone.utc),
        file_metadata=FileMetadata(
            size_bytes=len(data),
            geometry_count=len(geoms),
            format=fmt,
        ),
        result=result,
        errors=warnings,
    )
    store.save(measurement_id, response)
    return response


@router.post(
    "/measurements",
    responses={413: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
    summary="Upload a geospatial file and get measurements",
)
async def upload_measurement(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    sync: bool = Query(False, description="force synchronous processing for large files"),
):
    filename = file.filename or "upload"
    data = await _read_upload(file)

    # Big files are better as jobs — return 202 unless the caller insists.
    if len(data) > config.ASYNC_SUGGEST_BYTES and not sync:
        from app.routers.jobs import run_job

        job_id = str(uuid.uuid4())
        hint = (
            f"file is {len(data) / (1024*1024):.1f} MB; processing asynchronously. "
            f"Poll GET /api/v1/jobs/{job_id}, or re-upload with ?sync=true to wait."
        )
        job = JobResponse(
            job_id=job_id,
            filename=filename,
            status=JobStatus.QUEUED,
            created_at=datetime.now(timezone.utc),
            hint=hint,
        )
        store.save_job(job)
        background_tasks.add_task(run_job, job_id, filename, data)
        return JSONResponse(
            status_code=202,
            content={
                "job_id": job_id,
                "filename": filename,
                "status": JobStatus.QUEUED,
                "hint": hint,
                "poll": f"/api/v1/jobs/{job_id}",
            },
        )

    try:
        return _measure_bytes(filename, data)
    except ParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post(
    "/measurements/batch",
    response_model=BatchResponse,
    responses={413: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
    summary="Upload up to 10 files, get per-file results plus a summary",
)
async def batch_measurement(files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=422, detail="no files uploaded")
    if len(files) > config.MAX_BATCH_FILES:
        raise HTTPException(
            status_code=422,
            detail=f"too many files ({len(files)}), max is {config.MAX_BATCH_FILES}",
        )

    results: list[BatchFileResult] = []
    total_area = total_length = 0.0
    total_points = 0
    succeeded = 0

    for upload in files:
        filename = upload.filename or "upload"
        try:
            data = await _read_upload(upload)
        except HTTPException as exc:
            results.append(BatchFileResult(filename=filename, ok=False, error=exc.detail))
            continue
        try:
            measurement = _measure_bytes(filename, data)
        except (ParseError, HTTPException) as exc:
            detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
            results.append(BatchFileResult(filename=filename, ok=False, error=detail))
            continue
        results.append(BatchFileResult(filename=filename, ok=True, measurement=measurement))
        succeeded += 1
        m = measurement.result.measurements
        total_area += m.get("area_m2", 0) or 0
        total_length += m.get("length_m", 0) or 0
        total_points += m.get("count", 0) or 0

    return BatchResponse(
        batch_id=str(uuid.uuid4()),
        summary=BatchSummary(
            file_count=len(files),
            succeeded=succeeded,
            failed=len(files) - succeeded,
            total_area_m2=round(total_area, 3),
            total_length_m=round(total_length, 3),
            total_points=total_points,
        ),
        files=results,
    )


@router.post(
    "/measurements/geojson",
    response_model=MeasurementResponse,
    responses={422: {"model": ErrorResponse}},
    summary="Send raw GeoJSON in the request body",
)
async def raw_geojson_measurement(request: Request):
    data = await request.body()
    if not data.strip():
        raise HTTPException(status_code=422, detail="empty request body, expected GeoJSON")
    if len(data) > config.MAX_FILE_SIZE_BYTES:
        raise HTTPException(status_code=413, detail="body too large")

    try:
        return _measure_bytes("raw.geojson", data)
    except ParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get(
    "/measurements/{measurement_id}",
    response_model=MeasurementResponse,
    responses={404: {"model": ErrorResponse}},
    summary="Fetch a previously computed measurement",
)
def get_measurement(measurement_id: str):
    response = store.get(measurement_id)
    if response is None:
        raise HTTPException(status_code=404, detail=f"no measurement found for id '{measurement_id}'")
    return response


@router.get(
    "/measurements/{measurement_id}/export",
    responses={404: {"model": ErrorResponse}},
    summary="Download a measurement as CSV or JSON",
)
def export_measurement(
    measurement_id: str,
    format: str = Query("csv", pattern="^(csv|json)$", description="csv or json"),
):
    response = store.get(measurement_id)
    if response is None:
        raise HTTPException(status_code=404, detail=f"no measurement found for id '{measurement_id}'")

    base = (response.filename or measurement_id).rsplit(".", 1)[0]
    if format == "json":
        return JSONResponse(
            content=response.model_dump(mode="json"),
            headers={"Content-Disposition": f'attachment; filename="{base}.measurement.json"'},
        )

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["measurement_id", "filename", "geometry_type", "metric", "value",
                     "units", "detected_crs", "uploaded_at"])
    for metric, value in response.result.measurements.items():
        writer.writerow([
            response.measurement_id,
            response.filename or "",
            response.result.geometry_type,
            metric, value,
            response.result.units,
            response.result.detected_crs,
            response.uploaded_at.isoformat(),
        ])
    return StreamingResponse(
        io.BytesIO(buf.getvalue().encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{base}.measurements.csv"'},
    )
