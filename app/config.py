"""Knobs you might actually want to change."""

# max upload size — 25 MB is plenty for geojson/dxf, keeps memory sane
MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024

# how much of an uploaded file we read at a time
CHUNK_SIZE = 1024 * 1024

API_TITLE = "Geospatial File Measurement API"
API_VERSION = "1.0.0"
