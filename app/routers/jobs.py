"""Async measurement jobs: upload now, poll for the result.

Same parsing as the sync endpoint, just deferred to a background task so
big files don't hold the request open. Storage is the same in-memory dict
as everything else — see README 'Design decisions'.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from app import config, store
from app.models.schemas import ErrorResponse, JobResponse, JobStatus
from app.routers.measurements import _ext_of, _read_upload
from app.services.parsers import ParseError

router = APIRouter(prefix="/api/v1", tags=["jobs"])

SUPPORTED_EXTENSIONS = {".geojson", ".json", ".dxf", ".kml", ".kmz", ".gpx"}


def run_job(job_id: str, filename: str, data: bytes):
    """Background worker: parse + measure, then record done or failed."""
    from app.routers.measurements import _measure_bytes  # deferred, avoids a cycle

    job = store.get_job(job_id)
    if job is None:
        return
    job.status = JobStatus.PROCESSING
    store.save_job(job)
    try:
        measurement = _measure_bytes(filename, data)
    except ParseError as exc:
        job.status = JobStatus.FAILED
        job.error = str(exc)
    except Exception as exc:  # never leave a job stuck in "processing"
        job.status = JobStatus.FAILED
        job.error = f"unexpected error during processing: {exc}"
    else:
        job.status = JobStatus.DONE
        job.measurement_id = measurement.measurement_id
        job.result = measurement
    job.hint = None
    store.save_job(job)


def _new_job(filename: str) -> JobResponse:
    job = JobResponse(
        job_id=str(uuid.uuid4()),
        filename=filename,
        status=JobStatus.QUEUED,
        created_at=datetime.now(timezone.utc),
        hint="processing in the background",
    )
    store.save_job(job)
    return job


def _job_created_response(job: JobResponse) -> JSONResponse:
    return JSONResponse(
        status_code=202,
        content={
            "job_id": job.job_id,
            "filename": job.filename,
            "status": job.status,
            "hint": job.hint,
            "poll": f"/api/v1/jobs/{job.job_id}",
        },
    )


@router.post(
    "/jobs",
    responses={413: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
    summary="Submit a file for async measurement, poll for the result",
)
async def create_job(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    filename = file.filename or "upload"
    if _ext_of(filename) not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=422, detail="unsupported file type for async job")

    data = await _read_upload(file)

    job = _new_job(filename)
    job.hint = (
        f"job {job.job_id} queued. Poll GET /api/v1/jobs/{job.job_id} "
        "until status is done or failed."
    )
    store.save_job(job)
    background_tasks.add_task(run_job, job.job_id, filename, data)
    return _job_created_response(job)


@router.get(
    "/jobs/{job_id}",
    response_model=JobResponse,
    responses={404: {"model": ErrorResponse}},
    summary="Check an async job's status and result",
)
def get_job(job_id: str):
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"no job found for id '{job_id}'")
    return job
