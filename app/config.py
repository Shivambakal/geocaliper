"""Knobs you might actually want to change."""

# max upload size — 25 MB is plenty for geojson/dxf, keeps memory sane
MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024

# files bigger than this get auto-routed to async jobs on the sync endpoint
# (pass ?sync=true to force synchronous processing anyway)
ASYNC_SUGGEST_BYTES = 5 * 1024 * 1024

# batch endpoint cap — keeps one request from hogging the worker
MAX_BATCH_FILES = 10

# how much of an uploaded file we read at a time
CHUNK_SIZE = 1024 * 1024

API_TITLE = "Geospatial File Measurement API"
API_VERSION = "2.0.0"
