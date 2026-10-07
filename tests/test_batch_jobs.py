"""Tests for batch upload, CSV/JSON export, and async jobs."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app

SAMPLES = Path(__file__).parent.parent / "samples"

client = TestClient(app)


def _upload(name: str):
    with open(SAMPLES / name, "rb") as f:
        return client.post("/api/v1/measurements", files={"file": (name, f)})


def _measurement_id_for(name: str) -> str:
    return _upload(name).json()["measurement_id"]


# --- batch ---

def test_batch_two_files():
    with open(SAMPLES / "valid_polygon.geojson", "rb") as f1, \
         open(SAMPLES / "valid_route.gpx", "rb") as f2:
        r = client.post("/api/v1/measurements/batch", files=[
            ("files", ("valid_polygon.geojson", f1)),
            ("files", ("valid_route.gpx", f2)),
        ])
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["file_count"] == 2
    assert body["summary"]["succeeded"] == 2
    assert body["summary"]["failed"] == 0
    assert body["summary"]["total_area_m2"] == pytest.approx(10518.6, rel=0.01)
    assert body["summary"]["total_length_m"] > 100
    assert all(f["ok"] for f in body["files"])
    assert body["batch_id"]


def test_batch_summary_matches_parts():
    with open(SAMPLES / "valid_track.kml", "rb") as f1, \
         open(SAMPLES / "valid_track.kmz", "rb") as f2:
        r = client.post("/api/v1/measurements/batch", files=[
            ("files", ("a.kml", f1)),
            ("files", ("b.kmz", f2)),
        ])
    body = r.json()
    areas = [f["measurement"]["result"]["measurements"].get("area_m2", 0)
             for f in body["files"]]
    assert body["summary"]["total_area_m2"] == pytest.approx(sum(areas), rel=1e-6)


def test_batch_one_bad_file_keeps_going():
    with open(SAMPLES / "valid_polygon.geojson", "rb") as f1:
        r = client.post("/api/v1/measurements/batch", files=[
            ("files", ("good.geojson", f1)),
            ("files", ("bad.geojson", b"{{{ nope")),
            ("files", ("bad.shp", b"nope")),
        ])
    assert r.status_code == 200
    body = r.json()
    assert body["summary"] == {
        "file_count": 3, "succeeded": 1, "failed": 2,
        "total_area_m2": pytest.approx(10518.6, rel=0.01),
        "total_length_m": 0.0, "total_points": 0,
    }
    assert body["files"][1]["ok"] is False
    assert body["files"][1]["error"]


def test_batch_too_many_files_rejected():
    files = [("files", (f"f{i}.geojson", b'{"type":"Point","coordinates":[0,0]}'))
             for i in range(11)]
    r = client.post("/api/v1/measurements/batch", files=files)
    assert r.status_code == 422
    assert "too many" in r.json()["detail"]


def test_batch_empty_file_counts_as_failed():
    r = client.post("/api/v1/measurements/batch", files=[
        ("files", ("empty.geojson", b"")),
    ])
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["failed"] == 1
    assert body["files"][0]["ok"] is False


def test_batch_individual_results_stored():
    with open(SAMPLES / "valid_polygon.geojson", "rb") as f:
        batch = client.post("/api/v1/measurements/batch",
                            files=[("files", ("p.geojson", f))]).json()
    mid = batch["files"][0]["measurement"]["measurement_id"]
    r = client.get(f"/api/v1/measurements/{mid}")
    assert r.status_code == 200


# --- export ---

def test_export_csv():
    mid = _measurement_id_for("valid_polygon.geojson")
    r = client.get(f"/api/v1/measurements/{mid}/export", params={"format": "csv"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    assert ".csv" in r.headers["content-disposition"]
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("measurement_id,filename,geometry_type,metric,value")
    assert any("area_m2" in line and "10518" in line for line in lines[1:])


def test_export_json_attachment():
    mid = _measurement_id_for("valid_route.gpx")
    r = client.get(f"/api/v1/measurements/{mid}/export", params={"format": "json"})
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    body = r.json()
    assert body["measurement_id"] == mid
    assert body["file_metadata"]["format"] == "gpx"


def test_export_defaults_to_csv():
    mid = _measurement_id_for("valid_polygon.geojson")
    r = client.get(f"/api/v1/measurements/{mid}/export")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")


def test_export_unknown_id_404():
    r = client.get("/api/v1/measurements/nope/export")
    assert r.status_code == 404


def test_export_bad_format_422():
    mid = _measurement_id_for("valid_polygon.geojson")
    r = client.get(f"/api/v1/measurements/{mid}/export", params={"format": "xml"})
    assert r.status_code == 422


# --- async jobs ---

def test_job_full_lifecycle():
    with open(SAMPLES / "valid_route.gpx", "rb") as f:
        r = client.post("/api/v1/jobs", files={"file": ("valid_route.gpx", f)})
    assert r.status_code == 202
    created = r.json()
    assert created["status"] == "queued"
    assert created["poll"].endswith(created["job_id"])
    assert "hint" in created

    # TestClient runs background tasks inline, so it's done by now
    r = client.get(f"/api/v1/jobs/{created['job_id']}")
    assert r.status_code == 200
    job = r.json()
    assert job["status"] == "done"
    assert job["result"]["file_metadata"]["format"] == "gpx"
    assert job["measurement_id"] == job["result"]["measurement_id"]

    # and the measurement itself is retrievable the normal way
    r = client.get(f"/api/v1/measurements/{job['measurement_id']}")
    assert r.status_code == 200


def test_job_failure_path():
    r = client.post("/api/v1/jobs", files={"file": ("bad.geojson", b"{{{ nope")})
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    r = client.get(f"/api/v1/jobs/{job_id}")
    job = r.json()
    assert job["status"] == "failed"
    assert job["error"]
    assert job["result"] is None


def test_job_unknown_id_404():
    assert client.get("/api/v1/jobs/does-not-exist").status_code == 404


def test_job_bad_extension_422():
    r = client.post("/api/v1/jobs", files={"file": ("x.shp", b"junk")})
    assert r.status_code == 422


def test_large_upload_auto_suggests_async(monkeypatch):
    monkeypatch.setattr(config, "ASYNC_SUGGEST_BYTES", 100)
    with open(SAMPLES / "valid_polygon.geojson", "rb") as f:
        r = client.post("/api/v1/measurements", files={"file": ("big.geojson", f)})
    assert r.status_code == 202
    body = r.json()
    assert "sync=true" in body["hint"]
    assert body["status"] == "queued"

    r = client.get(f"/api/v1/jobs/{body['job_id']}")
    assert r.json()["status"] == "done"


def test_sync_param_forces_sync_on_large_file(monkeypatch):
    monkeypatch.setattr(config, "ASYNC_SUGGEST_BYTES", 100)
    with open(SAMPLES / "valid_polygon.geojson", "rb") as f:
        r = client.post("/api/v1/measurements?sync=true",
                        files={"file": ("big.geojson", f)})
    assert r.status_code == 200
    assert r.json()["result"]["geometry_type"] == "Polygon"


def test_job_with_kml():
    with open(SAMPLES / "valid_track.kml", "rb") as f:
        r = client.post("/api/v1/jobs", files={"file": ("t.kml", f)})
    assert r.status_code == 202
    job = client.get(f"/api/v1/jobs/{r.json()['job_id']}").json()
    assert job["status"] == "done"
    assert job["result"]["file_metadata"]["format"] == "kml"
