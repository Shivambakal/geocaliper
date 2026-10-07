"""File parsers: GeoJSON and DXF -> shapely geometries."""

import io
import json

import ezdxf
from shapely.geometry import shape

# DXF $INSUNITS -> meters multiplier. 0 = unitless, assume meters (see README).
INSUNITS_TO_METERS = {
    0: 1.0,
    1: 0.0254,      # inches
    2: 0.3048,      # feet
    3: 1609.344,    # miles
    4: 0.001,       # mm
    5: 0.01,        # cm
    6: 1.0,         # meters
    7: 1000.0,      # km
    8: 2.54e-8,     # microinches
    9: 2.54e-5,     # mils
    10: 0.9144,     # yards
    13: 1e-6,       # microns
    14: 0.1,        # decimeters
    15: 10.0,       # decameters
    16: 100.0,      # hectometers
    21: 0.3048006096012192,  # US survey feet
}

INSUNITS_NAMES = {
    0: "unitless (assumed meters)",
    1: "inches", 2: "feet", 3: "miles", 4: "millimeters",
    5: "centimeters", 6: "meters", 7: "kilometers",
    10: "yards", 21: "US survey feet",
}


class ParseError(ValueError):
    """Raised when a file can't be parsed into geometries."""


def parse_geojson_bytes(data: bytes):
    """Parse raw GeoJSON bytes -> (geometries, warnings)."""
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ParseError(f"not valid JSON: {exc}")

    geoms, warnings = [], []
    if obj.get("type") == "FeatureCollection":
        features = obj.get("features", [])
        if not isinstance(features, list):
            raise ParseError("'features' must be a list")
        for i, feat in enumerate(features):
            g = (feat or {}).get("geometry")
            if g is None:
                warnings.append(f"feature {i}: null geometry, skipped")
                continue
            try:
                geoms.append(shape(g))
            except Exception as exc:
                warnings.append(f"feature {i}: bad geometry, skipped ({exc})")
    elif "coordinates" in obj or obj.get("type") == "GeometryCollection":
        try:
            geoms.append(shape(obj))
        except Exception as exc:
            raise ParseError(f"invalid geometry object: {exc}")
    else:
        raise ParseError("not a GeoJSON geometry or FeatureCollection")

    if not geoms:
        raise ParseError("no usable geometries found in file")
    return geoms, warnings


def _dxf_entity_to_coords(entity):
    """Best-effort (x, y) extraction for common DXF entities."""
    t = entity.dxftype()
    if t == "LINE":
        s, e = entity.dxf.start, entity.dxf.end
        return [("LineString", [(s.x, s.y), (e.x, e.y)])]
    if t in ("LWPOLYLINE", "POLYLINE"):
        pts = [(p[0], p[1]) for p in entity.get_points()]
        if len(pts) < 2:
            return []
        kind = "Polygon" if getattr(entity, "closed", False) and len(pts) > 2 else "LineString"
        return [(kind, pts)]
    if t == "CIRCLE":
        c, r = entity.dxf.center, entity.dxf.radius
        # approximate with a 64-gon, fine for measurement purposes
        import math
        pts = [
            (c.x + r * math.cos(a), c.y + r * math.sin(a))
            for a in (2 * math.pi * i / 64 for i in range(64))
        ]
        return [("Polygon", pts)]
    if t == "ARC":
        import math
        c = entity.dxf.center
        r, a0, a1 = entity.dxf.radius, math.radians(entity.dxf.start_angle), math.radians(entity.dxf.end_angle)
        if a1 <= a0:
            a1 += 2 * math.pi
        n = max(8, int(32 * (a1 - a0) / (2 * math.pi)))
        pts = [(c.x + r * math.cos(a0 + (a1 - a0) * i / n),
                c.y + r * math.sin(a0 + (a1 - a0) * i / n)) for i in range(n + 1)]
        return [("LineString", pts)]
    if t == "POINT":
        p = entity.dxf.location
        return [("Point", [(p.x, p.y)])]
    return []  # TEXT, DIMENSION, etc. — not measurable, skip quietly


def parse_dxf_bytes(data: bytes):
    """Parse DXF bytes -> (geometries, unit_code, warnings)."""
    try:
        # ezdxf wants a text stream, not bytes
        doc = ezdxf.read(io.StringIO(data.decode("utf-8", errors="replace")))
    except (ezdxf.DXFError, OSError) as exc:
        raise ParseError(f"not a readable DXF file: {exc}")

    insunits = int(doc.header.get("$INSUNITS", 0))
    to_meters = INSUNITS_TO_METERS.get(insunits)
    warnings = []
    if to_meters is None:
        warnings.append(f"$INSUNITS={insunits} has no known conversion, treating as meters")
        to_meters = 1.0

    from shapely.geometry import LineString, Point, Polygon

    geoms = []
    skipped = 0
    for entity in doc.modelspace():
        for kind, pts in _dxf_entity_to_coords(entity):
            try:
                if kind == "Point":
                    geoms.append(Point(pts[0]))
                elif kind == "LineString":
                    geoms.append(LineString(pts))
                else:
                    geoms.append(Polygon(pts))
            except Exception:
                skipped += 1
    if skipped:
        warnings.append(f"{skipped} entit(ies) could not be converted, skipped")

    if not geoms:
        raise ParseError("no measurable geometry found in DXF modelspace")
    return geoms, insunits, to_meters, warnings
