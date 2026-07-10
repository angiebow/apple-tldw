# ViReel Cloud API (async, storage-backed)

The App Store-ready rewrite of the local `server.py`. Clients never send local
file paths; they upload media to object storage, enqueue a job, poll it, then
download results. Contract: [`openapi.yaml`](./openapi.yaml).

```
client ──1. POST /uploads──────────────▶ { media_key, upload_url }
       ──2. PUT upload_url (bytes)─────▶ object storage
       ──3. POST /jobs {type,media_key}▶ { job_id }
       ──4. GET /jobs/{id} (poll)──────▶ { status, progress }
       ──5. GET /jobs/{id}/result──────▶ { data, outputs:[{download_url}] }
```

## Layout
| File | Role |
|------|------|
| `app.py` | FastAPI app: uploads, jobs, results (+ dev `/storage` routes) |
| `storage.py` | Object-storage abstraction; `LocalStorage` (dev), S3 slots in for prod |
| `jobs.py` | SQLite job store |
| `worker.py` | In-process queue + thread pool; handler registry; mock handler |
| `pipeline_handlers.py` | Real ML handlers (transcribe/clip/merge), registered at import |
| `config.py` | Env-overridable settings |

Prod swaps three things behind the same interfaces: `LocalStorage`→S3, SQLite→Postgres,
in-process queue→Redis/Celery on GPU workers. The API and handlers don't change.

## Run locally (dev)
From `backend/`:
```bash
TLDW_PUBLIC_BASE_URL=http://127.0.0.1:8100 \
  ./venv/bin/uvicorn cloud.app:app --port 8100
```
Without the ML deps/PoC modules importable, the worker falls back to the **mock
handler**, so the whole upload→job→poll→download flow is testable with no models.

## Smoke test
See `smoke_test.sh` — mints an upload, PUTs a file, creates a job, polls to done,
downloads the output.
