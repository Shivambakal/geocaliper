"""End-to-end tests through the HTTP layer."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

SAMPLES = Path(__file__).parent.parent / "samples"

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_upload_geojson_polygon():
    with open(SAMPLES / "valid_polygon.geojson", "rb") as f:
        r = client.post("/api/v1/measurements", files={"file": ("valid_polygon.geojson", f)})
    assert r.status_code == 200
    body = r.json()
    assert body["result"]["geometry_type"] == "Polygon"
    assert body["result"]["measurements"]["area_m2"] == pytest.approx(10518.6, rel=0.01)
    assert body["file_metadata"]["format"] == "geojson"
    assert body["measurement_id"]


def test_upload_dxf_polyline():
    with open(SAMPLES / "valid_polyline.dxf", "rb") as f:
        r = client.post("/api/v1/measurements", files={"file": ("valid_polyline.dxf", f)})
    assert r.status_code == 200
    body = r.json()
    assert body["result"]["geometry_type"] == "Mixed"  # open polyline + closed rectangle
    assert body["result"]["measurements"]["length_m"] == pytest.approx(70, rel=1e-3)
    assert body["result"]["measurements"]["perimeter_m"] == pytest.approx(60, rel=1e-3)
    assert body["result"]["measurements"]["area_m2"] == pytest.approx(200, rel=1e-3)
    assert "meters" in body["result"]["detected_crs"]


def test_raw_geojson_endpoint():
    payload = {"type": "LineString", "coordinates": [[73.8567, 18.5204], [73.8577, 18.5204]]}
    r = client.post("/api/v1/measurements/geojson", json=payload)
    assert r.status_code == 200
    assert r.json()["result"]["measurements"]["length_m"] == pytest.approx(105.6, rel=0.02)


def test_get_by_id_roundtrip():
    payload = {"type": "Point", "coordinates": [73.85, 18.52]}
    created = client.post("/api/v1/measurements/geojson", json=payload).json()
    mid = created["measurement_id"]
    r = client.get(f"/api/v1/measurements/{mid}")
    assert r.status_code == 200
    assert r.json()["measurement_id"] == mid


def test_get_unknown_id_404():
    r = client.get("/api/v1/measurements/does-not-exist")
    assert r.status_code == 404


def test_malformed_file_422():
    with open(SAMPLES / "malformed_example.txt", "rb") as f:
        # .txt is rejected by extension check before parsing
        r = client.post("/api/v1/measurements", files={"file": ("malformed_example.txt", f)})
    assert r.status_code == 422

    # same garbage with a .geojson name reaches the parser and still 422s
    r = client.post(
        "/api/v1/measurements",
        files={"file": ("bad.geojson", b"{{{{ not json")},
    )
    assert r.status_code == 422
    assert "detail" in r.json()


def test_empty_upload_422():
    r = client.post("/api/v1/measurements", files={"file": ("empty.geojson", b"")})
    assert r.status_code == 422


def test_empty_raw_body_422():
    r = client.post("/api/v1/measurements/geojson", content=b"")
    assert r.status_code == 422


def test_request_id_header_present():
    r = client.get("/health")
    assert "x-request-id" in r.headers
