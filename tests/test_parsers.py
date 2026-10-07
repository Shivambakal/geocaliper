"""Parser tests: GeoJSON shapes and DXF unit handling."""

import io

import ezdxf
import pytest

from app.services.parsers import ParseError, parse_dxf_bytes, parse_geojson_bytes


def test_geojson_feature_collection():
    data = b'{"type":"FeatureCollection","features":[' \
           b'{"type":"Feature","geometry":{"type":"Point","coordinates":[1,2]},"properties":{}}]}'
    geoms, warnings = parse_geojson_bytes(data)
    assert len(geoms) == 1
    assert warnings == []


def test_geojson_bare_geometry():
    geoms, _ = parse_geojson_bytes(b'{"type":"LineString","coordinates":[[0,0],[1,1]]}')
    assert len(geoms) == 1


def test_geojson_garbage_raises():
    with pytest.raises(ParseError):
        parse_geojson_bytes(b"definitely not json {{{")


def test_geojson_null_geometry_warns_and_skips():
    data = (b'{"type":"FeatureCollection","features":['
            b'{"type":"Feature","geometry":null,"properties":{}},'
            b'{"type":"Feature","geometry":{"type":"Point","coordinates":[0,0]},"properties":{}}]}')
    geoms, warnings = parse_geojson_bytes(data)
    assert len(geoms) == 1
    assert any("null geometry" in w for w in warnings)


def test_geojson_empty_raises():
    with pytest.raises(ParseError):
        parse_geojson_bytes(b'{"type":"FeatureCollection","features":[]}')


def _make_dxf(insunits: int) -> bytes:
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = insunits
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (10, 0)])
    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode("utf-8")


def test_dxf_meters():
    geoms, insunits, to_meters, _ = parse_dxf_bytes(_make_dxf(6))
    assert insunits == 6
    assert to_meters == 1.0
    assert len(geoms) == 1


def test_dxf_feet_conversion():
    geoms, insunits, to_meters, _ = parse_dxf_bytes(_make_dxf(2))
    assert to_meters == pytest.approx(0.3048)


def test_dxf_unitless_defaults_to_meters_with_note():
    from app.services.parsers import INSUNITS_NAMES
    geoms, insunits, to_meters, _ = parse_dxf_bytes(_make_dxf(0))
    assert to_meters == 1.0
    assert "assumed meters" in INSUNITS_NAMES[0]


def test_dxf_garbage_raises():
    with pytest.raises(ParseError):
        parse_dxf_bytes(b"this is not a dxf file")


def _make_dxf_with(entity_fn) -> bytes:
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 6
    entity_fn(doc.modelspace())
    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode("utf-8")


def test_dxf_spline_flattens_to_linestring():
    data = _make_dxf_with(
        lambda msp: msp.add_spline(fit_points=[(0, 0), (10, 5), (20, 0), (30, 5)])
    )
    geoms, _, _, _ = parse_dxf_bytes(data)
    assert len(geoms) == 1
    assert geoms[0].geom_type == "LineString"
    # curve is a bit longer than the straight 30-unit span
    assert 30 < geoms[0].length < 45


def test_dxf_ellipse_becomes_polygon():
    data = _make_dxf_with(
        lambda msp: msp.add_ellipse(center=(0, 0), major_axis=(10, 0), ratio=0.5)
    )
    geoms, _, _, _ = parse_dxf_bytes(data)
    assert len(geoms) == 1
    assert geoms[0].geom_type == "Polygon"
    assert geoms[0].area == pytest.approx(3.14159 * 10 * 5, rel=0.02)


def test_dxf_hatch_boundary_becomes_polygon():
    def add(msp):
        hatch = msp.add_hatch()
        hatch.paths.add_polyline_path([(0, 0), (20, 0), (20, 20), (0, 20)], is_closed=True)

    geoms, _, _, _ = parse_dxf_bytes(_make_dxf_with(add))
    polys = [g for g in geoms if g.geom_type == "Polygon"]
    assert len(polys) == 1
    assert polys[0].area == pytest.approx(400, rel=1e-3)


def test_dxf_shapes_sample_end_to_end():
    from pathlib import Path

    data = (Path(__file__).parent.parent / "samples" / "valid_shapes.dxf").read_bytes()
    geoms, insunits, to_meters, warnings = parse_dxf_bytes(data)
    assert insunits == 6
    assert len(geoms) == 3  # spline, ellipse, hatch
    assert warnings == []


def test_parse_upload_dispatch():
    from app.services.parsers import parse_upload

    kml = b'<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark><Point><coordinates>1,2,0</coordinates></Point></Placemark></Document></kml>'
    geoms, fmt, crs, _, scale, _ = parse_upload(".kml", kml)
    assert fmt == "kml" and scale is None and "WGS84" in crs
    assert len(geoms) == 1

    gpx = b'<gpx xmlns="http://www.topografix.com/GPX/1/1" version="1.1"><wpt lat="1" lon="2"/></gpx>'
    geoms, fmt, _, _, scale, _ = parse_upload(".gpx", gpx)
    assert fmt == "gpx" and scale is None

    with pytest.raises(ParseError, match="unsupported file type"):
        parse_upload(".shp", b"whatever")
