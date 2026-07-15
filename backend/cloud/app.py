"""FastAPI app implementing the cloud contract (see openapi.yaml).

Dev-runnable with zero cloud credentials: the /storage routes stand in for
S3/GCS/R2 so the presigned PUT/GET flow works locally. Run:

    ./venv/bin/uvicorn cloud.app:app --reload      # from backend/
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.requests import ClientDisconnect

from . import config
from .jobs import JOB_TYPES, JobStore
from .storage import build_storage, new_media_key

# Importing this registers any available real ML handlers; it is intentionally
# tolerant so the scaffold still boots (mock handlers) when the ML deps or PoC
# modules aren't importable in this environment.
try:  # pragma: no cover - best effort
    from . import pipeline_handlers  # noqa: F401
except Exception as exc:  # noqa: BLE001
    print(f"[cloud] pipeline handlers unavailable, using mock worker: {exc}")

from .worker import Worker


# ── Request/response models ────────────────────────────────────────────────
class UploadRequest(BaseModel):
    filename: str
    content_type: Optional[str] = None
    size_bytes: Optional[int] = None


class UploadResponse(BaseModel):
    media_key: str
    upload_url: str
    method: str = "PUT"
    expires_in: int


class JobCreate(BaseModel):
    type: str
    media_key: str
    params: Dict[str, Any] = {}


class JobView(BaseModel):
    id: str
    type: str
    status: str
    progress: float
    stage: Optional[str] = None
    error: Optional[str] = None
    created_at: str
    updated_at: str


def _job_view(job: Dict[str, Any]) -> JobView:
    return JobView(
        id=job["id"], type=job["type"], status=job["status"],
        progress=job["progress"], stage=job.get("stage"), error=job.get("error"),
        created_at=job["created_at"], updated_at=job["updated_at"],
    )


# ── App wiring ─────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.storage = build_storage()
    app.state.store = JobStore()
    app.state.worker = Worker(app.state.store, app.state.storage)
    app.state.worker.start()
    yield
    app.state.worker.stop()


app = FastAPI(title="tldw / ViReel Cloud API", version="0.1.0", lifespan=lifespan)


def require_auth(authorization: Optional[str] = Header(None)) -> None:
    """Enforce a bearer token only when TLDW_API_TOKEN is configured (prod)."""
    if not config.API_TOKEN:
        return
    expected = f"Bearer {config.API_TOKEN}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Missing or invalid bearer token.")


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "storage": config.STORAGE_BACKEND,
        "light_mode": config.LIGHT_MODE,
        "features": config.FEATURES,
    }


@app.post("/uploads", response_model=UploadResponse)
def create_upload(req: UploadRequest, _: None = Depends(require_auth)) -> UploadResponse:
    key = new_media_key(req.filename)
    url, ttl = app.state.storage.presign_put(key)
    return UploadResponse(media_key=key, upload_url=url, expires_in=ttl)


@app.post("/jobs", status_code=202, response_model=JobView)
def create_job(req: JobCreate, _: None = Depends(require_auth)) -> JobView:
    if req.type not in JOB_TYPES:
        raise HTTPException(status_code=400,
                            detail=f"Unknown job type {req.type!r}; expected one of {JOB_TYPES}.")
    if req.type in config.DISABLED_JOB_TYPES:
        raise HTTPException(status_code=503,
                            detail=f"'{req.type}' is disabled on this tier (needs GPU hosting).")
    if not app.state.storage.exists(req.media_key):
        raise HTTPException(status_code=400,
                            detail=f"media_key not found in storage: {req.media_key} "
                                   "(did the client PUT the upload first?)")
    job = app.state.store.create(req.type, req.media_key, req.params)
    app.state.worker.enqueue(job["id"])
    return _job_view(job)


@app.get("/jobs/{job_id}", response_model=JobView)
def get_job(job_id: str, _: None = Depends(require_auth)) -> JobView:
    job = app.state.store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="No such job.")
    return _job_view(job)


@app.get("/jobs/{job_id}/result")
def get_job_result(job_id: str, _: None = Depends(require_auth)) -> Dict[str, Any]:
    job = app.state.store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="No such job.")
    if job["status"] != "done":
        raise HTTPException(status_code=409,
                            detail=f"Job not finished (status={job['status']}).")
    result = job.get("result") or {}
    outputs: List[Dict[str, Any]] = []
    for out in result.get("outputs", []):
        url, _ttl = app.state.storage.presign_get(out["key"])
        outputs.append({**out, "download_url": url})
    return {
        "id": job["id"], "type": job["type"], "status": job["status"],
        "data": result.get("data"), "outputs": outputs,
    }


# ── Synchronous text-only endpoints (delegate to server.py) ────────────────
@app.post("/highlight")
def highlight(payload: Dict[str, Any] = Body(...), _: None = Depends(require_auth)) -> Dict[str, Any]:
    from .sync_handlers import run_highlight
    return run_highlight(payload)


@app.post("/backsound")
def backsound(payload: Dict[str, Any] = Body(...), _: None = Depends(require_auth)) -> Dict[str, Any]:
    if not config.FEATURES["music"]:
        raise HTTPException(status_code=503,
                            detail="AI music is disabled on this tier (needs GPU hosting).")
    from .sync_handlers import run_backsound
    return run_backsound(payload)


# ── Dev-only storage routes (stand in for S3/GCS/R2 presigned PUT/GET) ──────
@app.put("/storage/{key:path}")
async def storage_put(key: str, request: Request,
                      _: None = Depends(require_auth)) -> Dict[str, Any]:
    try:
        body = await request.body()
    except ClientDisconnect:
        # Common when a large upload is cut off — e.g. Cloudflare free tunnels
        # cap request bodies at ~100MB. Return a clear 4xx, not a 500 traceback.
        raise HTTPException(
            status_code=413,
            detail="Upload interrupted — the file may be too large (the tunnel "
                   "caps uploads near 100MB). Try a shorter video.")
    if config.MAX_UPLOAD_BYTES and len(body) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Upload exceeds size limit.")
    try:
        app.state.storage.write_bytes(key, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"key": key, "bytes": len(body)}


@app.get("/storage/{key:path}")
def storage_get(key: str, _: None = Depends(require_auth)) -> FileResponse:
    try:
        path = app.state.storage.abs_path(key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not app.state.storage.exists(key):
        raise HTTPException(status_code=404, detail="No such object.")
    return FileResponse(path)
