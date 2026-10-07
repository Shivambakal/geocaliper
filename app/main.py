"""App entrypoint: middleware, health check, demo page, router wiring."""

import logging
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from app import config
from app.routers.jobs import router as jobs_router
from app.routers.measurements import router as measurements_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("geomeasure")

app = FastAPI(
    title=config.API_TITLE,
    version=config.API_VERSION,
    description=(
        "Upload geospatial files (GeoJSON, DXF, KML/KMZ, GPX) and get real-world "
        "measurements (length, area, perimeter). Geographic formats are measured "
        "geodesically on WGS84; DXF is measured in CAD units converted via $INSUNITS. "
        "Batch uploads, CSV export and async jobs included."
    ),
)


@app.middleware("http")
async def request_id_and_timing(request: Request, call_next):
    request_id = str(uuid.uuid4())[:8]
    start = time.time()
    response = await call_next(request)
    elapsed_ms = (time.time() - start) * 1000
    response.headers["X-Request-ID"] = request_id
    log.info("%s %s -> %s (%.1f ms) [req=%s]",
             request.method, request.url.path, response.status_code, elapsed_ms, request_id)
    return response


@app.get("/health", summary="Health check")
def health():
    return {"status": "ok", "version": config.API_VERSION}


DEMO_PAGE = Path(__file__).parent / "static" / "demo.html"


@app.get("/demo", include_in_schema=False, summary="Browser demo page")
def demo():
    return FileResponse(DEMO_PAGE, media_type="text/html")


@app.get("/", include_in_schema=False)
def index():
    return JSONResponse({
        "service": config.API_TITLE,
        "version": config.API_VERSION,
        "docs": "/docs",
        "demo": "/demo",
        "health": "/health",
    })


app.include_router(measurements_router)
app.include_router(jobs_router)
