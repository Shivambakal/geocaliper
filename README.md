# Geospatial File Measurement API

A small REST API that takes geospatial files (GeoJSON, DXF, KML/KMZ, GPX) and returns real-world measurements — length, area, perimeter. Built with FastAPI.

I picked FastAPI because the whole spec is basically "parse file, do math, return JSON" and FastAPI gives you validation, docs, and error handling without boilerplate. The interactive docs at `/docs` are handy for the reviewer too.

## Setup

Needs Python 3.11+.

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Server runs on http://localhost:8000. Docs at `/docs`, a small demo page at `/demo`.

Or with Docker:

```bash
docker build -t geomeasure .
docker run -p 8000:8000 geomeasure
```

Run the tests:

```bash
pytest tests/ -q
```

68 tests, all passing. They cover the measurement math, every parser (GeoJSON, DXF, KML/KMZ, GPX), and every endpoint including the error cases.

## API

### POST /api/v1/measurements

Upload a file. Works with `.geojson`, `.json`, `.dxf`, `.kml`, `.kmz`, and `.gpx`.

```bash
curl -X POST http://localhost:8000/api/v1/measurements \
  -F "file=@samples/valid_polygon.geojson"
```

Response:

```json
{
  "measurement_id": "0ea15e9c-8997-42f9-a16d-024b4684476c",
  "filename": "valid_polygon.geojson",
  "uploaded_at": "2026-10-07T06:36:42.735080Z",
  "status": "completed",
  "file_metadata": {
    "size_bytes": 246,
    "geometry_count": 1,
    "format": "geojson"
  },
  "result": {
    "geometry_type": "Polygon",
    "measurements": {
      "area_m2": 10518.611,
      "perimeter_m": 410.415
    },
    "units": "meters",
    "detected_crs": "EPSG:4326 (WGS84, per RFC 7946)",
    "conversion_details": "geodesic (ellipsoidal) measurement, no projection applied"
  },
  "errors": []
}
```

Bad files get a 422 with a plain-English message, never a 500. Wrong extension, empty file, garbage content, and files over 25 MB are all handled.

Files over 5 MB are automatically processed as async jobs instead — you get a 202 back with a `job_id` and a `poll` URL rather than a hanging request. Pass `?sync=true` if you'd rather wait for the synchronous result anyway.

### POST /api/v1/measurements/batch

Up to 10 files in one request. You get per-file results plus a summary with totals.

```bash
curl -X POST http://localhost:8000/api/v1/measurements/batch \
  -F "files=@samples/valid_polygon.geojson" \
  -F "files=@samples/valid_route.gpx"
```

A file that fails doesn't kill the batch — it shows up with `"ok": false` and an error message while the rest succeed. The summary aggregates `total_area_m2`, `total_length_m`, and `total_points` across the successful files.

### GET /api/v1/measurements/{measurement_id}/export?format=csv

Downloads the measurement as a CSV (one row per metric) or as JSON — both as file attachments, handy for pulling results into a spreadsheet or a report.

```bash
curl "http://localhost:8000/api/v1/measurements/<id>/export?format=csv" -o result.csv
```

### POST /api/v1/jobs

For when you don't want to wait: upload a file, get a `job_id` back immediately (202), then poll until it's done.

```bash
curl -X POST http://localhost:8000/api/v1/jobs -F "file=@samples/valid_track.kml"
# {"job_id": "...", "status": "queued", "poll": "/api/v1/jobs/...", ...}

curl http://localhost:8000/api/v1/jobs/<job_id>
# {"status": "done", "result": {...}, "measurement_id": "..."}
# or {"status": "failed", "error": "..."} if the file was bad
```

### GET /api/v1/jobs/{job_id}

Job status: `queued`, `processing`, `done`, or `failed`. The result, when done, is the same shape as the sync endpoint — and the measurement is also retrievable via `GET /api/v1/measurements/{measurement_id}` as usual.

### POST /api/v1/measurements/geojson

Same thing but you POST raw GeoJSON in the body instead of uploading a file.

```bash
curl -X POST http://localhost:8000/api/v1/measurements/geojson \
  -H "Content-Type: application/json" \
  -d '{"type": "LineString", "coordinates": [[73.8567, 18.5204], [73.8577, 18.5204]]}'
```

### GET /api/v1/measurements/{measurement_id}

Fetches a previously computed measurement. Unknown id returns 404.

### GET /health

Simple health check, returns `{"status": "ok"}`.

### GET /demo

A plain page where you can drag-drop a file or paste GeoJSON and see the measurements. It also does batch uploads, CSV/JSON downloads, and can submit background jobs with live polling. Nothing fancy, just useful for trying it out.

## Architecture

```
app/
  main.py              # app setup, request-id middleware, /health, /demo
  config.py            # file size limit and other knobs
  models/schemas.py    # request/response shapes
  store.py             # in-memory result + job store
  routers/
    measurements.py    # upload, batch, export, raw geojson, fetch
    jobs.py            # async job submit + poll
  services/
    parsers.py         # GeoJSON / DXF / KML / KMZ / GPX -> shapely geometries
    measure.py         # the actual measurement math
  static/demo.html     # demo page (upload, batch, export, job polling)
samples/               # geojson, dxf, kml, kmz, gpx samples + a malformed file
tests/                 # pytest suite
```

The flow for an upload:

1. Router checks the extension and reads the file in 1 MB chunks (so a large upload doesn't spike memory), enforcing the 25 MB limit while streaming.
2. Parser turns the file into shapely geometries. GeoJSON goes through `json` + shapely. DXF goes through ezdxf — I pull LINE, LWPOLYLINE, POLYLINE, CIRCLE, ARC, POINT, SPLINE (flattened), ELLIPSE (flattened), and HATCH boundaries out of modelspace and convert them to shapely equivalents. KML/KMZ and GPX are parsed with the stdlib (`xml.etree` + `zipfile`) — no extra dependencies — since both are WGS84 lon/lat by definition.
3. `measure.py` computes the numbers. Geographic formats are measured geodesically on the WGS84 ellipsoid. DXF is measured planar and scaled to meters using the `$INSUNITS` header.
4. The response is saved in the in-memory store under a UUID, and returned.

## CRS handling

This was the part I thought about most. GeoJSON coordinates are lat/lon degrees, so doing `sqrt(dx^2 + dy^2)` on them gives nonsense. I used geographiclib's geodesic routines — distances come from the inverse geodesic problem and areas from the polygon area algorithm, both directly on the WGS84 ellipsoid. No projection needed, no distortion.

DXF is the opposite problem: the coordinates are in CAD drawing units with no geographic meaning. ezdxf reads the `$INSUNITS` header which tells you what the units are (6 = meters, 2 = feet, 4 = mm, etc.), and I scale everything to meters. If the header says 0 (unitless) I assume meters and say so in the response — that's a guess, but it's the common case and it's stated openly in `conversion_details` rather than hidden.

## Design decisions

**Geodesic instead of projecting to UTM.** I could have projected everything into a local UTM zone and done flat math. That works but adds a zone-selection step that can silently go wrong near zone boundaries. Geodesic math is exact everywhere and the code is simpler. The tradeoff is speed — geographiclib is slower than planar math — but for file-upload sizes this doesn't matter.

**In-memory result store.** The spec says the API must be stateless, and strictly speaking a dict in memory isn't stateless across restarts or multiple workers. I kept it because for an assignment a database is overkill, and I documented it here instead of pretending. If this were real, I'd swap `store.py` for Redis — the interface is two functions, so it's a small change.

**422s with messages, never 500s.** Every parse failure is caught and returned as a 422 with a human-readable message. The `errors` array in the response also carries per-feature warnings (like a null geometry that got skipped) without failing the whole request.

**Chunked upload reading.** Files are read in 1 MB chunks with the size check during streaming, so a 200 MB upload gets rejected before it's fully in memory.

What I'd do next: CRS detection for GeoJSON files that aren't WGS84, Shapefile/GeoTIFF support (needs GDAL, which is why I skipped it for now), and Redis for the store, as mentioned. API keys + rate limiting if this ever faces the public internet.

## What I learned

I hadn't worked with DXF before this. The format is old and quirky — the `$INSUNITS` header is the only thing connecting a drawing to the real world, and plenty of files leave it at 0. I also hadn't used geodesic math directly; it was good to see how far off naive degree-based math is (about 5% error on area at these latitudes, worse further from the equator). The ezdxf library did most of the heavy lifting on parsing.

## Samples

- `samples/valid_polygon.geojson` — a ~100m x 100m plot in Kothrud, Pune. Measures about 10,519 m².
- `samples/valid_polyline.dxf` — an L-shaped polyline (70 m) and a closed 10x20 m rectangle, drawn in meters.
- `samples/valid_shapes.dxf` — a SPLINE curve (~34 m), an ELLIPSE (~157 m²), and a HATCHed 20x20 m rectangle (400 m²).
- `samples/valid_track.kml` — a walk in Kothrud: point, path, park plot, and a multigeometry placemark.
- `samples/valid_track.kmz` — the same KML zipped, for the KMZ path.
- `samples/valid_route.gpx` — a commute route, an evening ride (two track segments), and two waypoints.
- `samples/malformed_example.txt` — garbage, for testing the 422 path.
