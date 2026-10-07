"""Tests for KML/KMZ/GPX support — parsing and HTTP layer."""

import io
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.parsers import (
    ParseError,
    parse_gpx_bytes,
    parse_kml_bytes,
    parse_kmz_bytes,
)

SAMPLES = Path(__file__).parent.parent / "samples"

client = TestClient(app)


# --- KML parsing ---

def test_kml_sample_parses_all_types():
    geoms, warnings = parse_kml_bytes((SAMPLES / "valid_track.kml").read_bytes())
    types = sorted(g.geom_type for g in geoms)
    assert types == ["GeometryCollection", "LineString", "Point", "Polygon"]
    assert warnings == []


def test_kml_polygon_area_reasonable():
    # the park plot is ~0.001 x 0.001 degrees near lat 18.51
    geoms, _ = parse_kml_bytes((SAMPLES / "valid_track.kml").read_bytes())
    poly = next(g for g in geoms if g.geom_type == "Polygon")
    from app.services.measure import geodesic_area_perimeter
    area, _ = geodesic_area_perimeter(list(poly.exterior.coords))
    assert area == pytest.approx(11679, rel=0.05)


def test_kml_multigeometry_parts():
    geoms, _ = parse_kml_bytes((SAMPLES / "valid_track.kml").read_bytes())
    mc = next(g for g in geoms if g.geom_type == "GeometryCollection")
    assert sorted(p.geom_type for p in mc.geoms) == ["LineString", "Point"]


def test_kml_no_placemarks_raises():
    with pytest.raises(ParseError, match="no Placemark"):
        parse_kml_bytes(b'<kml xmlns="http://www.opengis.net/kml/2.2"><Document/></kml>')


def test_kml_invalid_xml_raises():
    with pytest.raises(ParseError):
        parse_kml_bytes(b"<kml><unclosed>")


def test_kml_placemark_without_geometry_warns():
    kml = """<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
      <Placemark><name>empty</name></Placemark>
      <Placemark><Point><coordinates>73.8,18.5,0</coordinates></Point></Placemark>
    </Document></kml>""".encode()
    geoms, warnings = parse_kml_bytes(kml)
    assert len(geoms) == 1
    assert any("empty" in w for w in warnings)


def test_kml_polygon_with_hole():
    kml = """<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark>
      <Polygon><outerBoundaryIs><LinearRing><coordinates>
      0,0,0 10,0,0 10,10,0 0,10,0 0,0,0</coordinates></LinearRing></outerBoundaryIs>
      <innerBoundaryIs><LinearRing><coordinates>
      2,2,0 4,2,0 4,4,0 2,4,0 2,2,0</coordinates></LinearRing></innerBoundaryIs>
      </Polygon></Placemark></Document></kml>""".encode()
    geoms, _ = parse_kml_bytes(kml)
    assert geoms[0].geom_type == "Polygon"
    assert len(geoms[0].interiors) == 1


# --- KMZ parsing ---

def test_kmz_sample_parses():
    geoms, warnings = parse_kmz_bytes((SAMPLES / "valid_track.kmz").read_bytes())
    assert len(geoms) == 4
    assert warnings == []


def _make_kmz(inner_name: str, content: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(inner_name, content)
    return buf.getvalue()


def test_kmz_bad_zip_raises():
    with pytest.raises(ParseError, match="not a valid KMZ"):
        parse_kmz_bytes(b"definitely not a zip")


def test_kmz_without_kml_raises():
    with pytest.raises(ParseError, match="no .kml file"):
        parse_kmz_bytes(_make_kmz("readme.txt", b"hello"))


# --- GPX parsing ---

def test_gpx_sample_tracks_routes_waypoints():
    geoms, warnings = parse_gpx_bytes((SAMPLES / "valid_route.gpx").read_bytes())
    types = sorted(g.geom_type for g in geoms)
    assert types == ["LineString", "LineString", "LineString", "Point", "Point"]
    assert warnings == []


def test_gpx_track_length_positive():
    geoms, _ = parse_gpx_bytes((SAMPLES / "valid_route.gpx").read_bytes())
    from app.services.measure import geodesic_length_m
    total = sum(geodesic_length_m(list(g.coords)) for g in geoms if g.geom_type == "LineString")
    assert total > 100  # commute + ride are several km


def test_gpx_bad_xml_raises():
    with pytest.raises(ParseError):
        parse_gpx_bytes(b"<gpx><trk>")


def test_gpx_wrong_root_raises():
    with pytest.raises(ParseError, match="isn't <gpx>"):
        parse_gpx_bytes(b"<kml></kml>")


def test_gpx_no_content_raises():
    with pytest.raises(ParseError, match="no tracks"):
        parse_gpx_bytes(
            b'<gpx xmlns="http://www.topografix.com/GPX/1/1" version="1.1"></gpx>'
        )


def test_gpx_single_point_segment_warns():
    gpx = """<gpx xmlns="http://www.topografix.com/GPX/1/1" version="1.1">
      <trk><trkseg><trkpt lat="18.5" lon="73.8"/></trkseg></trk>
      <trk><trkseg><trkpt lat="18.5" lon="73.8"/><trkpt lat="18.6" lon="73.9"/></trkseg></trk>
    </gpx>""".encode()
    geoms, warnings = parse_gpx_bytes(gpx)
    assert len(geoms) == 1
    assert any("single point" in w for w in warnings)


# --- HTTP layer ---

def test_upload_kml():
    with open(SAMPLES / "valid_track.kml", "rb") as f:
        r = client.post("/api/v1/measurements", files={"file": ("valid_track.kml", f)})
    assert r.status_code == 200
    body = r.json()
    assert body["file_metadata"]["format"] == "kml"
    assert body["result"]["geometry_type"] == "Mixed"
    assert body["result"]["measurements"]["area_m2"] == pytest.approx(11679, rel=0.05)
    assert "WGS84" in body["result"]["detected_crs"]


def test_upload_kmz():
    with open(SAMPLES / "valid_track.kmz", "rb") as f:
        r = client.post("/api/v1/measurements", files={"file": ("valid_track.kmz", f)})
    assert r.status_code == 200
    assert r.json()["file_metadata"]["format"] == "kmz"


def test_upload_gpx():
    with open(SAMPLES / "valid_route.gpx", "rb") as f:
        r = client.post("/api/v1/measurements", files={"file": ("valid_route.gpx", f)})
    assert r.status_code == 200
    body = r.json()
    assert body["file_metadata"]["format"] == "gpx"
    assert body["result"]["measurements"]["length_m"] > 100
    assert body["result"]["measurements"]["count"] == 2  # two waypoints
