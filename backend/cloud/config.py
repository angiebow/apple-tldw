"""Cloud API configuration — all overridable by environment variable.

Dev defaults keep everything on the local filesystem so the whole flow runs
without cloud credentials. Prod swaps STORAGE_BACKEND=s3 and points
PUBLIC_BASE_URL at the deployed host.
"""
from __future__ import annotations

import os

# Where the local-fs storage backend and the SQLite jobs DB live.
DATA_DIR = os.environ.get(
    "TLDW_CLOUD_DATA",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".data"),
)

# Public base URL used to build presigned-style URLs handed to clients. In dev
# these point back at this same server's /storage routes.
PUBLIC_BASE_URL = os.environ.get("TLDW_PUBLIC_BASE_URL", "http://127.0.0.1:8000")

# "local" (filesystem, dev) or "s3" (real object storage, prod — not yet wired).
STORAGE_BACKEND = os.environ.get("TLDW_STORAGE_BACKEND", "local")

# Lifetime of a presigned URL, seconds.
PRESIGN_TTL = int(os.environ.get("TLDW_PRESIGN_TTL", "900"))

# Number of worker threads draining the job queue (dev: in-process). Prod would
# run separate GPU worker processes off a shared queue instead.
WORKER_CONCURRENCY = int(os.environ.get("TLDW_WORKER_CONCURRENCY", "1"))

# Max upload size accepted by the dev /storage PUT route (bytes). 0 = unlimited.
MAX_UPLOAD_BYTES = int(os.environ.get("TLDW_MAX_UPLOAD_BYTES", str(2 * 1024 ** 3)))

# When set, every request must carry `Authorization: Bearer <token>`. Unset in
# dev so curl works without a token; set in prod (per-user tokens live in a DB).
API_TOKEN = os.environ.get("TLDW_API_TOKEN", "")
