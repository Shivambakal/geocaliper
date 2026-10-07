"""The three measurement endpoints + helpers."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from app import config, store
from app.models.schemas import (
    ErrorResponse,
    FileMetadata,
    MeasurementResponse,
    MeasurementResult,
)
from app.services.measure import measure_geojson_geom, measure_planar_m
from app.services.parsers import (
    INSUNITS_NAMES,
    ParseError,
    parse_dxf_bytes,
    parse_geojson_bytes,
)

router = APIRouter(prefix="/api/v1", tags=["measurements"])

SUPPORTED_EXTENSIONS = {".geojson", ".json", ".dxf"}


def _too_big(size: int) -> bool:
    return size > config.MAX_FILE_SIZE_BYTES


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


def _build_geojson_result(geoms, warnings) -> MeasurementResult:
    measurements: dict = {}
    types = set()
    for geom in geoms:
        gtype, m = measure_geojson_geom(geom)
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
        detected_crs="EPSG:4326 (WGS84, per RFC 7946)",
        conversion_details="geodesic (ellipsoidal) measurement, no projection applied",
    )


def _build_dxf_result(geoms, insunits: int, to_meters: float, warnings) -> MeasurementResult:
    unit_name = INSUNITS_NAMES.get(insunits, f"unknown ($INSUNITS={insunits}, assumed meters)")
    measurements: dict = {}
    types = set()
    for geom in geoms:
        gtype, m = measure_planar_m(geom, to_meters)
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
        detected_crs=f"CAD local coordinates (drawing units: {unit_name})",
        conversion_details=f"planar measurement scaled by {to_meters} m/unit",
    )


def _save_and_respond(filename, size_bytes, geometry_count, fmt, result, warnings) -> MeasurementResponse:
    measurement_id = str(uuid.uuid4())
    response = MeasurementResponse(
        measurement_id=measurement_id,
        filename=filename,
        uploaded_at=datetime.now(timezone.utc),
        file_metadata=FileMetadata(
            size_bytes=size_bytes,
            geometry_count=geometry_count,
            format=fmt,
        ),
        result=result,
        errors=warnings,
    )
    store.save(measurement_id, response)
    return response


@router.post(
    "/measurements",
    response_model=MeasurementResponse,
    responses={413: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
    summary="Upload a GeoJSON or DXF file and get measurements",
)
async def upload_measurement(request: Request, file: UploadFile = File(...)):
    filename = file.filename or "upload"
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=422,
            detail=f"unsupported file type '{ext or '(none)'}' — use .geojson, .json or .dxf",
        )

    data = await _read_upload(file)

    try:
        if ext == ".dxf":
            geoms, insunits, to_meters, warnings = parse_dxf_bytes(data)
            result = _build_dxf_result(geoms, insunits, to_meters, warnings)
            fmt = "dxf"
        else:
            geoms, warnings = parse_geojson_bytes(data)
            result = _build_geojson_result(geoms, warnings)
            fmt = "geojson"
    except ParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    return _save_and_respond(filename, len(data), len(geoms), fmt, result, warnings)


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
        geoms, warnings = parse_geojson_bytes(data)
    except ParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    result = _build_geojson_result(geoms, warnings)
    return _save_and_respond(None, len(data), len(geoms), "geojson", result, warnings)


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
