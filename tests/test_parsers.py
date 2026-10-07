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
