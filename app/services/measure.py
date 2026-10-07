"""Real-world measurement math.

GeoJSON coords are WGS84 lat/lon, so planar math on degrees would be wrong.
Use geographiclib's geodesic routines — distances and areas come out in
meters directly on the ellipsoid.
"""

from geographiclib.geodesic import Geodesic
from geographiclib.polygonarea import PolygonArea

GEOD = Geodesic.WGS84


def _coords_of(geom):
    """Yield (lon, lat) pairs for the outer ring / line of a shapely geom."""
    if geom.geom_type in ("LineString", "LinearRing"):
        return list(geom.coords)
    if geom.geom_type == "Polygon":
        return list(geom.exterior.coords)
    raise ValueError(f"unsupported geometry for measurement: {geom.geom_type}")


def geodesic_length_m(coords) -> float:
    """Sum of geodesic segment lengths in meters. coords = [(lon, lat), ...]."""
    total = 0.0
    for (lon1, lat1), (lon2, lat2) in zip(coords, coords[1:]):
        total += GEOD.Inverse(lat1, lon1, lat2, lon2)["s12"]
    return total


def geodesic_area_perimeter(coords) -> tuple[float, float]:
    """(area_m2, perimeter_m) for a closed ring, coords = [(lon, lat), ...]."""
    poly = PolygonArea(GEOD, False)
    for lon, lat in coords:
        poly.AddPoint(lat, lon)
    _, perimeter, area = poly.Compute()
    return abs(area), perimeter


def measure_geojson_geom(geom) -> tuple[str, dict]:
    """Return (geometry_type, measurements dict) for one shapely geometry."""
    gtype = geom.geom_type

    if gtype == "Point":
        return gtype, {"count": 1}

    if gtype in ("LineString", "LinearRing"):
        return gtype, {"length_m": round(geodesic_length_m(_coords_of(geom)), 3)}

    if gtype == "Polygon":
        area, perim = geodesic_area_perimeter(_coords_of(geom))
        return gtype, {
            "area_m2": round(area, 3),
            "perimeter_m": round(perim, 3),
        }

    if gtype in ("MultiPoint", "MultiLineString", "MultiPolygon", "GeometryCollection"):
        # aggregate over parts, keep the top-level type for honesty
        parts = list(geom.geoms)
        if gtype == "MultiPoint":
            return gtype, {"count": len(parts)}
        if gtype == "MultiLineString":
            total = sum(geodesic_length_m(_coords_of(p)) for p in parts)
            return gtype, {"length_m": round(total, 3), "part_count": len(parts)}
        # MultiPolygon / GeometryCollection
        total_area = total_perim = 0.0
        for p in parts:
            if p.geom_type == "Polygon":
                a, pr = geodesic_area_perimeter(_coords_of(p))
                total_area += a
                total_perim += pr
        return gtype, {
            "area_m2": round(total_area, 3),
            "perimeter_m": round(total_perim, 3),
            "part_count": len(parts),
        }

    raise ValueError(f"unsupported geometry type: {gtype}")


def measure_planar_m(geom, to_meters: float) -> tuple[str, dict]:
    """Planar measurement for CAD data, scaled to meters by to_meters factor."""
    gtype = geom.geom_type

    if gtype == "Point":
        return gtype, {"count": 1}
    if gtype in ("LineString", "LinearRing"):
        return gtype, {"length_m": round(geom.length * to_meters, 3)}
    if gtype == "Polygon":
        return gtype, {
            "area_m2": round(geom.area * to_meters**2, 3),
            "perimeter_m": round(geom.length * to_meters, 3),
        }
    raise ValueError(f"unsupported CAD geometry type: {gtype}")
