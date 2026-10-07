"""In-memory result store. Ephemeral by design — see README 'Design decisions'."""

from app.models.schemas import JobResponse, MeasurementResponse

_store: dict[str, MeasurementResponse] = {}


def save(measurement_id: str, response: MeasurementResponse) -> None:
    _store[measurement_id] = response


def get(measurement_id: str) -> MeasurementResponse | None:
    return _store.get(measurement_id)


# Async jobs live here too — same ephemerality tradeoff as measurements.
_jobs: dict[str, JobResponse] = {}


def save_job(job: JobResponse) -> None:
    _jobs[job.job_id] = job


def get_job(job_id: str) -> JobResponse | None:
    return _jobs.get(job_id)
