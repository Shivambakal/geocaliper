"""File parsers: GeoJSON, KML/KMZ, GPX and DXF -> shapely geometries."""

import io
import json
import zipfile
import xml.etree.ElementTree as ET

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
    if t == "SPLINE":
        # flatten the curve into a polyline; sagitta 0.05 is plenty for measurement
        try:
            pts = [(v.x, v.y) for v in entity.flattening(0.05)]
        except Exception:
            return []
        if len(pts) < 2:
            return []
        kind = "Polygon" if entity.closed and len(pts) > 2 else "LineString"
        return [(kind, pts)]
    if t == "ELLIPSE":
        try:
            pts = [(v.x, v.y) for v in entity.flattening(0.05)]
        except Exception:
            return []
        if len(pts) < 3:
            return []
        return [("Polygon", pts)]
    if t == "HATCH":
        # boundary paths -> polygons via ezdxf's path machinery
        try:
            from ezdxf.path import make_path
            path = make_path(entity)
        except Exception:
            return []
        out = []
        for sub in path.sub_paths():
            try:
                pts = [(v.x, v.y) for v in sub.flattening(0.1)]
            except Exception:
                continue
            if len(pts) > 2:
                out.append(("Polygon", pts))
        return out
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


# ---------------------------------------------------------------------------
# KML / KMZ — stdlib only, no extra dependency.
# KML coordinates are lon,lat[,alt] per the spec, i.e. WGS84 like GeoJSON.
# ---------------------------------------------------------------------------

KML_NS = "http://www.opengis.net/kml/2.2"


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _kml_coords(text: str):
    """'lon,lat,alt lon,lat,alt ...' -> [(lon, lat), ...]."""
    pts = []
    for token in (text or "").split():
        parts = token.split(",")
        if len(parts) < 2:
            continue
        try:
            pts.append((float(parts[0]), float(parts[1])))
        except ValueError:
            continue
    return pts


def _kml_geom(elem):
    """Convert one KML geometry element -> shapely geometry (or None)."""
    from shapely.geometry import LineString, MultiLineString, MultiPoint, MultiPolygon, Point, Polygon

    tag = _strip_ns(elem.tag)
    if tag == "Point":
        coords_el = elem.find(f"{{{KML_NS}}}coordinates")
        if coords_el is None:
            coords_el = elem.find("coordinates")  # some exporters drop the namespace
        pts = _kml_coords(coords_el.text if coords_el is not None else "")
        return Point(pts[0]) if pts else None
    if tag == "LineString":
        coords_el = elem.find(f"{{{KML_NS}}}coordinates")
        if coords_el is None:
            coords_el = elem.find("coordinates")
        pts = _kml_coords(coords_el.text if coords_el is not None else "")
        return LineString(pts) if len(pts) >= 2 else None
    if tag == "Polygon":
        outer = elem.find(f".//{{{KML_NS}}}outerBoundaryIs/{{{KML_NS}}}LinearRing/{{{KML_NS}}}coordinates")
        if outer is None:
            outer = elem.find(".//outerBoundaryIs/LinearRing/coordinates")
        shell = _kml_coords(outer.text if outer is not None else "")
        if len(shell) < 4:
            return None
        holes = []
        inners = elem.findall(f".//{{{KML_NS}}}innerBoundaryIs/{{{KML_NS}}}LinearRing/{{{KML_NS}}}coordinates")
        inners += elem.findall(".//innerBoundaryIs/LinearRing/coordinates")
        for inner in inners:
            hole = _kml_coords(inner.text)
            if len(hole) >= 4:
                holes.append(hole)
        try:
            return Polygon(shell, holes)
        except Exception:
            return None
    if tag == "MultiGeometry":
        parts = [_kml_geom(child) for child in elem]
        parts = [p for p in parts if p is not None]
        if not parts:
            return None
        kinds = {p.geom_type for p in parts}
        if len(kinds) == 1:
            kind = kinds.pop()
            if kind == "Point":
                return MultiPoint(parts)
            if kind == "LineString":
                return MultiLineString(parts)
            if kind == "Polygon":
                return MultiPolygon(parts)
        from shapely.geometry import GeometryCollection
        return GeometryCollection(parts)
    return None


def parse_kml_bytes(data: bytes):
    """Parse KML bytes -> (geometries, warnings)."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ParseError(f"not valid KML/XML: {exc}")

    geoms, warnings = [], []
    placemarks = [el for el in root.iter() if _strip_ns(el.tag) == "Placemark"]
    if not placemarks:
        raise ParseError("no Placemark elements found in KML")

    for i, pm in enumerate(placemarks):
        found = False
        for child in pm:
            g = _kml_geom(child)
            if g is not None:
                geoms.append(g)
                found = True
        if not found:
            name_el = next((c for c in pm if _strip_ns(c.tag) == "name"), None)
            label = name_el.text.strip() if name_el is not None and name_el.text else f"#{i}"
            warnings.append(f"placemark '{label}': no supported geometry, skipped")

    if not geoms:
        raise ParseError("no measurable geometry found in KML")
    return geoms, warnings


def parse_kmz_bytes(data: bytes):
    """KMZ is just a zipped KML — unzip in memory, parse the first .kml inside."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ParseError(f"not a valid KMZ (zip) file: {exc}")
    kml_names = [n for n in zf.namelist() if n.lower().endswith(".kml")]
    if not kml_names:
        raise ParseError("KMZ contains no .kml file")
    with zf.open(kml_names[0]) as f:
        return parse_kml_bytes(f.read())


# ---------------------------------------------------------------------------
# GPX — stdlib only. Tracks/routes -> LineStrings, waypoints -> Points.
# GPX is WGS84 by definition.
# ---------------------------------------------------------------------------

GPX_NS = "http://www.topografix.com/GPX/1/1"


def _gpx_pts(parent, pt_tag: str):
    pts = []
    for pt in list(parent.findall(f"{{{GPX_NS}}}{pt_tag}")) + list(parent.findall(pt_tag)):
        try:
            pts.append((float(pt.get("lon")), float(pt.get("lat"))))
        except (TypeError, ValueError):
            continue
    return pts


def parse_gpx_bytes(data: bytes):
    """Parse GPX bytes -> (geometries, warnings)."""
    from shapely.geometry import LineString, Point

    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ParseError(f"not valid GPX/XML: {exc}")
    if _strip_ns(root.tag).lower() != "gpx":
        raise ParseError("not a GPX file (root element isn't <gpx>)")

    geoms, warnings = [], []

    for trk in list(root.findall(f"{{{GPX_NS}}}trk")) + list(root.findall("trk")):
        segs = list(trk.findall(f"{{{GPX_NS}}}trkseg")) + list(trk.findall("trkseg"))
        for seg in segs:
            pts = _gpx_pts(seg, "trkpt")
            if len(pts) >= 2:
                geoms.append(LineString(pts))
            elif pts:
                warnings.append("track segment with a single point, skipped")

    for rte in list(root.findall(f"{{{GPX_NS}}}rte")) + list(root.findall("rte")):
        pts = _gpx_pts(rte, "rtept")
        if len(pts) >= 2:
            geoms.append(LineString(pts))

    for wpt in list(root.findall(f"{{{GPX_NS}}}wpt")) + list(root.findall("wpt")):
        try:
            geoms.append(Point(float(wpt.get("lon")), float(wpt.get("lat"))))
        except (TypeError, ValueError):
            warnings.append("waypoint with bad coordinates, skipped")

    if not geoms:
        raise ParseError("no tracks, routes or waypoints found in GPX")
    return geoms, warnings


# ---------------------------------------------------------------------------
# Unified dispatch: extension -> (geoms, format, crs label, conversion notes,
# planar scale or None for geographic formats, warnings)
# ---------------------------------------------------------------------------

def parse_upload(ext: str, data: bytes):
    """Parse any supported upload. Raises ParseError on bad input."""
    if ext in (".geojson", ".json"):
        geoms, warnings = parse_geojson_bytes(data)
        return (
            geoms, "geojson",
            "EPSG:4326 (WGS84, per RFC 7946)",
            "geodesic (ellipsoidal) measurement, no projection applied",
            None, warnings,
        )
    if ext == ".dxf":
        geoms, insunits, to_meters, warnings = parse_dxf_bytes(data)
        unit_name = INSUNITS_NAMES.get(insunits, f"unknown ($INSUNITS={insunits}, assumed meters)")
        return (
            geoms, "dxf",
            f"CAD local coordinates (drawing units: {unit_name})",
            f"planar measurement scaled by {to_meters} m/unit",
            to_meters, warnings,
        )
    if ext == ".kml":
        geoms, warnings = parse_kml_bytes(data)
        return (
            geoms, "kml",
            "EPSG:4326 (WGS84, KML uses lon/lat)",
            "geodesic (ellipsoidal) measurement, no projection applied",
            None, warnings,
        )
    if ext == ".kmz":
        geoms, warnings = parse_kmz_bytes(data)
        return (
            geoms, "kmz",
            "EPSG:4326 (WGS84, KML uses lon/lat)",
            "unzipped KMZ in memory; geodesic (ellipsoidal) measurement",
            None, warnings,
        )
    if ext == ".gpx":
        geoms, warnings = parse_gpx_bytes(data)
        return (
            geoms, "gpx",
            "EPSG:4326 (WGS84, GPX is lon/lat by definition)",
            "geodesic (ellipsoidal) measurement along track path",
            None, warnings,
        )
    raise ParseError(f"unsupported file type '{ext}'")
