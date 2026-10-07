"""In-memory result store. Ephemeral by design — see README 'Design decisions'."""

from app.models.schemas import MeasurementResponse

_store: dict[str, MeasurementResponse] = {}


def save(measurement_id: str, response: MeasurementResponse) -> None:
    _store[measurement_id] = response


def get(measurement_id: str) -> MeasurementResponse | None:
    return _store.get(measurement_id)
