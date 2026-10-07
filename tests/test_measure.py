"""Unit tests for the measurement math."""

import pytest
from shapely.geometry import LineString, MultiLineString, MultiPoint, Point, Polygon

from app.services.measure import geodesic_length_m, measure_geojson_geom, measure_planar_m


def test_geodesic_length_one_degree_latitude():
    # WGS84 meridional arc for 1 degree at the equator ~= 110574.39 m
    length = geodesic_length_m([(0, 0), (0, 1)])
    assert length == pytest.approx(110574.39, rel=1e-4)


def test_polygon_area_and_perimeter():
    # ~100m x ~100m square in Pune (from samples/valid_polygon.geojson)
    ring = [(73.8567, 18.5204), (73.8577, 18.5204), (73.8577, 18.5213),
            (73.8567, 18.5213), (73.8567, 18.5204)]
    gtype, m = measure_geojson_geom(Polygon(ring))
    assert gtype == "Polygon"
    assert m["area_m2"] == pytest.approx(10518.6, rel=0.01)
    assert m["perimeter_m"] == pytest.approx(410.4, rel=0.01)


def test_linestring_length():
    gtype, m = measure_geojson_geom(LineString([(73.8567, 18.5204), (73.8577, 18.5204)]))
    assert gtype == "LineString"
    # 0.001 deg lon at 18.52N ~= 105.6 m
    assert m["length_m"] == pytest.approx(105.6, rel=0.02)


def test_point_count():
    gtype, m = measure_geojson_geom(Point(73.85, 18.52))
    assert m == {"count": 1}


def test_multi_aggregates():
    gtype, m = measure_geojson_geom(MultiPoint([(0, 0), (1, 1)]))
    assert m["count"] == 2
    gtype, m = measure_geojson_geom(MultiLineString([[(0, 0), (0, 1)], [(0, 0), (0, 1)]]))
    assert m["length_m"] == pytest.approx(2 * 110574.39, rel=1e-4)


def test_planar_scales_by_unit_factor():
    # 3-4-5 triangle edge in meters
    gtype, m = measure_planar_m(LineString([(0, 0), (3, 4)]), 1.0)
    assert m["length_m"] == 5.0
    # 10x20 rectangle drawn in feet -> meters
    gtype, m = measure_planar_m(Polygon([(0, 0), (10, 0), (10, 20), (0, 20)]), 0.3048)
    assert m["area_m2"] == pytest.approx(18.581, rel=1e-3)
    assert m["perimeter_m"] == pytest.approx(18.288, rel=1e-3)


def test_coords_of_rejects_point():
    # points have no length/area path through _coords_of
    from app.services.measure import _coords_of
    with pytest.raises(ValueError):
        _coords_of(Point(0, 0))
